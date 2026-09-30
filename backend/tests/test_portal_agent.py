"""Portal agent against a local fake portal, driven by real headless Chromium."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.generation.runs import queue_portal, run_next_portal
from app.llm.gateway import LLMGateway
from app.models import (
    Application,
    Job,
    JobFile,
    LLMProvider,
    LLMTaskConfig,
    PipelineRun,
    Portal,
    PortalSkill,
    Resume,
    Setting,
)
from app.models.enums import (
    ApplicationStatus,
    JobSource,
    JobStatus,
    LLMProviderKind,
    LLMTask,
    ResumeKind,
    RunKind,
    RunState,
)
from app.portal.agent import PROMPT_KEY, HumanNeeded, PortalAgent, PortalError, profile_value
from app.schemas.settings import Profile
from app.services.settings_service import set_profile
from tests.fakes import FakeBackend, make_pdf

playwright = pytest.importorskip("playwright.sync_api")

JD_PDF = make_pdf(1, body="Eligibility: CGPA 7.5 and above. Bond: none.")


class FakePortal(BaseHTTPRequestHandler):
    otp_required = False
    logins: list[str] = []
    submissions: list[bytes] = []

    def log_message(self, *args: Any) -> None:  # quiet
        pass

    def _send(
        self,
        body: str | bytes,
        status: int = 200,
        ctype: str = "text/html",
        headers: dict[str, str] | None = None,
    ) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _authed(self) -> bool:
        return "session=ok" in (self.headers.get("Cookie") or "")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/login":
            self._send(
                '<form method="post" action="/login"><input type="email" name="username">'
                '<input type="password" name="password"><button type="submit">Login</button></form>'
            )
        elif path == "/otp":
            self._send(
                '<p>Enter the One-Time Password</p><form method="post" action="/otp">'
                '<input name="otp" autocomplete="one-time-code"><button type="submit">Verify</button></form>'
            )
        elif path == "/jobs/42":
            if not self._authed():
                self._send("", 302, headers={"Location": "/login?next=/jobs/42"})
                return
            self._send(
                '<main><h1 class="title">SDE Intern</h1><div class="company">Acme Corp</div>'
                '<div class="jd">We need Python, FastAPI and PostgreSQL. Deadline 30 Sep 2026.</div>'
                '<a href="/files/jd.pdf">Full JD</a><a href="https://evil.example/x.pdf">mirror</a>'
                '<button id="apply" onclick="location.href=\'/jobs/42/apply\'">Apply</button></main>'
            )
        elif path == "/jobs/42/apply":
            self._send(
                '<form method="post" action="/jobs/42/apply" enctype="multipart/form-data">'
                '<input type="file" name="resume"><input id="phone" name="phone">'
                '<input type="checkbox" id="decl" name="decl"><button id="submit" type="submit">Submit</button></form>'
            )
        elif path == "/jobs/77":  # a portal that gates everything behind reCAPTCHA
            self._send("", 302, headers={"Location": "/captcha"})
        elif path == "/captcha":
            self._send('<p>Verify you are human</p><div class="g-recaptcha"></div>')
        elif path == "/files/jd.pdf":
            self._send(JD_PDF, ctype="application/pdf")
        elif path == "/leave":
            self._send("", 302, headers={"Location": "http://localhost.evil.invalid/phish"})
        else:
            self._send("not found", 404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        path = urlparse(self.path).path
        if path == "/login":
            form = parse_qs(body.decode())
            FakePortal.logins.append(form.get("username", [""])[0])
            if form.get("password", [""])[0] != "portal-pass":
                self._send("<p>bad credentials</p><input type='password'>")
            elif FakePortal.otp_required:
                self._send("", 303, headers={"Location": "/otp"})
            else:
                self._send(
                    "", 303, headers={"Location": "/jobs/42", "Set-Cookie": "session=ok; Path=/"}
                )
        elif path == "/otp":
            ok = parse_qs(body.decode()).get("otp", [""])[0] == "123456"
            self._send(
                "",
                303,
                headers={
                    "Location": "/jobs/42" if ok else "/otp",
                    **({"Set-Cookie": "session=ok; Path=/"} if ok else {}),
                },
            )
        elif path == "/jobs/42/apply":
            FakePortal.submissions.append(body)
            self._send("<h2>Application submitted successfully</h2>")


@pytest.fixture
def portal_server() -> Iterator[str]:
    FakePortal.otp_required, FakePortal.logins, FakePortal.submissions = False, [], []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakePortal)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    (tmp_path / "fakeportal.yaml").write_text("""
job:
  title: "h1.title"
  company: ".company"
  description: ".jd"
apply:
  open: "#apply"
  resume_input: "input[type=file]"
  fields:
    "#phone": phone
  checkboxes: ["#decl"]
  submit: "#submit"
  confirmation: "text=/submitted successfully/i"
""")
    return tmp_path


@pytest.fixture
def env(
    db: Session,
    engine: Engine,
    gateway: LLMGateway,
    portal_server: str,
    config_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    monkeypatch.setattr("app.portal.agent.CHALLENGE_WAIT_MS", 300)
    p = LLMProvider(label="T", kind=LLMProviderKind.OPENAI, api_key="sk-test-key-000000")
    db.add(p)
    db.flush()
    db.add(LLMTaskConfig(task=LLMTask.PORTAL_HELPER, provider_id=p.id, model="portal", params={}))
    portal = Portal(
        name="FakePortal",
        base_url=portal_server,
        allowed_domains=["127.0.0.1"],
        credentials={"username": "abhi@college.edu", "password": "portal-pass"},
    )
    db.add(portal)
    db.flush()
    job = Job(
        source=JobSource.EMAIL,
        status=JobStatus.DETECTED,
        portal_id=portal.id,
        apply_url=f"{portal_server}/jobs/42",
    )
    db.add(job)
    db.commit()
    import os
    import shutil

    shutil.rmtree(Path(os.environ["DATA_DIR"]) / "portal_sessions", ignore_errors=True)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    agent = PortalAgent(maker, gateway, prompt_timeout_s=8, config_root=config_root)
    return {"job": job, "portal": portal, "agent": agent, "maker": maker, "base": portal_server}


def portal_json() -> str:
    return json.dumps(
        {
            "company": "Acme Corp (LLM)",
            "role": None,
            "ctc": "12 LPA",
            "location": "Bengaluru",
            "deadline": "2026-09-30T23:59",
            "eligibility": {"min_cgpa": 7.5},
            "required_documents": ["Resume"],
        }
    )


def test_scrape_logs_in_reads_jd_and_downloads_allowed_attachments(
    env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    backend.script("portal", portal_json())
    res = env["agent"].scrape(env["job"].id, merge_email=False)
    assert FakePortal.logins == ["abhi@college.edu"]
    assert "FastAPI" in res.jd_text and res.attachments == 1  # evil.example mirror skipped
    db.expire_all()
    job = db.get(Job, env["job"].id)
    assert job is not None and job.status is JobStatus.SCRAPED
    assert job.company == "Acme Corp" and job.role == "SDE Intern"  # selectors beat the LLM
    assert job.ctc == "12 LPA" and job.deadline is not None and job.required_documents == ["Resume"]
    assert "CGPA 7.5" in (job.jd_text or "")  # attachment text merged into the JD
    kinds = sorted(f.kind.value for f in db.query(JobFile).filter_by(job_id=job.id))
    assert kinds == ["jd_attachment", "screenshot"]

    session_file = (
        Path(__import__("os").environ["DATA_DIR"]) / "portal_sessions" / f"{env['portal'].id}.enc"
    )
    assert session_file.exists() and "session" not in session_file.read_text()  # encrypted cookies

    backend.script("portal", portal_json())
    env["agent"].scrape(env["job"].id, merge_email=False)
    assert FakePortal.logins == ["abhi@college.edu"]  # saved session reused: no second login


def test_otp_is_relayed_from_the_user_never_bypassed(
    env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    FakePortal.otp_required = True
    backend.script("portal", portal_json())

    def user_answers() -> None:
        for _ in range(60):
            with env["maker"]() as s:
                row = s.get(Setting, PROMPT_KEY.format(job_id=env["job"].id))
                if row and row.secret_value:
                    data = json.loads(row.secret_value)
                    assert data["kind"] == "otp" and data["screenshot_file_id"]
                    data["answer"] = "123456"
                    row.secret_value = json.dumps(data)
                    s.commit()
                    return
            time.sleep(0.1)

    t = threading.Thread(target=user_answers)
    t.start()
    env["agent"].scrape(env["job"].id, merge_email=False)
    t.join()
    db.expire_all()
    assert db.get(Job, env["job"].id).status is JobStatus.SCRAPED  # type: ignore[union-attr]


def test_otp_without_answer_pauses(env: dict[str, Any], db: Session) -> None:
    FakePortal.otp_required = True
    env["agent"]._prompt_timeout = 1
    with pytest.raises(HumanNeeded, match="no answer"):
        env["agent"].scrape(env["job"].id, merge_email=False)


def test_leaving_the_allowed_domain_aborts(env: dict[str, Any], db: Session) -> None:
    job = db.get(Job, env["job"].id)
    assert job is not None
    job.apply_url = env["base"] + "/leave"
    db.commit()
    with pytest.raises(PortalError, match="non-allowed address"):
        env["agent"].scrape(job.id, merge_email=False)


def test_recaptcha_gate_asks_the_user_to_sign_in_once(env: dict[str, Any], db: Session) -> None:
    job = db.get(Job, env["job"].id)
    assert job is not None
    job.apply_url = env["base"] + "/jobs/77"
    db.commit()
    with pytest.raises(HumanNeeded, match=r"never solves CAPTCHAs.*portal_login\.py"):
        env["agent"].scrape(job.id, merge_email=False)
    assert FakePortal.logins == []  # no credentials were typed into a CAPTCHA page


def test_session_upload_keeps_only_portal_cookies(
    env: dict[str, Any], client: Any, db: Session
) -> None:
    portal = db.get(Portal, env["portal"].id)
    assert portal is not None
    portal.base_url = "https://127.0.0.1"  # the API only serves https portals
    db.commit()
    pid = portal.id
    state = {
        "cookies": [
            {"name": "session", "value": "ok", "domain": "127.0.0.1", "path": "/"},
            {"name": "NID", "value": "x", "domain": ".google.com", "path": "/"},
        ],
        "origins": [{"origin": "https://www.google.com", "localStorage": []}],
        "session_storage": {
            "https://127.0.0.1": {"token": "t"},
            "https://www.google.com": {"x": "y"},
        },
    }
    r = client.put(f"/api/portals/{pid}/session", json=state)
    assert r.status_code == 200 and r.json()["session_saved_at"]
    from app.portal.agent import _load_session

    saved = _load_session(env["portal"])
    assert saved == {
        "cookies": [state["cookies"][0]],
        "origins": [],
        "session_storage": {"https://127.0.0.1": {"token": "t"}},
    }
    only_google = {"cookies": [state["cookies"][1]], "origins": state["origins"]}
    assert client.put(f"/api/portals/{pid}/session", json=only_google).status_code == 422
    assert client.delete(f"/api/portals/{pid}/session").json()["session_saved_at"] is None


def _approved(db: Session, job: Job) -> Application:
    rpath = Path(__import__("os").environ["DATA_DIR"]) / "resumes" / "apply-test.pdf"
    rpath.parent.mkdir(parents=True, exist_ok=True)
    rpath.write_bytes(make_pdf(1, body="Abhi Ram resume"))
    r = Resume(
        kind=ResumeKind.GENERATED,
        version=1,
        job_id=job.id,
        tex_source="x",
        pdf_path="resumes/apply-test.pdf",
    )
    db.add(r)
    db.flush()
    a = Application(job_id=job.id, resume_id=r.id, status=ApplicationStatus.APPROVED)
    db.add(a)
    db.commit()
    return a


def test_apply_submits_approved_resume_with_confirmation(env: dict[str, Any], db: Session) -> None:
    from app.schemas.settings import AppSettingsPatch
    from app.services.settings_service import update_app_settings

    set_profile(db, Profile(full_name="Abhi Ram", phone="+91 90000 00000"))
    update_app_settings(db, AppSettingsPatch(resume_file_name="Abhiram"))
    job = db.get(Job, env["job"].id)
    assert job is not None
    a = _approved(db, job)
    env["agent"].apply(job.id, a.id)
    assert len(FakePortal.submissions) == 1
    body = FakePortal.submissions[0]
    assert b"%PDF" in body and b"+91 90000 00000" in body and b"decl" in body
    assert b'filename="Abhiram.pdf"' in body  # the company sees your name, not "resume.pdf"
    db.expire_all()
    a2 = db.get(Application, a.id)
    assert a2 is not None and a2.status is ApplicationStatus.SUBMITTED and a2.confirmation_file_id
    assert db.get(Job, job.id).status is JobStatus.APPLIED  # type: ignore[union-attr]
    shots = {f.original_name for f in db.query(JobFile).filter_by(job_id=job.id)}
    assert {"before_submit.png", "confirmation.png"} <= shots
    env["agent"].apply(job.id, a.id)
    assert len(FakePortal.submissions) == 1  # never submits twice


def test_apply_refuses_without_approval_or_profile(env: dict[str, Any], db: Session) -> None:
    job = db.get(Job, env["job"].id)
    assert job is not None
    a = _approved(db, job)
    with pytest.raises(PortalError, match="missing values"):  # profile has no phone
        env["agent"].apply(job.id, a.id)
    a.status = ApplicationStatus.PREPARED
    db.commit()
    with pytest.raises(PortalError, match="isn't approved"):
        env["agent"].apply(job.id, a.id)
    assert FakePortal.submissions == []


def test_apply_learns_the_portal_and_reuses_it_next_time(
    env: dict[str, Any], backend: FakeBackend, db: Session, config_root: Path
) -> None:
    """No apply selectors configured: the agent learns the steps (heuristic arms + a model
    field mapping) on the first application; the second one needs no model help."""
    (config_root / "fakeportal.yaml").write_text("job: {}\n")
    set_profile(db, Profile(full_name="Abhi Ram", phone="+91 90000 00000"))
    backend.script(
        "portal", json.dumps({"mappings": [{"label": "phone", "profile_path": "phone"}]})
    )
    job = db.get(Job, env["job"].id)
    assert job is not None
    env["agent"].apply(job.id, _approved(db, job).id)
    assert len(FakePortal.submissions) == 1 and b"+91 90000 00000" in FakePortal.submissions[0]
    first_calls = len([c for c in backend.calls if c[0].model == "portal"])
    assert first_calls == 1  # only the field mapping needed the model

    db.expire_all()
    skills = {(k.role, k.selector): k for k in db.query(PortalSkill).all()}
    assert skills[("field", "#phone")].value_path == "phone"
    # whichever resume-input arm Thompson sampling tried first got step + episode rewards
    assert max(k.successes for (role, _), k in skills.items() if role == "apply.resume_input") >= 2

    job2 = Job(
        source=JobSource.EMAIL,
        status=JobStatus.DETECTED,
        portal_id=job.portal_id,
        apply_url=job.apply_url,
    )
    db.add(job2)
    db.commit()
    env["agent"].apply(job2.id, _approved(db, job2).id)
    assert len(FakePortal.submissions) == 2
    assert len([c for c in backend.calls if c[0].model == "portal"]) == first_calls  # learned


def test_submit_guard_never_clicks_draft_or_withdraw() -> None:
    from app.portal.agent import _is_submit_button

    class Loc:
        def __init__(self, info: dict[str, str]) -> None:
            self.info = info

        def evaluate(self, _js: str) -> dict[str, str]:
            return self.info

    ok = {"tag": "BUTTON", "type": "submit", "role": "", "text": "Submit application"}
    assert _is_submit_button(Loc(ok))
    assert not _is_submit_button(Loc(ok | {"text": "Save as draft"}))
    assert not _is_submit_button(Loc(ok | {"text": "Withdraw"}))
    assert not _is_submit_button(Loc({"tag": "A", "type": "", "role": "", "text": "Submit"}))


def test_runner_scrape_then_queues_generation(
    env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    backend.script("portal", portal_json())
    queue_portal(db, env["job"], then_generate=True, merge_email=False)
    run = run_next_portal(env["agent"], env["maker"])
    assert run is not None and run.state is RunState.DONE
    kinds = [
        r.kind
        for r in db.query(PipelineRun).filter_by(job_id=env["job"].id).order_by(PipelineRun.id)
    ]
    assert kinds == [RunKind.SCRAPE, RunKind.GENERATE]


def test_runner_marks_paused_when_human_needed(env: dict[str, Any], db: Session) -> None:
    portal = db.get(Portal, env["portal"].id)
    assert portal is not None
    portal.credentials = None
    db.commit()
    queue_portal(db, env["job"], then_generate=True, merge_email=False)
    run = run_next_portal(env["agent"], env["maker"])
    assert run is not None and run.state is RunState.FAILED
    db.expire_all()
    job = db.get(Job, env["job"].id)
    assert (
        job is not None
        and job.status is JobStatus.PAUSED
        and "needs a login" in (job.status_reason or "")
    )


def test_profile_value_paths() -> None:
    p = {"phone": "1", "education": [{"cgpa": 8.1}], "form_fields": {"roll": "CB.EN"}}
    assert (
        profile_value(p, "education.0.cgpa") == "8.1"
        and profile_value(p, "form_fields.roll") == "CB.EN"
    )
    assert profile_value(p, "education.1.cgpa") is None and profile_value(p, "email") is None
