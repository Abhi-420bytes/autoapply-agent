"""Stage 1: searching a job website.

open search URL(s) → collect links (allowlisted domain only) → pick job postings
→ category filter (exclude words, then literal match, then the model for near-matches)
→ record every posting (with a skip reason) → matching new postings become Jobs,
scheduled after the site's apply delay.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urldefrag

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.email.ingest import dedupe_key
from app.knowledge.skills import normalize
from app.llm.errors import LLMError
from app.llm.gateway import LLMGateway
from app.models import DiscoveredPosting, Job, JobSite
from app.models.enums import ApplyMode, JobSource, JobStatus, LLMTask

log = logging.getLogger(__name__)
MAX_LINKS = 120
_JUNK = (
    "login",
    "signin",
    "sign-in",
    "signup",
    "register",
    "logout",
    "help",
    "privacy",
    "terms",
    "cookie",
    "about",
    "contact",
    "settings",
    "notifications",
    "messaging",
    "feed",
    "profile",
    "premium",
    "learning",
    "#",
)

COLLECT_JS = """
els => els.map(a => {
  const card = a.closest('li, article, [role=listitem], .job, .card, tr, div');
  return {href: a.href, text: (a.innerText || a.getAttribute('aria-label') || '').trim(),
          context: card ? card.innerText.trim().slice(0, 300) : ''};
})
"""


@dataclass
class Link:
    url: str
    text: str
    context: str


@dataclass
class Posting:
    url: str
    title: str
    company: str | None = None
    snippet: str | None = None


class PickedPosting(BaseModel):
    index: int
    title: str
    company: str | None = None


class PickedPostings(BaseModel):
    postings: list[PickedPosting] = Field(default_factory=list)


class CategoryVerdict(BaseModel):
    index: int
    category: str | None = Field(default=None, description="exact category name, or null")


class CategoryVerdicts(BaseModel):
    verdicts: list[CategoryVerdict] = Field(default_factory=list)


def clean_links(raw: list[dict[str, Any]], allowed: Any) -> list[Link]:
    seen: set[str] = set()
    out: list[Link] = []
    for r in raw:
        url = urldefrag(str(r.get("href") or ""))[0]
        if not url.startswith(("http://", "https://")) or url in seen or not allowed(url):
            continue
        lower = url.lower()
        if any(f"/{j}" in lower for j in _JUNK if j != "#"):
            continue
        seen.add(url)
        out.append(
            Link(
                url=url,
                text=" ".join(str(r.get("text") or "").split())[:200],
                context=" ".join(str(r.get("context") or "").split())[:300],
            )
        )
    return out[:MAX_LINKS]


def pick_postings(gateway: LLMGateway, links: list[Link], use_all: bool) -> list[Posting]:
    """Which links are individual job postings. With a configured job_link selector every
    link already is one; otherwise the portal_helper model decides."""
    if use_all or not links:
        return [
            Posting(url=lk.url, title=lk.text or lk.context[:120], snippet=lk.context)
            for lk in links
            if lk.text or lk.context
        ]
    listing = "\n".join(
        f"[{i}] {lk.text} | {lk.url} | {lk.context[:160]}" for i, lk in enumerate(links)
    )
    result = gateway.structured(
        LLMTask.PORTAL_HELPER, "job_links", {"links": listing}, PickedPostings
    ).value
    out: list[Posting] = []
    for p in result.postings:
        if 0 <= p.index < len(links):
            lk = links[p.index]
            out.append(
                Posting(
                    url=lk.url,
                    title=p.title.strip()[:300] or lk.text,
                    company=p.company,
                    snippet=lk.context,
                )
            )
    return out


def _literal_match(title: str, categories: list[str]) -> str | None:
    t = f" {normalize(title)} "
    for c in categories:
        words = [w for w in normalize(c).split() if len(w) > 1]
        if words and all(f" {w} " in t for w in words):
            return c
    return None


@dataclass
class Verdict:
    matched: bool
    category: str | None = None
    reason: str | None = None


def match_categories(
    gateway: LLMGateway, postings: list[Posting], categories: list[str], exclude: list[str]
) -> list[Verdict]:
    verdicts: list[Verdict] = []
    pending: list[int] = []
    for i, p in enumerate(postings):
        text = f"{p.title} {p.snippet or ''}"
        hit = next(
            (w for w in exclude if w.strip() and f" {normalize(w)} " in f" {normalize(text)} "),
            None,
        )
        if hit:
            verdicts.append(Verdict(False, reason=f"contains excluded word '{hit}'"))
        elif not categories:
            verdicts.append(Verdict(True, reason="no category filter set"))
        elif cat := _literal_match(p.title, categories):
            verdicts.append(Verdict(True, category=cat))
        else:
            verdicts.append(Verdict(False, reason="not in your categories"))
            pending.append(i)
    if pending and categories:  # near-matches, e.g. "ML Engineer" for "Machine Learning"
        listing = "\n".join(f"[{i}] {postings[i].title}" for i in pending)
        try:
            out = gateway.structured(
                LLMTask.PORTAL_HELPER,
                "category_match",
                {"titles": listing, "categories": categories},
                CategoryVerdicts,
            ).value
        except LLMError as exc:
            log.warning("category matching fell back to literal only: %s", exc)
            return verdicts
        allowed = {c.lower(): c for c in categories}
        for v in out.verdicts:
            if v.index in pending and v.category and v.category.lower() in allowed:
                verdicts[v.index] = Verdict(True, category=allowed[v.category.lower()])
    return verdicts


@dataclass
class DiscoverReport:
    pages: int = 0
    links: int = 0
    postings: int = 0
    new: int = 0
    matched: int = 0
    jobs_created: int = 0
    skipped: int = 0
    deferred: int = 0  # matches over the per-check limit, left for next time
    errors: list[str] = field(default_factory=list)


def record(
    db: Session,
    site: JobSite,
    postings: list[Posting],
    verdicts: list[Verdict],
    report: DiscoverReport,
) -> None:
    now = datetime.now(UTC)
    for p, v in zip(postings, verdicts, strict=True):
        if db.scalar(
            select(DiscoveredPosting.id).where(
                DiscoveredPosting.job_site_id == site.id, DiscoveredPosting.url == p.url
            )
        ):
            continue  # seen on an earlier check
        if v.matched and report.jobs_created >= site.max_new_per_check:
            report.deferred += 1  # not recorded, so the next check picks it up
            continue
        report.new += 1
        row = DiscoveredPosting(
            job_site_id=site.id,
            url=p.url,
            title=p.title,
            company=p.company,
            snippet=p.snippet,
            matched=v.matched,
            matched_category=v.category,
            skip_reason=v.reason,
        )
        db.add(row)
        if not v.matched:
            report.skipped += 1
            continue
        report.matched += 1
        key = dedupe_key(site.portal_id, p.url, None, None)
        existing = db.scalar(select(Job).where(Job.dedupe_key == key))
        if existing is not None:  # e.g. the same job already came in by email
            row.job_id = existing.id
            row.skip_reason = "already tracked (from email or another site)"
            continue
        job = Job(
            source=JobSource.SITE,
            status=JobStatus.DETECTED,
            company=p.company,
            role=p.title,
            portal_id=site.portal_id,
            job_site_id=site.id,
            apply_url=p.url,
            apply_mode=ApplyMode.DIRECT_LINK,
            dedupe_key=key,
            detected_at=now,
            scheduled_at=now,
        )
        db.add(job)
        db.flush()
        row.job_id = job.id
        report.jobs_created += 1
    db.commit()


def polite_pause() -> None:
    """Human-paced navigation between result pages (sites rate-limit bots)."""
    time.sleep(random.uniform(1.5, 3.5))  # noqa: S311 — jitter, not crypto
