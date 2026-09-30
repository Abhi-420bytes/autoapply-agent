"""LinkedIn alerts: one job per posting, the public JD, and the company's own apply page."""

# ruff: noqa: F811  (the mail_env fixture is imported from test_email_ingest)
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from app.boards import service
from app.boards.linkedin import Posting, alert_postings, find_on_company_site, parse_posting
from app.email.ingest import poll_account, process_due_jobs
from app.generation.runs import after_generation
from app.llm.gateway import LLMGateway
from app.models import Job, Notification, Portal, SenderRule
from app.models.enums import ApplyMode, JobStatus
from app.search.web import SearchResult
from tests.fakes import FakeBackend
from tests.test_email_ingest import FakeProvider, mail_env  # noqa: F401 (fixture)
from tests.test_email_parsing import mime

ALERT = """
<table>
 <tr><td><a href="https://www.linkedin.com/comm/jobs/view/4470307739/?trackingId=abc&refId=x">
   Back End Developer</a><p>The Alter Office · Bengaluru</p></td></tr>
 <tr><td><a href="https://www.linkedin.com/comm/jobs/view/4470307739/?trk=img"><img></a></td></tr>
 <tr><td><a href="https://www.linkedin.com/comm/jobs/view/4471025490/?trackingId=def">
   Software Engineer</a></td></tr>
 <tr><td><a href="https://www.linkedin.com/comm/jobs/view/4472259801/">Python Developer</a></td></tr>
 <tr><td><a href="https://www.linkedin.com/comm/psettings/email-unsubscribe">Unsubscribe</a></td></tr>
</table>"""

PAGE = """<html><h1 class="top-card-layout__title font-sans">Back End Developer</h1>
<a class="topcard__org-name-link topcard__flavor--black-link" href="#">
  The Alter Office </a>
<span class="topcard__flavor topcard__flavor--bullet"> Bengaluru, Karnataka, India </span>
<div class="show-more-less-html__markup relative"><p>We build an AdTech platform.</p>
<ul><li>Python and FastAPI services</li><li>PostgreSQL at scale</li></ul></div></html>"""


def test_alert_lists_each_posting_once_with_its_title() -> None:
    got = alert_postings(ALERT, "")
    assert [(p.job_id, p.title) for p in got] == [
        ("4470307739", "Back End Developer"),
        ("4471025490", "Software Engineer"),
        ("4472259801", "Python Developer"),
    ]


def test_public_posting_page_is_parsed() -> None:
    p = parse_posting("4470307739", PAGE)
    assert p is not None
    assert (p.title, p.company, p.location) == (
        "Back End Developer",
        "The Alter Office",
        "Bengaluru, Karnataka, India",
    )
    assert "- Python and FastAPI services" in p.description
    assert parse_posting("1", "<html>sign in to view</html>") is None


class FakeSearch:
    def __init__(self, results: list[SearchResult]) -> None:
        self.results = results
        self.queries: list[str] = []

    def search(
        self, query: str, *, count: int = 10, country: str | None = None
    ) -> list[SearchResult]:
        self.queries.append(query)
        return self.results


POSTING = Posting("1", "Back End Developer", "The Alter Office", "Bengaluru", "desc")


@pytest.mark.parametrize(
    ("url", "title", "expect"),
    [
        # known applicant-tracking system naming the company: trusted
        (
            "https://jobs.lever.co/thealteroffice/123",
            "The Alter Office - Back End Developer",
            ("https://jobs.lever.co/thealteroffice/123", True),
        ),
        # the company's own careers site: found, but needs the user's OK
        (
            "https://careers.thealteroffice.com/jobs/backend",
            "Back End Developer | Careers",
            ("https://careers.thealteroffice.com/jobs/backend", False),
        ),
        # job boards are never the company's page
        (
            "https://www.naukri.com/job/alter-office-backend",
            "Back End Developer - The Alter Office",
            None,
        ),
        # a different role at the company is not this opening
        ("https://jobs.lever.co/thealteroffice/9", "The Alter Office - Sales Manager", None),
        # an aggregator that only mentions the company in its path is not the company
        (
            "https://bebee.com/in/jobs/back-end-developer-the-alter-office",
            "Back End Developer - The Alter Office",
            None,
        ),
        # an ATS page for another company
        ("https://boards.greenhouse.io/othercorp/jobs/1", "Back End Developer", None),
    ],
)
def test_company_site_match_is_verified(url: str, title: str, expect: Any) -> None:
    found = find_on_company_site(FakeSearch([SearchResult(url, title, "")]), POSTING)  # type: ignore[arg-type]
    assert (None if found is None else (found.url, found.trusted)) == expect


def _linkedin_rule(db: Session, mail_env: dict[str, Any]) -> None:
    db.add(
        SenderRule(
            email_account_id=mail_env["gmail"].id,
            sender_match="jobalerts-noreply@linkedin.com",
            apply_mode=ApplyMode.READ_EMAIL_THEN_APPLY,
            delay_minutes=0,
        )
    )
    db.commit()


def test_alert_email_becomes_one_job_per_posting_and_follows_to_company_site(
    db: Session,
    gateway: LLMGateway,
    backend: FakeBackend,
    mail_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _linkedin_rule(db, mail_env)
    fresh = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S +0000")
    raw = mime(
        html=ALERT, text="jobs", sender="LinkedIn <jobalerts-noreply@linkedin.com>", date=fresh
    )
    rep = poll_account(
        db,
        gateway,
        mail_env["gmail"],
        FakeProvider({"a": ("jobalerts-noreply@linkedin.com", raw)}),
        None,
    )
    assert rep.jobs_created == 3
    assert not [c for c in backend.calls if c[0].model == "cls"]  # no LLM needed for alerts
    jobs = db.query(Job).order_by(Job.id).all()
    assert [j.dedupe_key for j in jobs] == [
        "linkedin:4470307739",
        "linkedin:4471025490",
        "linkedin:4472259801",
    ]
    # the same alert again (e.g. a reminder) creates nothing new
    raw2 = mime(
        html=ALERT,
        text="jobs",
        sender="LinkedIn <jobalerts-noreply@linkedin.com>",
        date=fresh,
        mid="<other@x>",
    )
    poll_account(
        db,
        gateway,
        mail_env["gmail"],
        FakeProvider({"b": ("jobalerts-noreply@linkedin.com", raw2)}),
        None,
    )
    assert db.query(Job).count() == 3

    def page(request: httpx.Request) -> httpx.Response:
        if "4470307739" in str(request.url):
            return httpx.Response(200, text=PAGE)
        return httpx.Response(200, text="<html>sign in</html>")  # not public: fall back

    search = FakeSearch(
        [
            SearchResult(
                "https://jobs.lever.co/thealteroffice/123",
                "The Alter Office - Back End Developer",
                "",
            )
        ]
    )
    real = service.prepare_linkedin_job
    monkeypatch.setattr(
        "app.email.ingest.prepare_linkedin_job",
        lambda db_, jid: real(
            db_,
            jid,
            http=httpx.Client(transport=httpx.MockTransport(page)),
            search=search,  # type: ignore[arg-type]
            pause_s=0,
        ),
    )
    for j in jobs:
        j.scheduled_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    queued: list[int] = []
    process_due_jobs(db, lambda _db, j, **kw: queued.append(j.id), None)
    assert queued == [j.id for j in jobs]

    db.expire_all()
    first, second = db.get(Job, jobs[0].id), db.get(Job, jobs[1].id)
    assert first is not None and second is not None
    assert first.company == "The Alter Office" and "PostgreSQL at scale" in (first.jd_text or "")
    assert first.apply_url == "https://jobs.lever.co/thealteroffice/123"
    portal = db.get(Portal, first.portal_id)
    assert portal is not None and portal.allowed_domains == ["*.lever.co"]
    # posting page not public: resume from the alert email; apply yourself on LinkedIn
    assert second.portal_id is None and second.apply_url.startswith("https://www.linkedin.com/")
    assert "Software Engineer" in (second.jd_text or "")

    second.status = JobStatus.SCRAPED
    db.commit()
    after_generation(db, second.id)
    db.expire_all()
    second = db.get(Job, jobs[1].id)
    assert second is not None and second.status is JobStatus.RESUME_READY
    note = db.query(Notification).filter_by(kind="job.ready_manual").one()
    assert "linkedin.com/jobs/view/4471025490" in (note.body or "")


def test_allowing_a_company_site_lets_the_agent_apply(client: Any, db: Session) -> None:
    job = Job(
        source="email",
        status=JobStatus.RESUME_READY,
        apply_url="https://careers.thealteroffice.com/jobs/backend",
    )
    db.add(job)
    db.commit()
    r = client.post(f"/api/jobs/{job.id}/allow-apply-site")
    assert r.status_code == 200
    db.expire_all()
    job = db.get(Job, job.id)
    assert job is not None and job.portal_id is not None
    assert db.get(Portal, job.portal_id).allowed_domains == ["*.thealteroffice.com"]  # type: ignore[union-attr]

    board = Job(
        source="email",
        status=JobStatus.RESUME_READY,
        apply_url="https://www.linkedin.com/jobs/view/1/",
    )
    db.add(board)
    db.commit()
    assert client.post(f"/api/jobs/{board.id}/allow-apply-site").status_code == 409


def test_senior_titles_are_skipped_on_whole_words() -> None:
    from app.boards.linkedin import title_skipped
    from app.schemas.settings import AppSettings

    words = AppSettings().skip_title_words
    assert title_skipped("SSE/TL - Golang Backend", words) == "sse"
    assert title_skipped("Sr. Software Engineer", words) == "sr"
    assert title_skipped("Leadership Program Intern", words) is None
    assert title_skipped("Python Developer", words) is None


def test_pasting_the_company_link_lets_the_agent_apply_and_boards_are_refused(
    client: Any, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models import Application
    from app.models.enums import ApplicationStatus

    job = Job(
        source="email",
        status=JobStatus.RESUME_READY,
        company="Venwiz",
        role="SDE I",
        apply_url="https://www.linkedin.com/jobs/view/4471393126/",
    )
    db.add(job)
    db.flush()
    db.add(Application(job_id=job.id, status=ApplicationStatus.APPROVED))
    db.commit()
    queued: list[int] = []
    monkeypatch.setattr("app.api.jobs.queue_apply", lambda _db, j, app_id: queued.append(app_id))

    r = client.post(f"/api/jobs/{job.id}/apply-link", json={"url": "https://www.naukri.com/job/1"})
    assert r.status_code == 422 and queued == []
    r = client.post(
        f"/api/jobs/{job.id}/apply-link", json={"url": "https://jobs.lever.co/venwiz/abc/apply"}
    )
    assert r.status_code == 200 and r.json()["portal_id"] and len(queued) == 1


def test_search_again_finds_the_company_page(
    client: Any, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = Job(
        source="email",
        status=JobStatus.RESUME_READY,
        company="The Alter Office",
        role="Back End Developer",
        apply_url="https://www.linkedin.com/jobs/view/1/",
    )
    db.add(job)
    db.commit()
    search = FakeSearch(
        [
            SearchResult(
                "https://jobs.lever.co/thealteroffice/123",
                "The Alter Office - Back End Developer",
                "",
            )
        ]
    )
    monkeypatch.setattr("app.api.jobs.make_searcher", lambda *a, **k: search)
    r = client.post(f"/api/jobs/{job.id}/find-apply-page")
    assert r.status_code == 200
    body = r.json()
    assert body["apply_url"] == "https://jobs.lever.co/thealteroffice/123" and body["portal_id"]

    monkeypatch.setattr("app.api.jobs.make_searcher", lambda *a, **k: FakeSearch([]))
    job2 = Job(source="email", status=JobStatus.RESUME_READY, company="Venwiz", role="SDE I")
    db.add(job2)
    db.commit()
    r = client.post(f"/api/jobs/{job2.id}/find-apply-page")
    assert r.status_code == 200 and "No application page" in r.json()["notes"]
