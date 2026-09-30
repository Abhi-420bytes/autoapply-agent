"""Preparing jobs that came from job-board alerts (LinkedIn) for the normal pipeline."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.boards.linkedin import (
    CompanyApply,
    Posting,
    fetch_posting,
    find_on_company_site,
    is_board,
    registrable,
)
from app.models import Job, Portal
from app.models.enums import PortalLoginMethod
from app.search.web import Searcher, SearchError, make_searcher

log = logging.getLogger(__name__)

POLITE_PAUSE_S = 4.0  # between public posting reads, like a person opening links


def ensure_portal(db: Session, url: str) -> Portal:
    """The portal the agent may apply on for this site (created on first use)."""
    host = (urlparse(url).hostname or "").lower()
    domain = registrable(host)
    pattern = f"*.{domain}"
    for p in db.scalars(select(Portal)):
        if pattern in (p.allowed_domains or []) or domain in (p.allowed_domains or []):
            return p
    existing = db.scalar(select(Portal).where(Portal.name == domain))
    if existing is not None:
        existing.allowed_domains = sorted({*(existing.allowed_domains or []), pattern})
        return existing
    portal = Portal(
        name=domain,
        base_url=f"https://{host}",
        allowed_domains=[pattern],
        login_method=PortalLoginMethod.MANUAL,
    )
    db.add(portal)
    db.flush()
    return portal


def prepare_linkedin_job(
    db: Session,
    job_id: int,
    *,
    http: httpx.Client | None = None,
    search: Searcher | None = None,
    pause_s: float = POLITE_PAUSE_S,
) -> None:
    """Read the public posting (full JD) and look for the company's own application page.
    Network calls happen outside any DB transaction."""
    job = db.get(Job, job_id)
    if job is None or not (job.portal_job_ref or "").startswith("linkedin:"):
        return
    linkedin_id = (job.portal_job_ref or "").split(":", 1)[1]
    if search is None:
        from app.llm import get_gateway

        search = make_searcher(db, get_gateway(), job_id=job_id)
    db.commit()

    client = http or httpx.Client()
    try:
        posting: Posting | None = fetch_posting(linkedin_id, client)
    finally:
        if http is None:
            client.close()
    found: CompanyApply | None = None
    no_search = ""
    if posting is not None:
        try:
            found = find_on_company_site(search, posting)
        except SearchError as exc:
            no_search = str(exc)
            log.info("company-site search skipped: %s", exc)
    if pause_s:
        time.sleep(pause_s)

    job = db.get(Job, job_id)
    assert job is not None
    if posting is not None:
        job.company, job.role = posting.company or job.company, posting.title or job.role
        job.location = posting.location or job.location
        job.jd_text = "\n".join(
            x
            for x in (
                f"{posting.title} at {posting.company}",
                posting.location,
                "",
                posting.description,
            )
            if x is not None
        )
    if found is not None:
        _use_found(db, job, found)
    else:
        job.notes = (
            "Apply yourself on LinkedIn with the prepared resume"
            + (f" (company-site search didn't run: {no_search[:160]})" if no_search else "")
            + (
                ""
                if posting is not None
                else ". The public LinkedIn page couldn't be read, so the resume is based "
                "on the alert email"
            )
            + "."
        )
    db.commit()


def _use_found(db: Session, job: Job, found: CompanyApply) -> None:
    job.apply_url = found.url
    if found.trusted:
        job.portal_id = ensure_portal(db, found.url).id
        job.notes = f"Company application page (found via search): {found.url}"
    else:
        job.notes = (
            f"Found this opening on {urlparse(found.url).hostname}. Allow that site on the job "
            "page so the agent can apply there, or apply yourself."
        )


def search_apply_page(db: Session, job_id: int, search: Searcher) -> bool:
    """Look (again) for this job's application page on the company's own site."""
    job = db.get(Job, job_id)
    if job is None or not (job.company and job.role):
        return False
    posting = Posting(job.portal_job_ref or "", job.role, job.company, job.location or "", "")
    db.commit()
    found = find_on_company_site(search, posting)  # SearchError propagates to the caller
    job = db.get(Job, job_id)
    assert job is not None
    if found is None:
        job.notes = (
            f"No application page for this opening was found on {job.company}'s own site; "
            "apply yourself (on LinkedIn for LinkedIn jobs), or paste the link if you know it."
        )
    else:
        _use_found(db, job, found)
    db.commit()
    return found is not None


def set_apply_link(db: Session, job: Job, url: str) -> Portal:
    """You pasted the company's application page: that's your OK for the agent to use it."""
    job.apply_url = url.strip()
    return allow_apply_site(db, job)


def allow_apply_site(db: Session, job: Job) -> Portal:
    """User OK: let the agent open this job's (non-ATS) company site and apply there."""
    url = job.apply_url or ""
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname or is_board(u.hostname):
        raise ValueError("this job has no company application page to allow")
    portal = ensure_portal(db, url)
    job.portal_id = portal.id
    job.notes = f"Company application page (allowed by you): {url}"
    return portal
