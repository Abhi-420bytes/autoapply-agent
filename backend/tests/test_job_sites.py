"""Job websites: stage 1 (searching) with real headless Chromium, stage 2 hand-off, API."""

from __future__ import annotations

import json
import os
import shutil
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.email.ingest import process_due_jobs
from app.generation.runs import after_generation
from app.llm.gateway import LLMGateway
from app.models import (
    DiscoveredPosting,
    Job,
    JobSite,
    LLMProvider,
    LLMTaskConfig,
    Notification,
    Portal,
    Resume,
)
from app.models.enums import JobSource, JobStatus, LLMProviderKind, LLMTask, ResumeKind, SiteMode
from app.portal.agent import PortalAgent
from app.sites import search as search_mod
from tests.fakes import FakeBackend

pytest.importorskip("playwright.sync_api")

POSTINGS = [
    (101, "Data Engineer Intern", "Acme"),
    (102, "Senior Data Engineer", "Globex"),
    (103, "Frontend Developer", "Initech"),
    (104, "ML Engineer Intern", "Umbrella"),
]


class Board(BaseHTTPRequestHandler):
    def log_message(self, *a: Any) -> None:
        pass

    def _send(self, body: str, status: int = 200) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/search"):
            cards = "".join(
                f'<li><a class="job" href="/jobs/{i}">{t}</a> <span>{c} · Bengaluru</span></li>'
                for i, t, c in POSTINGS
            )
            self._send(
                f'<nav><a href="/about">About</a><a href="/login">Login</a></nav><ul>{cards}</ul>'
                '<a href="https://evil.example/jobs/999">Sponsored</a>'
            )
        else:
            self._send("<h1>Job</h1><p>Python, SQL, pipelines</p>")


@pytest.fixture
def board() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Board)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(autouse=True)
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(search_mod, "polite_pause", lambda: None)
    shutil.rmtree(Path(os.environ["DATA_DIR"]) / "portal_sessions", ignore_errors=True)


def make_env(
    db: Session,
    engine: Engine,
    gateway: LLMGateway,
    base: str,
    tmp: Path,
    selector: bool,
    **site_kw: Any,
) -> dict[str, Any]:
    p = LLMProvider(label="T", kind=LLMProviderKind.OPENAI, api_key="sk-test-key-000000")
    db.add(p)
    db.flush()
    db.add(LLMTaskConfig(task=LLMTask.PORTAL_HELPER, provider_id=p.id, model="portal", params={}))
    portal = Portal(name="Board", base_url=base, allowed_domains=["127.0.0.1"])
    db.add(portal)
    db.flush()
    (tmp / "board.yaml").write_text(
        'search:\n  job_link: "a.job"\n' if selector else "search: {}\n"
    )
    site = JobSite(
        name="Board",
        portal_id=portal.id,
        search_urls=[f"{base}/search?q=data"],
        categories=["Data Engineer", "Machine Learning Intern"],
        exclude_keywords=["senior"],
        **({"apply_delay_minutes": 30} | site_kw),
    )
    db.add(site)
    db.commit()
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    return {"site": site, "agent": PortalAgent(maker, gateway, config_root=tmp), "maker": maker}


def category_json() -> str:
    return json.dumps(
        {
            "verdicts": [
                {"index": 3, "category": "Machine Learning Intern"},
                {"index": 2, "category": None},
            ]
        }
    )


def test_search_filters_by_category_and_creates_jobs(
    db: Session,
    engine: Engine,
    gateway: LLMGateway,
    backend: FakeBackend,
    board: str,
    tmp_path: Path,
) -> None:
    env = make_env(db, engine, gateway, board, tmp_path, selector=True)
    backend.script("portal", category_json())
    report = env["agent"].discover(env["site"].id)
    assert (report.postings, report.new, report.matched, report.jobs_created, report.skipped) == (
        4,
        4,
        2,
        2,
        2,
    )
    rows = {r.title: r for r in db.query(DiscoveredPosting)}
    assert (
        rows["Data Engineer Intern"].matched_category == "Data Engineer"
    )  # literal match, no model
    assert (
        rows["ML Engineer Intern"].matched_category == "Machine Learning Intern"
    )  # model near-match
    assert "excluded word 'senior'" in (rows["Senior Data Engineer"].skip_reason or "")
    assert rows["Frontend Developer"].skip_reason == "not in your categories"
    assert all("evil.example" not in r.url for r in rows.values())  # off-domain link ignored
    jobs = {j.role: j for j in db.query(Job)}
    assert set(jobs) == {"Data Engineer Intern", "ML Engineer Intern"}
    j = jobs["Data Engineer Intern"]
    assert (
        j.source is JobSource.SITE
        and j.job_site_id == env["site"].id
        and j.apply_url.endswith("/jobs/101")
    )
    # prepared right away; the delay is now the review window before applying
    assert j.scheduled_at is not None
    assert j.scheduled_at.replace(tzinfo=UTC) <= datetime.now(UTC)
    db.expire_all()
    assert db.get(JobSite, env["site"].id).last_checked_at is not None  # type: ignore[union-attr]

    backend.script("portal", category_json())
    again = env["agent"].discover(env["site"].id)
    assert (
        again.new == 0 and again.jobs_created == 0 and db.query(Job).count() == 2
    )  # no duplicates


def test_model_picks_postings_when_no_selector(
    db: Session,
    engine: Engine,
    gateway: LLMGateway,
    backend: FakeBackend,
    board: str,
    tmp_path: Path,
) -> None:
    env = make_env(db, engine, gateway, board, tmp_path, selector=False, max_new_per_check=1)
    backend.script(
        "portal",
        json.dumps(
            {
                "postings": [
                    {"index": 0, "title": "Data Engineer Intern", "company": "Acme"},
                    {"index": 3, "title": "ML Engineer Intern", "company": "Umbrella"},
                    {"index": 99, "title": "Invented", "company": "Nope"},  # out of range: ignored
                ]
            }
        ),
        category_json().replace('"index": 3', '"index": 1'),
    )
    report = env["agent"].discover(env["site"].id)
    assert report.jobs_created == 1 and report.deferred == 1  # per-check limit respected
    assert {j.company for j in db.query(Job)} == {"Acme"}
    assert db.query(DiscoveredPosting).filter_by(title="Invented").count() == 0


def _job(db: Session, site: JobSite, **kw: Any) -> Job:
    j = Job(
        source=JobSource.SITE,
        status=JobStatus.DETECTED,
        job_site_id=site.id,
        portal_id=site.portal_id,
        apply_url="https://x/jobs/1",
        role="Data Engineer Intern",
        **kw,
    )
    db.add(j)
    db.commit()
    return j


def test_stage2_handoff_and_search_only_lifecycle(
    db: Session, engine: Engine, gateway: LLMGateway, board: str, tmp_path: Path
) -> None:
    env = make_env(db, engine, gateway, board, tmp_path, selector=True)
    site = env["site"]
    job = _job(db, site, scheduled_at=datetime.now(UTC) - timedelta(minutes=1))
    portal_q: list[int] = []
    process_due_jobs(
        db, lambda *a, **k: None, lambda _db, j, **kw: portal_q.append((j.id, kw["merge_email"]))
    )  # type: ignore[arg-type,func-returns-value]
    assert portal_q == [(job.id, False)]  # stage 2 starts by opening the posting

    db.add(
        Resume(
            kind=ResumeKind.GENERATED,
            version=1,
            job_id=job.id,
            tex_source="x",
            score_report={"passed": True},
        )
    )
    db.commit()
    after_generation(db, job.id)
    db.expire_all()
    assert db.get(Job, job.id).status is JobStatus.RESUME_READY  # type: ignore[union-attr]
    assert db.query(Notification).filter_by(kind="job.ready_manual").count() == 1

    site.mode = SiteMode.SEARCH_AND_APPLY
    db.commit()
    job2 = _job(db, site)
    db.add(
        Resume(
            kind=ResumeKind.GENERATED,
            version=1,
            job_id=job2.id,
            tex_source="x",
            score_report={"passed": True},
        )
    )
    db.commit()
    after_generation(db, job2.id)
    db.expire_all()
    assert db.get(Job, job2.id).status is JobStatus.AWAITING_APPROVAL  # type: ignore[union-attr]  # auto-apply off


def test_sites_api(client: TestClient, db: Session) -> None:
    r = client.post(
        "/api/sites",
        json={
            "name": "Havlock",
            "search_urls": ["https://placements.haveloc.com/jobs?view=eligible"],
            "categories": ["Data Engineer"],
            "check_every_minutes": 60,
        },
    )
    assert r.status_code == 201, r.text
    s = r.json()
    assert (
        s["allowed_domains"] == ["placements.haveloc.com", "*.haveloc.com"]
        and s["mode"] == "search_only"
    )
    assert s["tos_warning"] is None and s["found"] == 0
    # the same portal is reused for another site on that domain
    r2 = client.post(
        "/api/sites",
        json={"name": "Havlock 2", "search_urls": ["https://placements.haveloc.com/jobs?view=all"]},
    ).json()
    assert r2["portal_id"] == s["portal_id"]

    li = {
        "name": "LinkedIn",
        "search_urls": ["https://www.linkedin.com/jobs/search/?keywords=data%20engineer"],
        "mode": "search_and_apply",
    }
    r = client.post("/api/sites", json=li)
    assert r.status_code == 422 and "LinkedIn" in r.text
    r = client.post("/api/sites", json=li | {"acknowledge_risk": True})
    assert r.status_code == 201 and "LinkedIn" in r.json()["tos_warning"]

    assert (
        client.post(
            "/api/sites", json={"name": "x", "search_urls": ["http://insecure.com/jobs"]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/sites",
            json={"name": "x", "search_urls": ["https://a.com/j"], "check_every_minutes": 1},
        ).status_code
        == 422
    )
    assert client.post(f"/api/sites/{s['id']}/check").status_code == 202
    assert client.get("/api/sites/postings").json() == []
    assert len(client.get("/api/sites").json()) == 3
    assert client.delete(f"/api/sites/{s['id']}").status_code == 204


def _ready(db: Session, site: JobSite, passed: bool = True) -> Job:
    j = _job(db, site)
    db.add(
        Resume(
            kind=ResumeKind.GENERATED,
            version=1,
            job_id=j.id,
            tex_source="x",
            score_report={"passed": passed},
            build_report={"within_limit": True},
        )
    )
    db.commit()
    return j


def test_review_window_then_auto_apply(
    db: Session, engine: Engine, gateway: LLMGateway, board: str, tmp_path: Path
) -> None:
    from app.generation.runs import release_due_auto_submits
    from app.models import Application, PipelineRun
    from app.models.enums import ApplicationStatus, RunKind

    env = make_env(
        db,
        engine,
        gateway,
        board,
        tmp_path,
        selector=True,
        mode=SiteMode.SEARCH_AND_APPLY,
        apply_delay_minutes=30,
    )
    job = _ready(db, env["site"])
    after_generation(db, job.id)
    db.expire_all()
    app_ = db.query(Application).one()
    assert app_.status is ApplicationStatus.PREPARED and app_.auto_submit_at is not None
    assert app_.auto_submit_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(minutes=29)
    assert db.get(Job, job.id).status is JobStatus.AWAITING_APPROVAL  # type: ignore[union-attr]
    assert db.query(Notification).filter_by(kind="job.ready_auto").count() == 1

    assert release_due_auto_submits(db) == 0  # still inside the window: nothing sent
    assert db.query(PipelineRun).count() == 0
    app_.auto_submit_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert release_due_auto_submits(db) == 1  # window over, user did nothing → apply
    db.expire_all()
    assert db.query(Application).one().status is ApplicationStatus.APPROVED
    assert [r.kind for r in db.query(PipelineRun)] == [RunKind.APPLY]


def test_zero_window_applies_immediately_and_failed_resumes_never_auto_send(
    db: Session, engine: Engine, gateway: LLMGateway, board: str, tmp_path: Path
) -> None:
    from app.models import Application, PipelineRun
    from app.models.enums import ApplicationStatus

    env = make_env(
        db,
        engine,
        gateway,
        board,
        tmp_path,
        selector=True,
        mode=SiteMode.SEARCH_AND_APPLY,
        apply_delay_minutes=0,
    )
    good = _ready(db, env["site"])
    after_generation(db, good.id)
    db.expire_all()
    assert (
        db.query(Application).filter_by(job_id=good.id).one().status is ApplicationStatus.APPROVED
    )
    assert db.query(PipelineRun).filter_by(job_id=good.id).count() == 1

    weak = _ready(db, env["site"], passed=False)
    after_generation(db, weak.id)
    db.expire_all()
    assert db.query(Application).filter_by(job_id=weak.id).count() == 0  # waits for a human
    assert db.get(Job, weak.id).status is JobStatus.AWAITING_APPROVAL  # type: ignore[union-attr]


def test_apply_now_and_dont_apply(
    client: TestClient, db: Session, engine: Engine, gateway: LLMGateway, board: str, tmp_path: Path
) -> None:
    from app.models import Application, PipelineRun
    from app.models.enums import ApplicationStatus

    env = make_env(
        db,
        engine,
        gateway,
        board,
        tmp_path,
        selector=True,
        mode=SiteMode.SEARCH_AND_APPLY,
        apply_delay_minutes=60,
    )
    job = _ready(db, env["site"])
    after_generation(db, job.id)
    detail = client.post(f"/api/jobs/{job.id}/approve", json={}).json()  # "Apply now"
    assert (
        detail["applications"][0]["status"] == "approved"
        and detail["applications"][0]["auto_submit_at"] is None
    )
    assert db.query(Application).count() == 1 and db.query(PipelineRun).count() == 1

    job2 = _ready(db, env["site"])
    after_generation(db, job2.id)
    r = client.delete(f"/api/jobs/{job2.id}/approval")  # "Don't apply"
    assert r.status_code == 200 and r.json()["status"] == "resume_ready"
    assert "chose not to apply" in r.json()["status_reason"]
    db.expire_all()
    assert db.query(Application).filter_by(job_id=job2.id).count() == 0

    job3 = _ready(db, env["site"])
    client.post(f"/api/jobs/{job3.id}/approve", json={})
    client.delete(f"/api/jobs/{job3.id}/approval")  # withdrawn before the agent started
    db.expire_all()
    assert db.query(PipelineRun).filter_by(job_id=job3.id).count() == 0  # queued run cancelled
    assert (
        db.query(Application).filter_by(job_id=job3.id, status=ApplicationStatus.APPROVED).count()
        == 0
    )
