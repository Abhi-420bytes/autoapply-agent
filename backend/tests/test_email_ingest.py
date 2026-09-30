from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.email.ingest import dedupe_key, poll_account, process_due_jobs, sender_matches
from app.email.oauth import TokenStore
from app.email.providers import Candidate, GmailProvider, GraphProvider, ImapProvider
from app.llm.gateway import LLMGateway
from app.models import (
    EmailAccount,
    Job,
    LLMProvider,
    LLMTaskConfig,
    Notification,
    Portal,
    SenderRule,
    SourceEmail,
)
from app.models.enums import ApplyMode, EmailProvider, JobStatus, LLMProviderKind, LLMTask
from app.services.settings_service import set_secret
from tests.fakes import FakeBackend
from tests.test_email_parsing import HAVLOCK_HTML, mime


class FakeProvider:
    def __init__(self, messages: dict[str, tuple[str, bytes]]) -> None:
        self.messages = messages  # provider_id -> (sender, raw)
        self.fetched: list[str] = []

    def test(self, account: EmailAccount) -> str:
        return "ok"

    def list_candidates(self, account, since, cursor):  # type: ignore[no-untyped-def]
        return [Candidate(pid, s, None) for pid, (s, _) in self.messages.items()], "cursor-1"

    def fetch_raw(self, account: EmailAccount, provider_id: str) -> bytes:
        self.fetched.append(provider_id)
        return self.messages[provider_id][1]


def classify_json(**kw: Any) -> str:
    return json.dumps(
        {
            "kind": "job_notice",
            "company": "Acme Corp",
            "role": "SDE Intern",
            "deadline": (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%dT23:59"),
            "apply_link": 1,
        }
        | kw
    )


@pytest.fixture
def mail_env(db: Session) -> dict[str, Any]:
    p = LLMProvider(label="T", kind=LLMProviderKind.OPENAI, api_key="sk-test-key-000000")
    db.add(p)
    db.flush()
    db.add(LLMTaskConfig(task=LLMTask.EMAIL_CLASSIFIER, provider_id=p.id, model="cls", params={}))
    portal = Portal(
        name="Havlock", base_url="https://app.havlock.in", allowed_domains=["*.havlock.in"]
    )
    outlook = EmailAccount(provider=EmailProvider.OUTLOOK_GRAPH, address="abhi@college.edu")
    gmail = EmailAccount(provider=EmailProvider.GMAIL_API, address="abhi@gmail.com")
    db.add_all([portal, outlook, gmail])
    db.flush()
    for acct in (outlook, gmail):
        db.add(
            SenderRule(
                email_account_id=acct.id,
                sender_match="@havlock.in",
                portal_id=portal.id,
                apply_mode=ApplyMode.READ_EMAIL_THEN_APPLY,
                delay_minutes=30,
            )
        )
    db.commit()
    return {"portal": portal, "outlook": outlook, "gmail": gmail}


def test_sender_matches() -> None:
    assert sender_matches("noreply@havlock.in", "@havlock.in")
    assert sender_matches("x@mail.havlock.in", "havlock.in")
    assert not sender_matches("x@evilhavlock.in", "@havlock.in")
    assert sender_matches("jobs@havlock.in", "jobs@havlock.in") and not sender_matches(
        "hr@havlock.in", "jobs@havlock.in"
    )


def test_job_notice_creates_scheduled_job(
    db: Session, gateway: LLMGateway, backend: FakeBackend, mail_env: dict[str, Any]
) -> None:
    backend.script("cls", classify_json())
    prov = FakeProvider(
        {
            "m1": ("noreply@havlock.in", mime(attach="1")),
            "m2": (
                "friend@gmail.com",
                mime(sender="Friend <friend@gmail.com>", mid="<p@x>"),
            ),  # personal
        }
    )
    rep = poll_account(db, gateway, mail_env["outlook"], prov, resolver=None)
    assert rep.jobs_created == 1 and prov.fetched == ["m1"]  # personal mail never downloaded
    job = db.query(Job).one()
    assert job.status is JobStatus.DETECTED and job.apply_url == "https://app.havlock.in/jobs/4821"
    assert job.company == "Acme Corp" and job.apply_mode is ApplyMode.READ_EMAIL_THEN_APPLY
    assert job.deadline is not None and len(job.files) == 1
    received = db.query(SourceEmail).one().received_at.replace(tzinfo=UTC)
    assert job.scheduled_at is not None and job.scheduled_at.replace(
        tzinfo=UTC
    ) == received + timedelta(minutes=30)
    assert mail_env["outlook"].sync_cursor == "cursor-1"


def test_same_email_in_both_inboxes_is_one_job(
    db: Session, gateway: LLMGateway, backend: FakeBackend, mail_env: dict[str, Any]
) -> None:
    backend.script("cls", classify_json())
    raw = mime()
    poll_account(
        db, gateway, mail_env["outlook"], FakeProvider({"a": ("noreply@havlock.in", raw)}), None
    )
    rep = poll_account(
        db, gateway, mail_env["gmail"], FakeProvider({"b": ("noreply@havlock.in", raw)}), None
    )
    assert rep.duplicates == 1 and db.query(Job).count() == 1
    assert len(backend.calls) == 1  # the twin wasn't even re-classified
    assert {s.job_id for s in db.query(SourceEmail)} == {db.query(Job).one().id}


def test_reminder_with_same_link_dedupes(
    db: Session, gateway: LLMGateway, backend: FakeBackend, mail_env: dict[str, Any]
) -> None:
    backend.script(
        "cls",
        classify_json(),
        classify_json(kind="reminder"),
        classify_json(kind="deadline_extension", deadline="2026-10-05T23:59"),
    )
    acct = mail_env["outlook"]
    for i, pid in enumerate(["a", "b", "c"]):
        poll_account(
            db,
            gateway,
            acct,
            FakeProvider({pid: ("noreply@havlock.in", mime(mid=f"<m{i}@h>"))}),
            None,
        )
    job = db.query(Job).one()
    assert job.deadline is not None and job.deadline.day == 5 and job.deadline.month == 10
    assert db.query(Notification).filter_by(kind="job.deadline_extended").count() == 1
    assert [s.classification.value for s in db.query(SourceEmail).order_by(SourceEmail.id)] == [
        "job_notice",
        "reminder",
        "deadline_extension",
    ]


def test_unsafe_link_marks_suspicious_and_alerts(
    db: Session, gateway: LLMGateway, backend: FakeBackend, mail_env: dict[str, Any]
) -> None:
    backend.script("cls", classify_json())
    html = HAVLOCK_HTML.replace(
        "https%3A%2F%2Fapp.havlock.in%2Fjobs%2F4821", "https%3A%2F%2Fhavlock-jobs.xyz%2Flogin"
    )
    poll_account(
        db,
        gateway,
        mail_env["outlook"],
        FakeProvider({"a": ("noreply@havlock.in", mime(html=html))}),
        None,
    )
    job = db.query(Job).one()
    assert job.status is JobStatus.SUSPICIOUS and "havlock-jobs.xyz" in (job.status_reason or "")
    assert job.apply_url is None and job.scheduled_at is None  # never opened or scheduled
    assert db.query(Notification).filter_by(kind="job.suspicious").count() == 1


def test_spoofed_sender_failing_dmarc_is_ignored(
    db: Session, gateway: LLMGateway, backend: FakeBackend, mail_env: dict[str, Any]
) -> None:
    raw = mime(auth="mx.college.edu; dmarc=fail header.from=havlock.in")
    rep = poll_account(
        db, gateway, mail_env["outlook"], FakeProvider({"a": ("noreply@havlock.in", raw)}), None
    )
    assert rep.ignored == 1 and db.query(SourceEmail).count() == 0 and backend.calls == []


def test_forwarded_from_own_outlook_to_gmail(
    db: Session, gateway: LLMGateway, backend: FakeBackend, mail_env: dict[str, Any]
) -> None:
    backend.script("cls", classify_json())
    body = (
        "---------- Forwarded message ---------\nFrom: Havlock <noreply@havlock.in>\nSubject: New Job\n\n"
        "Apply: https://app.havlock.in/jobs/4821"
    )
    fwd = mime(
        sender="Abhi <abhi@college.edu>",
        subject="Fwd: New Job",
        text=body,
        html=f"<pre>{body}</pre>",
        auth="mx.google.com; dkim=pass header.d=college.edu; dmarc=pass",
    )
    rep = poll_account(
        db, gateway, mail_env["gmail"], FakeProvider({"f": ("abhi@college.edu", fwd)}), None
    )
    assert rep.jobs_created == 1
    src = db.query(SourceEmail).one()
    assert src.original_sender == "noreply@havlock.in" and src.sender == "abhi@college.edu"

    # the same "forwarded" text from a stranger is NOT trusted
    stranger = mime(
        sender="x@evil.com",
        subject="Fwd: New Job",
        text=body,
        html=f"<pre>{body}</pre>",
        mid="<s@x>",
    )
    rep = poll_account(
        db, gateway, mail_env["gmail"], FakeProvider({"s": ("x@evil.com", stranger)}), None
    )
    assert rep.jobs_created == 0 and rep.downloaded == 0


def test_deadline_sooner_than_delay_processes_now(
    db: Session, gateway: LLMGateway, backend: FakeBackend, mail_env: dict[str, Any]
) -> None:
    soon = (datetime.now(UTC) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M")
    backend.script("cls", classify_json(deadline=soon + "+00:00"))
    fresh = mime(date=datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000"))
    poll_account(
        db, gateway, mail_env["outlook"], FakeProvider({"a": ("noreply@havlock.in", fresh)}), None
    )
    job = db.query(Job).one()
    assert job.scheduled_at is not None and job.scheduled_at.replace(tzinfo=UTC) <= datetime.now(
        UTC
    )
    assert db.query(Notification).filter_by(kind="job.urgent").count() == 1


def test_due_jobs_read_email_mode_uses_email_as_jd(
    db: Session, gateway: LLMGateway, backend: FakeBackend, mail_env: dict[str, Any]
) -> None:
    backend.script("cls", classify_json())
    fresh = mime(date=datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000"))
    poll_account(
        db, gateway, mail_env["outlook"], FakeProvider({"a": ("noreply@havlock.in", fresh)}), None
    )
    job = db.query(Job).one()
    queued: list[int] = []
    assert process_due_jobs(db, lambda _db, j, **kw: queued.append(j.id), None) == 0  # not due yet
    job.scheduled_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert process_due_jobs(db, lambda _db, j, **kw: queued.append(j.id), None) == 1
    assert (
        queued == [job.id]
        and "Acme Corp" in (job.jd_text or "")
        and "Subject:" in (job.jd_text or "")
    )


def test_dedupe_key_ignores_tracking_params() -> None:
    a = dedupe_key(1, "https://app.havlock.in/jobs/4821?utm_source=mail&id=9", None, None)
    b = dedupe_key(1, "https://app.havlock.in/jobs/4821/?id=9&utm_campaign=x", None, None)
    assert a == b and a != dedupe_key(1, "https://app.havlock.in/jobs/4822?id=9", None, None)


# -- providers ---------------------------------------------------------------------------


def _tokens_ready(db: Session, account: EmailAccount) -> None:
    account.oauth_tokens = {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    }
    db.commit()


def test_gmail_provider_lists_metadata_then_fetches_raw(
    db: Session, mail_env: dict[str, Any]
) -> None:
    raw = mime()
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(f"{req.url.path}?{req.url.params.get('format', '')}")
        assert req.headers["authorization"] == "Bearer at-1"
        if req.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "g1"}]})
        if req.url.params.get("format") == "metadata":
            return httpx.Response(
                200,
                json={
                    "internalDate": "1790000000000",
                    "payload": {
                        "headers": [{"name": "From", "value": "Havlock <noreply@havlock.in>"}]
                    },
                },
            )
        return httpx.Response(200, json={"raw": base64.urlsafe_b64encode(raw).decode().rstrip("=")})

    acct = mail_env["gmail"]
    _tokens_ready(db, acct)
    g = GmailProvider(TokenStore(db), httpx.MockTransport(handler))
    cands, cursor = g.list_candidates(acct, datetime.now(UTC) - timedelta(days=1), None)
    assert cands[0].sender == "noreply@havlock.in" and cursor
    assert g.fetch_raw(acct, "g1") == raw


def test_graph_provider_refreshes_expired_token(db: Session, mail_env: dict[str, Any]) -> None:
    set_secret(db, "microsoft_client_id", "cid-123")
    set_secret(db, "microsoft_client_secret", "csecret-456789")
    acct = mail_env["outlook"]
    acct.oauth_tokens = {
        "access_token": "old",
        "refresh_token": "rt-1",
        "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    }
    db.commit()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "login.microsoftonline.com":
            assert b"grant_type=refresh_token" in req.content
            return httpx.Response(200, json={"access_token": "new-at", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer new-at"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "o1",
                        "receivedDateTime": "2026-09-24T04:30:00Z",
                        "from": {"emailAddress": {"address": "NoReply@Havlock.in"}},
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    g = GraphProvider(TokenStore(db, transport), transport)
    cands, _ = g.list_candidates(acct, datetime.now(UTC) - timedelta(days=1), None)
    assert cands == [
        Candidate("o1", "noreply@havlock.in", datetime(2026, 9, 24, 4, 30, tzinfo=UTC))
    ]
    db.refresh(acct)
    assert (
        acct.oauth_tokens["access_token"] == "new-at"
        and acct.oauth_tokens["refresh_token"] == "rt-1"
    )


def test_imap_provider_is_readonly_and_uses_uid_cursor() -> None:
    raw = mime()

    class FakeImap:
        calls: list[tuple[str, ...]] = []

        def login(self, u: str, p: str) -> None:
            assert p == "app-password"

        def select(self, box: str, readonly: bool = False) -> None:
            assert readonly  # never changes read flags

        def uid(self, cmd: str, *args: Any) -> tuple[str, list[Any]]:
            FakeImap.calls.append((cmd, *[str(a) for a in args]))
            if cmd == "SEARCH":
                return "OK", [b"5 6"]
            if "HEADER.FIELDS" in str(args[-1]):
                return "OK", [
                    (
                        b"x",
                        b"From: Havlock <noreply@havlock.in>\r\nDate: Wed, 24 Sep 2026 10:00:00 +0530\r\n\r\n",
                    )
                ]
            return "OK", [(b"x", raw)]

        def logout(self) -> None:
            pass

    acct = EmailAccount(
        provider=EmailProvider.IMAP,
        address="me@x.com",
        imap_host="imap.x.com",
        imap_password="app-password",
    )
    p = ImapProvider(connect=lambda h, port: FakeImap())
    cands, cursor = p.list_candidates(acct, datetime.now(UTC), "4")
    assert [c.provider_id for c in cands] == ["5", "6"] and cursor == "6"
    assert ("SEARCH", "None", "UID 5:*") in FakeImap.calls
    assert p.fetch_raw(acct, "5") == raw


# -- API -----------------------------------------------------------------------------------


def test_email_and_portal_api(client: TestClient, db: Session) -> None:
    portal = client.post(
        "/api/portals",
        json={
            "name": "Havlock",
            "base_url": "https://app.havlock.in",
            "allowed_domains": ["*.havlock.in"],
        },
    ).json()
    assert (
        client.post(
            "/api/portals", json={"name": "x", "base_url": "http://insecure.com"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/portals",
            json={"name": "y", "base_url": "https://a.com", "allowed_domains": ["not a domain"]},
        ).status_code
        == 422
    )

    acct = client.post(
        "/api/email/accounts",
        json={
            "provider": "imap",
            "address": "Me@X.com",
            "imap_host": "imap.x.com",
            "imap_password": "app-password-123",
        },
    ).json()
    assert acct["address"] == "me@x.com" and acct["imap_password_masked"] == "app-...-123"
    assert "app-password" not in client.get("/api/email/accounts").text
    rule = client.post(
        f"/api/email/accounts/{acct['id']}/rules",
        json={
            "sender_match": "@Havlock.in",
            "portal_id": portal["id"],
            "apply_mode": "read_email_then_apply",
            "delay_minutes": 45,
        },
    ).json()
    assert rule["sender_match"] == "@havlock.in"
    r = client.patch(f"/api/email/rules/{rule['id']}", json={"apply_mode": "direct_link"})
    assert r.json()["apply_mode"] == "direct_link"
    assert client.post(f"/api/email/accounts/{acct['id']}/sync").status_code == 202
    assert client.post(f"/api/email/accounts/{acct['id']}/oauth/start").status_code == 422  # IMAP

    gm = client.post(
        "/api/email/accounts", json={"provider": "gmail_api", "address": "me@gmail.com"}
    ).json()
    r = client.post(f"/api/email/accounts/{gm['id']}/oauth/start")
    assert r.status_code == 409 and "client ID" in r.text  # no OAuth app configured yet
    client.put(
        "/api/settings/secrets/google_client_id", json={"value": "cid.apps.googleusercontent.com"}
    )
    client.put("/api/settings/secrets/google_client_secret", json={"value": "GOCSPX-secret-value"})
    url = client.post(f"/api/email/accounts/{gm['id']}/oauth/start").json()["auth_url"]
    assert (
        url.startswith("https://accounts.google.com/")
        and "code_challenge=" in url
        and "gmail.readonly" in url
    )
    assert (
        client.post("/api/email/oauth/callback", json={"code": "x", "state": "forged"}).status_code
        == 400
    )


@pytest.mark.parametrize(
    ("body", "expect"),
    [
        (
            {
                "error": {
                    "code": 403,
                    "message": "Gmail API has not been used in project 123 before or it is disabled.",
                    "details": [{"reason": "SERVICE_DISABLED"}],
                }
            },
            "Enable the Gmail API",
        ),
        (
            {
                "error": {
                    "code": 403,
                    "message": "Request had insufficient authentication scopes.",
                    "errors": [{"reason": "insufficientPermissions"}],
                }
            },
            "tick the 'Read your email' box",
        ),
    ],
)
def test_gmail_errors_are_explained(body: dict[str, Any], expect: str) -> None:
    from app.email.providers import explain_api_error

    msg = explain_api_error(httpx.Response(403, json=body), GmailProvider.base)
    assert msg.startswith("403 from Gmail:") and expect in msg and "college" not in msg


@pytest.mark.parametrize(
    ("value", "result"),
    [
        ("NoReply@Haveloc.com", "noreply@haveloc.com"),
        ("haveloc.com", "@haveloc.com"),
        ("@mail.haveloc.com", "@mail.haveloc.com"),
        ("https://placements.haveloc.com/jobs?sort=x", None),
        ("placements.haveloc.com/jobs", None),
        ("havloc", None),
    ],
)
def test_sender_match_validation(value: str, result: str | None) -> None:
    from app.api.email import normalize_sender_match

    if result is None:
        with pytest.raises(ValueError):
            normalize_sender_match(value)
    else:
        assert normalize_sender_match(value) == result


def test_read_email_job_without_link_generates_instead_of_suspicious(
    db: Session, gateway: LLMGateway, backend: FakeBackend, mail_env: dict[str, Any]
) -> None:
    backend.script("cls", classify_json(apply_link=None))
    html = "<p>Acme is hiring a Data Engineer Intern. Skills: Python, SQL. Reply to apply.</p>"
    fresh = mime(
        html=html,
        text="Acme is hiring",
        date=datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000"),
    )
    poll_account(
        db, gateway, mail_env["outlook"], FakeProvider({"a": ("noreply@havlock.in", fresh)}), None
    )
    job = db.query(Job).one()
    assert (
        job.status is JobStatus.DETECTED
        and job.apply_url is None
        and "apply yourself" in (job.status_reason or "")
    )
    job.scheduled_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    queued_gen: list[int] = []
    queued_portal: list[int] = []
    process_due_jobs(
        db,
        lambda _db, j, **kw: queued_gen.append(j.id),
        lambda _db, j, **kw: queued_portal.append(j.id),
    )
    assert queued_gen == [job.id] and queued_portal == []  # nothing to open: straight to generation
    assert "Data Engineer" in (job.jd_text or "")


def test_bad_link_is_still_suspicious_in_read_email_mode(
    db: Session, gateway: LLMGateway, backend: FakeBackend, mail_env: dict[str, Any]
) -> None:
    backend.script("cls", classify_json())
    html = HAVLOCK_HTML.replace(
        "https%3A%2F%2Fapp.havlock.in%2Fjobs%2F4821", "https%3A%2F%2Fevil.example%2Flogin"
    )
    poll_account(
        db,
        gateway,
        mail_env["outlook"],
        FakeProvider({"a": ("noreply@havlock.in", mime(html=html))}),
        None,
    )
    assert db.query(Job).one().status is JobStatus.SUSPICIOUS
