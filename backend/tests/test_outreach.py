"""Cold-email outreach: crawling, discovery, drafting and approval-gated sending."""

# ruff: noqa: F811  (the gen_env fixture is imported from test_generation)
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models import EmailAccount, Job, OutreachCompany, OutreachEmail, Resume
from app.models.enums import ConnectionStatus, EmailProvider, ResumeKind
from app.outreach import service
from app.outreach.crawl import SiteReader, emails_in
from app.outreach.discover import company_name, discover, is_company_site
from app.outreach.draft import disclosure
from app.outreach.send import build_message, send
from app.schemas.settings import AppSettingsPatch, Profile
from app.search.web import SearchResult
from app.services.settings_service import set_profile, update_app_settings
from tests.fakes import FakeBackend, make_pdf
from tests.test_generation import gen_env  # noqa: F401 (fixture)


def test_only_published_hiring_addresses_on_the_company_domain() -> None:
    html = (
        '<a href="mailto:careers@acme.io">Careers</a> privacy@acme.io noreply@acme.io '
        "sales@acme.io priya@acme.io someone@gmail.com logo@2x.acme.io.png"
    )
    text = "Write to jobs [at] acme [dot] io or hr@eu.acme.io"
    assert set(emails_in(html, text, "acme.io")) == {
        "careers@acme.io",
        "priya@acme.io",
        "jobs@acme.io",
        "hr@eu.acme.io",
    }


def _site(routes: dict[str, tuple[int, str]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        status, body = routes.get(str(request.url), (404, "not found"))
        return httpx.Response(status, text=body, headers={"content-type": "text/html"})

    return httpx.MockTransport(handler)


def test_site_reader_follows_careers_link_and_ranks_hiring_inbox_first() -> None:
    reader = SiteReader(
        _site(
            {
                "https://acme.io/robots.txt": (200, "User-agent: *\nDisallow: /admin"),
                "https://acme.io/": (
                    200,
                    '<h1>Acme builds payment APIs</h1><a href="/careers">Careers</a>'
                    '<a href="/admin">x</a><a href="https://other.com/jobs">jobs</a>'
                    "<p>hello@acme.io</p>",
                ),
                "https://acme.io/careers": (
                    200,
                    "<h2>Backend Engineer</h2><p>Send your CV to careers@acme.io</p>",
                ),
            }
        ),
        pause_s=0,
    )
    rep = reader.read("https://acme.io/")
    assert rep.blocked is None
    assert [p.url for p in rep.pages] == ["https://acme.io/", "https://acme.io/careers"]
    assert [(e.address, e.kind) for e in rep.emails] == [
        ("careers@acme.io", "careers"),
        ("hello@acme.io", "general"),
    ]


def test_site_reader_respects_robots_txt() -> None:
    reader = SiteReader(
        _site({"https://acme.io/robots.txt": (200, "User-agent: *\nDisallow: /")}), pause_s=0
    )
    rep = reader.read("https://acme.io")
    assert rep.pages == [] and "robots.txt" in (rep.blocked or "")


class FakeSearch:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results

    def search(
        self, query: str, *, count: int = 10, country: str | None = None
    ) -> list[SearchResult]:
        return self.results


def test_discovery_keeps_company_sites_only() -> None:
    results = [
        SearchResult("https://www.naukri.com/python-jobs", "Python jobs", ""),
        SearchResult("https://yourstory.com/top-startups", "Top startups", ""),
        SearchResult("https://careers.acme.io/jobs", "Careers | Acme Payments", ""),
        SearchResult("https://acme.io/about", "Acme", ""),  # same company again
        SearchResult("https://www.zeta.tech/careers", "Zeta – Work with us", ""),
    ]
    found = discover(
        FakeSearch(results), ["python developer"], ["Bengaluru"], ["startups"], {"zeta.tech"}, 5
    )  # type: ignore[arg-type]
    assert [(c.name, c.domain) for c in found] == [("Acme Payments", "acme.io")]
    assert not is_company_site("www.linkedin.com") and is_company_site("acme.io")
    assert company_name("Work with us - Razorpay", "razorpay.com") == "Razorpay"


def _setup_company(db: Session) -> OutreachCompany:
    set_profile(
        db,
        Profile(
            full_name="Abhi Ram",
            email="abhi@example.com",
            links={"github": "https://github.com/abhi"},
        ),
    )
    c = OutreachCompany(
        name="Acme",
        domain="acme.io",
        website="https://acme.io",
        status="researched",
        summary={"what_they_do": "payment APIs", "tech": ["Python", "FastAPI"], "hook": "UPI API"},
        emails=[
            {
                "address": "careers@acme.io",
                "source_url": "https://acme.io/careers",
                "kind": "careers",
            }
        ],
    )
    db.add(c)
    db.commit()
    return c


def test_draft_is_truthful_signed_and_discloses_the_agent(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    c = _setup_company(db)
    queued: list[int] = []
    email = service.queue_company(db, c, lambda _db, j, **kw: queued.append(j.id))
    job = db.get(Job, email.job_id)
    assert job is not None and queued == [job.id] and "payment APIs" in (job.jd_text or "")
    assert db.get(OutreachCompany, c.id).status == "queued"  # type: ignore[union-attr]

    service.draft_ready(db, gen_env["deps"].gateway, email.id)  # no resume yet: waits
    assert db.get(OutreachEmail, email.id).status == "resume_pending"  # type: ignore[union-attr]

    db.add(
        Resume(
            kind=ResumeKind.GENERATED, version=1, job_id=job.id, tex_source="x", pdf_path="r.pdf"
        )
    )
    db.commit()
    backend.script(
        "writer",
        json.dumps(
            {
                "subject": "Python backend help for Acme's UPI API",
                "greeting": "Hi Acme team,",
                "body": "I'm Abhi. I built a REST API in FastAPI serving 2k daily requests, "
                "and grew it to 500 users.",
                "projects_used": ["b1"],
                "adaptation": False,
            }
        ),
    )
    service.draft_ready(db, gen_env["deps"].gateway, email.id)
    db.expire_all()
    e = db.get(OutreachEmail, email.id)
    assert e is not None and e.status == "ready" and e.resume_id is not None
    assert e.body is not None and e.body.startswith("Hi Acme team,")
    assert "Abhi Ram\nabhi@example.com\nhttps://github.com/abhi" in e.body
    assert e.body.rstrip().endswith(disclosure("Abhi Ram"))
    assert any("500" in w for w in e.warnings)  # 500 isn't in any fact: flagged
    assert not any("2" in w.split(":")[-1].split(",")[0] for w in e.warnings if "2k" in w)


def test_sending_needs_approval_and_respects_the_daily_cap(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c = _setup_company(db)
    db.add(
        EmailAccount(
            provider=EmailProvider.GMAIL_API,
            address="abhi@gmail.com",
            status=ConnectionStatus.CONNECTED,
        )
    )
    pdf = service.data_root() / "resumes" / "cold.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(make_pdf(1, body="resume"))
    r = Resume(kind=ResumeKind.GENERATED, version=1, tex_source="x", pdf_path="resumes/cold.pdf")
    db.add(r)
    db.flush()
    e = OutreachEmail(
        company_id=c.id,
        resume_id=r.id,
        to_address="careers@acme.io",
        subject="Hi",
        body="Body",
        status="ready",
    )
    db.add(e)
    db.commit()
    sent: list[str] = []
    monkeypatch.setattr(service, "send", lambda _db, acct, msg: sent.append(msg["To"]) or "id-1")

    service.send_approved(db, e.id)
    assert sent == []  # not approved: never sent

    e.status = "approved"
    db.commit()
    update_app_settings(db, AppSettingsPatch(outreach_daily_cap=1))
    other = OutreachCompany(name="B", domain="b.io", website="https://b.io", status="queued")
    db.add(other)
    db.flush()
    db.add(
        OutreachEmail(
            company_id=other.id,
            to_address="x@b.io",
            status="sent",
            sent_at=datetime.now(UTC) - timedelta(hours=2),
        )
    )
    db.commit()
    service.send_approved(db, e.id)
    assert sent == [] and db.get(OutreachEmail, e.id).status == "approved"  # type: ignore[union-attr]

    update_app_settings(db, AppSettingsPatch(outreach_daily_cap=5))
    service.send_approved(db, e.id)
    db.expire_all()
    assert sent == ["careers@acme.io"] and db.get(OutreachEmail, e.id).status == "sent"  # type: ignore[union-attr]


def test_gmail_send_posts_raw_mime_with_resume(db: Session, tmp_path: Path) -> None:
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(make_pdf(1, body="resume"))
    msg = build_message(
        from_name="Abhi Ram",
        from_address="abhi@gmail.com",
        to="careers@acme.io",
        subject="Hello",
        body="Body",
        attachment=pdf,
        attachment_name="Abhi_Ram_Resume.pdf",
    )
    acct = EmailAccount(
        provider=EmailProvider.GMAIL_API,
        address="abhi@gmail.com",
        oauth_tokens={
            "access_token": "tok",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["raw"] = json.loads(request.content)["raw"]
        return httpx.Response(200, json={"id": "gm-1"})

    assert send(db, acct, msg, transport=httpx.MockTransport(handler)) == "gm-1"
    assert seen["url"].endswith("/users/me/messages/send") and seen["auth"] == "Bearer tok"
    parsed = message_from_bytes(base64.urlsafe_b64decode(seen["raw"]))
    assert parsed["To"] == "careers@acme.io"
    names = [p.get_filename() for p in parsed.walk() if p.get_filename()]
    assert names == ["Abhi_Ram_Resume.pdf"]


def test_outreach_api_edit_keeps_the_disclosure_and_send_approves(
    client: Any, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _setup_company(db)
    e = OutreachEmail(
        company_id=c.id, to_address="careers@acme.io", subject="S", body="B", status="ready"
    )
    db.add(e)
    db.commit()
    r = client.patch(f"/api/outreach/emails/{e.id}", json={"body": "My own words, rewritten."})
    assert r.status_code == 200
    body = r.json()["companies"][0]["email"]["body"]
    assert body.endswith(disclosure("Abhi Ram"))

    calls: list[int] = []
    monkeypatch.setattr("app.api.outreach.send_approved", lambda _db, eid: calls.append(eid))
    r = client.post(f"/api/outreach/emails/{e.id}/send")
    assert r.status_code == 200 and calls == [e.id]
    assert r.json()["companies"][0]["email"]["status"] == "approved"
    assert client.post(f"/api/outreach/emails/{e.id}/send").status_code == 409

    r = client.post("/api/outreach/companies", json={"website": "zeta.tech/careers"})
    assert r.status_code == 201
    assert any(x["domain"] == "zeta.tech" and x["status"] == "new" for x in r.json()["companies"])


def _draft_json(body: str) -> str:
    return json.dumps(
        {"subject": "Hi Acme", "greeting": "Hi Acme team,", "body": body, "projects_used": ["b1"]}
    )


def test_clean_drafts_send_automatically_after_the_window_flagged_ones_wait(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    update_app_settings(db, AppSettingsPatch(outreach_auto_send=True))
    c = _setup_company(db)
    email = service.queue_company(db, c, lambda *a, **k: None)
    db.add(
        Resume(
            kind=ResumeKind.GENERATED,
            version=1,
            job_id=email.job_id,
            tex_source="x",
            pdf_path="r.pdf",
        )
    )
    db.commit()
    backend.script("writer", _draft_json("I built a REST API in FastAPI and PostgreSQL."))
    service.draft_ready(db, gen_env["deps"].gateway, email.id)
    db.expire_all()
    e = db.get(OutreachEmail, email.id)
    assert e is not None and e.status == "ready" and e.auto_send_at is not None  # clean: scheduled
    assert (e.auto_send_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds() > 25 * 60

    sent: list[int] = []
    monkeypatch.setattr(service, "send_approved", lambda _db, eid: sent.append(eid))
    service.process_outreach(
        gen_env["deps"].gateway,
        gen_env["maker"],
        lambda *a, **k: None,
        reader=SiteReader(_site({}), pause_s=0),
        search=FakeSearch([]),
    )
    assert sent == []  # window not over yet
    e.auto_send_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    service.process_outreach(
        gen_env["deps"].gateway,
        gen_env["maker"],
        lambda *a, **k: None,
        reader=SiteReader(_site({}), pause_s=0),
        search=FakeSearch([]),
    )
    assert sent == [e.id]

    # a draft with an unverifiable number is never sent automatically
    other = OutreachCompany(
        name="Zeta",
        domain="zeta.tech",
        website="https://zeta.tech",
        status="researched",
        summary={},
        emails=[{"address": "jobs@zeta.tech", "source_url": "x", "kind": "careers"}],
    )
    db.add(other)
    db.commit()
    e2 = service.queue_company(db, other, lambda *a, **k: None)
    db.add(
        Resume(
            kind=ResumeKind.GENERATED, version=1, job_id=e2.job_id, tex_source="x", pdf_path="r.pdf"
        )
    )
    db.commit()
    backend.script("writer", _draft_json("My app has 900 users."))
    service.draft_ready(db, gen_env["deps"].gateway, e2.id)
    db.expire_all()
    got = db.get(OutreachEmail, e2.id)
    assert got is not None and got.status == "ready" and got.auto_send_at is None
    assert any("won't be sent automatically" in w for w in got.warnings)


def test_large_companies_are_skipped(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    from app.outreach.draft import CompanyResearch

    def research_json(size: str, evidence: str) -> str:
        return CompanyResearch(
            name="Acme", what_they_do="payments", size=size, size_evidence=evidence
        ).model_dump_json()  # type: ignore[arg-type]

    site = {
        "https://acme.io/robots.txt": (404, ""),
        "https://acme.io/": (
            200,
            "<p>Acme has 25,000 employees in 40 countries. careers@acme.io</p>",
        ),
    }
    c = OutreachCompany(name="Acme", domain="acme.io", website="https://acme.io/", status="new")
    db.add(c)
    db.commit()
    backend.script("jd", research_json("large", "25,000 employees in 40 countries"))
    service.research_company(
        db, gen_env["deps"].gateway, c.id, SiteReader(_site(site), pause_s=0), None
    )
    db.expire_all()
    got = db.get(OutreachCompany, c.id)
    assert got is not None and got.status == "skipped" and got.size == "large"
    assert "25,000 employees" in (got.error or "")

    # a size claim without a real quote on the site counts as unknown (kept)
    c2 = OutreachCompany(name="Beta", domain="beta.io", website="https://beta.io/", status="new")
    db.add(c2)
    db.commit()
    site2 = {
        "https://beta.io/robots.txt": (404, ""),
        "https://beta.io/": (200, "<p>We build tools. hello@beta.io</p>"),
    }
    backend.script("jd", research_json("large", "Fortune 500 company"))
    service.research_company(
        db, gen_env["deps"].gateway, c2.id, SiteReader(_site(site2), pause_s=0), None
    )
    db.expire_all()
    got2 = db.get(OutreachCompany, c2.id)
    assert got2 is not None and got2.size is None and got2.status == "researched"


def test_known_enterprises_are_not_discovered() -> None:
    results = [
        SearchResult("https://careers.infosys.com/jobs", "Infosys careers", ""),
        SearchResult("https://smallco.ai/careers", "SmallCo – careers", ""),
    ]
    found = discover(FakeSearch(results), ["python"], ["Bengaluru"], ["companies"], set(), 5)  # type: ignore[arg-type]
    assert [c.domain for c in found] == ["smallco.ai"]


def test_ai_suggested_address_rules() -> None:
    from app.outreach.discover import acceptable_ai_address

    assert acceptable_ai_address("Careers@Acme.io", "acme.io") == "careers@acme.io"
    assert acceptable_ai_address("priya.sharma@acme.io", "acme.io") is None  # no guessed people
    assert acceptable_ai_address("careers@gmail.com", "acme.io") is None
    assert acceptable_ai_address("sales@acme.io", "acme.io") is None


def test_no_published_email_falls_back_to_ai_and_then_waits_for_you(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.outreach.draft import CompanyResearch

    monkeypatch.setattr(service, "has_mail_server", lambda domain: True)
    site = {
        "https://acme.io/robots.txt": (404, ""),
        "https://acme.io/": (200, "<p>Acme builds payment APIs</p>"),
    }
    c = OutreachCompany(name="Acme", domain="acme.io", website="https://acme.io/", status="new")
    db.add(c)
    db.commit()
    backend.script(
        "jd",
        CompanyResearch(name="Acme", what_they_do="payment APIs for small shops").model_dump_json(),
        json.dumps(
            {"address": "careers@acme.io", "confidence": "pattern", "reason": "common inbox"}
        ),
    )
    service.research_company(
        db, gen_env["deps"].gateway, c.id, SiteReader(_site(site), pause_s=0), FakeSearch([])
    )
    db.expire_all()
    got = db.get(OutreachCompany, c.id)
    assert got is not None and got.status == "researched"
    assert got.emails[0]["address"] == "careers@acme.io" and got.emails[0]["source"] == "ai"

    email = service.queue_company(db, got, lambda *a, **k: None)
    db.add(
        Resume(
            kind=ResumeKind.GENERATED,
            version=1,
            job_id=email.job_id,
            tex_source="x",
            pdf_path="r.pdf",
        )
    )
    db.commit()
    backend.script("writer", _draft_json("I built a REST API in FastAPI and PostgreSQL."))
    service.draft_ready(db, gen_env["deps"].gateway, email.id)
    db.expire_all()
    e = db.get(OutreachEmail, email.id)
    assert e is not None and e.status == "ready" and e.auto_send_at is None  # waits for you
    assert any("suggested by the AI" in w for w in e.warnings)
    assert "Best regards,\nAbhi Ram" in (e.body or "")


def test_job_boards_and_advertising_inboxes_are_skipped(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    from app.outreach.draft import CompanyResearch

    site = {
        "https://startupplay.com/robots.txt": (404, ""),
        "https://startupplay.com/": (200, '<p>Startup jobs</p><a href="/advertise">Advertise</a>'),
        "https://startupplay.com/advertise": (
            200,
            "<p>Advertise with us: contact@startupplay.com</p>",
        ),
    }
    c = OutreachCompany(
        name="Startup Play",
        domain="startupplay.com",
        website="https://startupplay.com/",
        status="new",
    )
    db.add(c)
    db.commit()
    backend.script(
        "jd",
        CompanyResearch(
            name="Startup Play", what_they_do="A job board with thousands of startup job listings"
        ).model_dump_json(),
    )
    service.research_company(
        db, gen_env["deps"].gateway, c.id, SiteReader(_site(site), pause_s=0), None
    )
    db.expire_all()
    got = db.get(OutreachCompany, c.id)
    assert got is not None and got.status == "skipped" and "job board" in (got.error or "")
    assert got.emails == []  # the advertising inbox isn't collected


def test_signature_uses_the_resume_name_when_profile_name_is_empty() -> None:
    from app.outreach.draft import author_name, disclosure, signature

    p = Profile(full_name="Abhi Ram")
    assert author_name(p) == "Abhi Ram" and author_name(Profile()) == ""  # no hard-coded name
    assert signature(p, author_name(p)).startswith("Best regards,\nAbhi Ram")
    assert "agentic AI system created by Abhiram" in disclosure(author_name(p))
    assert disclosure("Abhi Ram", "Sent by AutoApply Agent, built by {name}.") == (
        "Sent by AutoApply Agent, built by Abhi Ram."
    )


def test_closing_note_is_editable_but_must_name_the_agent(client: Any) -> None:
    ok = "Sent by AutoApply Agent, the agentic AI I built. Happy to demo it on a call!"
    r = client.patch("/api/settings", json={"outreach_closing_note": ok})
    assert r.status_code == 200 and r.json()["outreach_closing_note"] == ok
    bad = client.patch(
        "/api/settings", json={"outreach_closing_note": "Thanks for reading my email!"}
    )
    assert bad.status_code == 422


def test_ai_suggested_companies_are_filtered_and_added(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    update_app_settings(
        db,
        AppSettingsPatch(
            outreach_roles=["backend engineer"],
            outreach_locations=["Bengaluru"],
            outreach_new_per_search=5,
        ),
    )
    db.add(OutreachCompany(name="Old", domain="old.io", website="https://old.io", status="skipped"))
    db.commit()
    backend.script(
        "jd",
        json.dumps(
            {
                "companies": [
                    {"name": "Zeta", "website": "https://www.zeta.tech", "why": "fintech"},
                    {"name": "Infosys", "website": "infosys.com", "why": "big"},
                    {"name": "Naukri", "website": "https://www.naukri.com", "why": "jobs"},
                    {"name": "Old", "website": "https://old.io", "why": "dup"},
                    {"name": "Setu", "website": "setu.co", "why": "APIs"},
                ]
            }
        ),
    )
    n = service.run_discovery(db, FakeSearch([]), force=True, gateway=gen_env["deps"].gateway)
    assert n == 2
    got = {c.domain: c for c in db.query(OutreachCompany).filter_by(status="new")}
    assert set(got) == {"zeta.tech", "setu.co"} and got["setu.co"].website == "https://setu.co"
    assert (got["zeta.tech"].source_query or "").startswith("AI:")


def test_lists_and_job_pages_are_not_companies() -> None:
    results = [
        SearchResult("https://topstartups.io/bangalore", "Top 29 Bangalore Startups", ""),
        SearchResult("https://startup.jobs/x", "Startup jobs", ""),
        SearchResult("https://news.example.com/a", "19 Remote, Hybrid & Onsite Startups", ""),
        SearchResult("https://acme.ai/", "Acme – AI for invoices", ""),
    ]
    found = discover(FakeSearch(results), ["python"], ["Bengaluru"], ["startups"], set(), 5)  # type: ignore[arg-type]
    assert [c.domain for c in found] == ["acme.ai"]


def _research(**kw: Any) -> str:
    from app.outreach.draft import CompanyResearch

    return CompanyResearch(name="Acme", what_they_do="payment APIs", **kw).model_dump_json()


def test_hiring_email_from_the_web_beats_contact_us(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    site = {
        "https://acme.io/robots.txt": (404, ""),
        "https://acme.io/": (200, "<p>Payments. contact@acme.io</p>"),
    }
    c = OutreachCompany(name="Acme", domain="acme.io", website="https://acme.io/", status="new")
    db.add(c)
    db.commit()
    backend.script("jd", _research())
    web = FakeSearch(
        [SearchResult("https://angel.co/acme", "Acme", "Hiring! Send your CV to careers@acme.io")]
    )
    service.research_company(
        db, gen_env["deps"].gateway, c.id, SiteReader(_site(site), pause_s=0), web
    )
    db.expire_all()
    got = db.get(OutreachCompany, c.id)
    assert got is not None and got.status == "researched"
    assert [(e["address"], e["source"]) for e in got.emails] == [
        ("careers@acme.io", "web"),
        ("contact@acme.io", "site"),
    ]


def test_no_email_anywhere_watches_the_careers_page_and_applies_to_matches(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models import Portal
    from app.models.enums import JobSource

    monkeypatch.setattr(service, "has_mail_server", lambda d: False)  # the AI guess is rejected
    update_app_settings(db, AppSettingsPatch(outreach_roles=["backend engineer"]))
    site = {
        "https://acme.io/robots.txt": (404, ""),
        "https://acme.io/": (200, '<p>Payments</p><a href="/careers">Careers</a>'),
        "https://acme.io/careers": (200, "<h2>Open roles</h2><p>Backend Engineer Intern</p>"),
    }
    c = OutreachCompany(name="Acme", domain="acme.io", website="https://acme.io/", status="new")
    db.add(c)
    db.commit()
    backend.script("jd", _research(), json.dumps({"address": "careers@acme.io"}))
    reader = SiteReader(_site(site), pause_s=0)
    service.research_company(db, gen_env["deps"].gateway, c.id, reader, FakeSearch([]))
    db.expire_all()
    got = db.get(OutreachCompany, c.id)
    assert (
        got is not None
        and got.status == "watching"
        and got.careers_url == "https://acme.io/careers"
    )

    roles = [
        {"title": "Backend Engineer Intern", "url": "https://acme.io/careers/be-intern"},
        {"title": "Senior Backend Engineer", "url": "https://acme.io/careers/sr"},
        {"title": "Sales Manager", "url": "https://acme.io/careers/sales"},
    ]
    backend.script("jd", _research(open_roles=roles), _research(open_roles=roles))
    assert service.check_careers_page(db, gen_env["deps"].gateway, c.id, reader) == 1
    job = db.query(Job).filter(Job.dedupe_key.like("careers:%")).one()
    assert job.source is JobSource.SITE and job.role == "Backend Engineer Intern"
    assert job.apply_url == "https://acme.io/careers/be-intern" and job.scheduled_at is not None
    assert db.get(Portal, job.portal_id).allowed_domains == ["*.acme.io"]  # type: ignore[union-attr]
    assert service.check_careers_page(db, gen_env["deps"].gateway, c.id, reader) == 0  # no repeats


def test_your_email_is_used_first(client: Any, db: Session) -> None:
    r = client.post(
        "/api/outreach/companies", json={"website": "setu.co", "email": "Hiring@Setu.co"}
    )
    assert r.status_code == 201
    c = next(x for x in r.json()["companies"] if x["domain"] == "setu.co")
    assert c["emails"][0] == {
        "address": "hiring@setu.co",
        "source_url": "",
        "kind": "careers",
        "source": "user",
    }

    w = OutreachCompany(
        name="W",
        domain="w.io",
        website="https://w.io",
        status="watching",
        summary={"what_they_do": "x"},
        careers_url="https://w.io/careers",
    )
    db.add(w)
    db.commit()
    r = client.put(f"/api/outreach/companies/{w.id}/email", json={"email": "founders@w.io"})
    got = next(x for x in r.json()["companies"] if x["domain"] == "w.io")
    assert got["status"] == "researched" and got["emails"][0]["source"] == "user"


def test_just_an_email_is_enough(client: Any, db: Session) -> None:
    r = client.post("/api/outreach/companies", json={"email": "Priya@Zeta.tech"})
    assert r.status_code == 201
    work = next(x for x in r.json()["companies"] if x["domain"] == "zeta.tech")
    assert work["status"] == "new" and work["website"] == "https://zeta.tech"  # site gets read
    assert (
        work["emails"][0]["address"] == "priya@zeta.tech" and work["emails"][0]["source"] == "user"
    )

    r = client.post(
        "/api/outreach/companies", json={"email": "hr.recruiter@gmail.com", "name": "Acme"}
    )
    personal = next(x for x in r.json()["companies"] if x["domain"] == "hr.recruiter@gmail.com")
    assert personal["name"] == "Acme" and personal["status"] == "researched"  # no site to read
    assert client.post("/api/outreach/companies", json={}).status_code == 422


def test_your_address_is_sent_without_the_review_window(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    update_app_settings(db, AppSettingsPatch(outreach_auto_send=True))
    c = service.add_by_email(db, "hr.recruiter@gmail.com", name="Acme")
    db.commit()
    email = service.queue_company(db, c, lambda *a, **k: None)
    db.add(
        Resume(
            kind=ResumeKind.GENERATED,
            version=1,
            job_id=email.job_id,
            tex_source="x",
            pdf_path="r.pdf",
        )
    )
    db.commit()
    backend.script("writer", _draft_json("I built a REST API in FastAPI and PostgreSQL."))
    service.draft_ready(db, gen_env["deps"].gateway, email.id)
    db.expire_all()
    e = db.get(OutreachEmail, email.id)
    assert e is not None and e.to_address == "hr.recruiter@gmail.com" and e.auto_send_at is not None
    assert e.auto_send_at.replace(tzinfo=UTC) <= datetime.now(UTC)  # due now: sent on the next tick


def test_marketing_and_sales_inboxes_are_never_used() -> None:
    from app.outreach.crawl import emails_in

    got = emails_in(
        "marketing@acme.io sales-india@acme.io support.team@acme.io hr@acme.io", "", "acme.io"
    )
    assert got == ["hr@acme.io"]


def test_resume_file_name_and_sender_name(db: Session) -> None:
    from app.services.settings_service import display_name, resume_filename

    assert resume_filename(db) == "Resume.pdf" and display_name(db) == ""  # nothing set
    update_app_settings(db, AppSettingsPatch(resume_file_name="Abhiram"))
    assert resume_filename(db) == "Abhiram.pdf" and display_name(db) == "Abhiram"
    update_app_settings(db, AppSettingsPatch(resume_file_name="Challa Abhiram Resume.pdf"))
    assert resume_filename(db) == "Challa_Abhiram_Resume.pdf"
    set_profile(db, Profile(full_name="Challa Abhiram"))
    assert display_name(db) == "Challa Abhiram"  # your profile name wins for the sender


def test_download_uses_your_file_name(client: Any, db: Session) -> None:
    pdf = service.data_root() / "resumes" / "dl.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(make_pdf(1, body="resume"))
    r = Resume(kind=ResumeKind.GENERATED, version=1, tex_source="x", pdf_path="resumes/dl.pdf")
    db.add(r)
    db.commit()
    update_app_settings(db, AppSettingsPatch(resume_file_name="Abhiram"))
    got = client.get(f"/api/resumes/{r.id}/pdf")
    assert got.status_code == 200 and 'filename="Abhiram.pdf"' in got.headers["content-disposition"]


def _gmail_account() -> EmailAccount:
    return EmailAccount(
        provider=EmailProvider.GMAIL_API,
        address="abhi@gmail.com",
        oauth_tokens={
            "access_token": "tok",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )


@pytest.mark.parametrize(
    ("handler", "transient"),
    [
        (lambda r: (_ for _ in ()).throw(httpx.ConnectError("Name or service not known")), True),
        (lambda r: httpx.Response(503, json={"error": {"message": "busy"}}), True),
        (lambda r: httpx.Response(429, json={"error": {"message": "slow down"}}), True),
        (lambda r: httpx.Response(400, json={"error": {"message": "bad recipient"}}), False),
    ],
)
def test_network_problems_are_temporary(
    db: Session, tmp_path: Path, handler: Any, transient: bool
) -> None:
    from app.outreach.send import SendError

    msg = build_message(
        from_name="A",
        from_address="abhi@gmail.com",
        to="x@acme.io",
        subject="s",
        body="b",
        attachment=None,
        attachment_name="Abhiram.pdf",
    )
    with pytest.raises(SendError) as exc:
        send(db, _gmail_account(), msg, transport=httpx.MockTransport(handler))
    assert exc.value.transient is transient


def test_temporary_send_failure_retries_instead_of_failing(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models import Notification
    from app.outreach.send import SendError

    c = _setup_company(db)
    db.add(
        EmailAccount(
            provider=EmailProvider.GMAIL_API,
            address="abhi@gmail.com",
            status=ConnectionStatus.CONNECTED,
        )
    )
    pdf = service.data_root() / "resumes" / "retry.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    pdf.write_bytes(make_pdf(1, body="resume"))
    r = Resume(kind=ResumeKind.GENERATED, version=1, tex_source="x", pdf_path="resumes/retry.pdf")
    db.add(r)
    db.flush()
    e = OutreachEmail(
        company_id=c.id,
        resume_id=r.id,
        to_address="careers@acme.io",
        subject="Hi",
        body="Body",
        status="approved",
        approved_at=datetime.now(UTC),
    )
    db.add(e)
    db.commit()

    def offline(*a: Any) -> str:
        raise SendError(
            "could not reach the sign-in service: [Errno -2] Name or service not known",
            transient=True,
        )

    monkeypatch.setattr(service, "send", offline)
    service.send_approved(db, e.id)
    service.send_approved(db, e.id)
    db.expire_all()
    got = db.get(OutreachEmail, e.id)
    assert (
        got is not None and got.status == "approved" and (got.error or "").startswith("Will retry")
    )
    assert db.query(Notification).filter_by(kind="outreach.retrying").count() == 1  # not spammed

    monkeypatch.setattr(service, "send", lambda *a: "gm-9")  # back online
    service.send_approved(db, e.id)
    db.expire_all()
    got = db.get(OutreachEmail, e.id)
    assert got is not None and got.status == "sent" and got.error is None

    # still failing a day later: give up and tell you
    e2 = OutreachEmail(
        company_id=_setup_company2(db).id,
        resume_id=r.id,
        to_address="jobs@b.io",
        subject="Hi",
        body="Body",
        status="approved",
        approved_at=datetime.now(UTC) - timedelta(hours=25),
    )
    db.add(e2)
    db.commit()
    monkeypatch.setattr(service, "send", offline)
    service.send_approved(db, e2.id)
    db.expire_all()
    assert db.get(OutreachEmail, e2.id).status == "failed"  # type: ignore[union-attr]


def _setup_company2(db: Session) -> OutreachCompany:
    c = OutreachCompany(name="B", domain="b.io", website="https://b.io", status="queued")
    db.add(c)
    db.commit()
    return c


JD_BACKEND = (
    "Backend Engineer Intern at Acme. Required: Python, FastAPI, PostgreSQL. "
    "You will build REST APIs for payments."
)


def test_email_plus_job_description_tailors_resume_and_email(
    client: Any, gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    r = client.post(
        "/api/outreach/companies",
        json={
            "email": "hr.recruiter@gmail.com",
            "name": "Acme",
            "jd_text": JD_BACKEND,
            "role": "Backend Engineer Intern",
        },
    )
    assert r.status_code == 201
    c_out = next(x for x in r.json()["companies"] if x["domain"] == "hr.recruiter@gmail.com")
    assert c_out["has_jd"] and c_out["role"] == "Backend Engineer Intern"

    c = db.get(OutreachCompany, c_out["id"])
    assert c is not None
    email = service.queue_company(db, c, lambda *a, **k: None)
    job = db.get(Job, email.job_id)
    assert job is not None and job.jd_text == JD_BACKEND and job.role == "Backend Engineer Intern"

    job.jd_structured = {
        "role": "Backend Engineer Intern",
        "company": "Acme",
        "required_skills": ["Python", "FastAPI", "PostgreSQL"],
        "eligibility": {},
    }
    db.add(
        Resume(
            kind=ResumeKind.GENERATED, version=1, job_id=job.id, tex_source="x", pdf_path="r.pdf"
        )
    )
    db.commit()
    backend.script("writer", _draft_json("I built a REST API in FastAPI and PostgreSQL."))
    service.draft_ready(db, gen_env["deps"].gateway, email.id)
    prompt = [c[1][-1]["content"] for c in backend.calls if c[0].model == "writer"][-1]
    assert "JOB POSTING" in prompt and "build REST APIs for payments" in prompt
    assert "REQUIREMENTS: Python, FastAPI, PostgreSQL" in prompt
    db.expire_all()
    assert db.get(OutreachEmail, email.id).status == "ready"  # type: ignore[union-attr]


def test_ai_rate_limit_during_research_retries_later_instead_of_failing(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    from app.llm.errors import ErrorKind, LLMProviderError

    site = {
        "https://acme.io/robots.txt": (404, ""),
        "https://acme.io/": (200, "<p>Payments. careers@acme.io</p>"),
    }
    c = OutreachCompany(name="Acme", domain="acme.io", website="https://acme.io/", status="new")
    db.add(c)
    db.commit()
    backend.script("jd", *[LLMProviderError(ErrorKind.RATE_LIMIT, "429 quota")] * 2)
    service.research_company(
        db, gen_env["deps"].gateway, c.id, SiteReader(_site(site), pause_s=0), None
    )
    db.expire_all()
    got = db.get(OutreachCompany, c.id)
    assert got is not None and got.status == "new" and (got.error or "").startswith("Will retry")

    picked: list[int] = []
    orig = service.research_company
    service.research_company = lambda db_, gw, cid, *a: picked.append(cid)  # type: ignore[assignment]
    try:
        service.process_outreach(
            gen_env["deps"].gateway,
            gen_env["maker"],
            lambda *a, **k: None,
            reader=SiteReader(_site({}), pause_s=0),
            search=FakeSearch([]),
        )
    finally:
        service.research_company = orig  # type: ignore[assignment]
    assert picked == []  # waits 30 minutes before trying again


def test_closing_note_can_be_turned_off(client: Any, db: Session) -> None:
    assert client.patch("/api/settings", json={"outreach_closing_note": ""}).status_code == 200
    c = _setup_company(db)
    e = OutreachEmail(
        company_id=c.id, to_address="careers@acme.io", subject="S", body="B", status="ready"
    )
    db.add(e)
    db.commit()
    r = client.patch(f"/api/outreach/emails/{e.id}", json={"body": "Just my own words."})
    body = r.json()["companies"][0]["email"]["body"]
    assert body == "Just my own words." and "AutoApply Agent" not in body


def test_pick_role_fits_the_company() -> None:
    from app.outreach.draft import CompanyResearch, OpenRole
    from app.outreach.service import nice_role, pick_role

    roles = ["software engineer", "backend engineer", "AI engineer", "full stack developer"]
    skip = ["senior", "lead"]
    ai = CompanyResearch(what_they_do="Builds LLM agents for customer support", tech=["PyTorch"])
    assert pick_role(roles, ai, skip) == "AI Engineer"
    api = CompanyResearch(
        what_they_do="API management platform for enterprises", tech=["Kubernetes"]
    )
    assert pick_role(roles, api, skip) == "Backend Engineer"
    web = CompanyResearch(
        what_they_do="An e-commerce marketplace web app", tech=["React", "Next.js"]
    )
    assert pick_role(roles, web, skip) == "Full Stack Developer"
    ai_first = CompanyResearch(
        what_they_do="An AI-powered platform that turns ideas into full-stack web apps",
        tech=["GitHub integration", "Self hosted database", "VPC setup"],
    )
    assert pick_role(roles, ai_first, skip) == "AI Engineer"
    oolka = CompanyResearch(what_they_do="An AI\u2011driven platform for credit scores", tech=["payments"])
    assert pick_role(roles, oolka, skip) == "AI Engineer"  # non-breaking hyphen
    vague = CompanyResearch(what_they_do="We help people")
    assert pick_role(roles, vague, skip) == "Software Engineer"
    # a matching opening on their own site wins; a senior one doesn't
    opening = CompanyResearch(
        what_they_do="LLM agents",
        open_roles=[
            OpenRole(title="Senior Backend Engineer"),
            OpenRole(title="Backend Engineer - Payments"),
        ],
    )
    assert pick_role(roles, opening, skip) == "Backend Engineer - Payments"
    assert nice_role("ml engineer") == "ML Engineer"
