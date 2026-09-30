from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_config
from app.db.session import get_db
from app.email.links import host_allowed
from app.models import DiscoveredPosting, Job, JobSite, Portal, Setting
from app.models.enums import AuditActor, PortalLoginMethod, SiteMode
from app.portal.agent import SITE_PROMPT_KEY
from app.services.audit import audit

router = APIRouter(prefix="/api/sites", tags=["job sites"])
MIN_CHECK_MINUTES = 15

TOS_WARNINGS = {
    "linkedin.com": (
        "LinkedIn's User Agreement prohibits automated access (bots, scrapers, automated "
        "applying). Using the agent here can get your LinkedIn account restricted. Search "
        "only is strongly recommended; applying is on you."
    ),
    "indeed.com": (
        "Indeed's terms prohibit automated access; your account may be blocked. "
        "Search only is recommended."
    ),
    "naukri.com": (
        "Naukri's terms prohibit automated access; your account may be blocked. "
        "Search only is recommended."
    ),
}


def tos_warning(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    return next((w for d, w in TOS_WARNINGS.items() if host == d or host.endswith("." + d)), None)


class SiteIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    search_urls: list[str] = Field(min_length=1, max_length=10)
    categories: list[str] = Field(default_factory=list, max_length=20)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=30)
    mode: SiteMode = SiteMode.SEARCH_ONLY
    check_every_minutes: int = Field(default=120, ge=MIN_CHECK_MINUTES, le=7 * 24 * 60)
    apply_delay_minutes: int = Field(default=0, ge=0, le=7 * 24 * 60)
    max_new_per_check: int = Field(default=5, ge=1, le=25)
    enabled: bool = True
    portal_id: int | None = None
    acknowledge_risk: bool = False  # required for search & apply on sites that forbid bots

    @field_validator("search_urls")
    @classmethod
    def _urls(cls, v: list[str]) -> list[str]:
        out = []
        for u in v:
            u = u.strip()
            p = urlparse(u)
            if p.scheme != "https" or not p.hostname:
                raise ValueError(f"'{u}' must be an https:// link (a search results page)")
            out.append(u)
        return out

    @field_validator("categories", "exclude_keywords")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        return [x.strip() for x in v if x.strip()][:30]


class PostingOut(BaseModel):
    id: int
    site_id: int
    site_name: str
    url: str
    title: str | None
    company: str | None
    matched: bool
    matched_category: str | None
    skip_reason: str | None
    job_id: int | None
    job_status: str | None
    discovered_at: datetime


class SiteOut(BaseModel):
    id: int
    name: str
    portal_id: int
    portal_name: str
    allowed_domains: list[str]
    has_login: bool
    search_urls: list[str]
    categories: list[str]
    exclude_keywords: list[str]
    mode: SiteMode
    check_every_minutes: int
    apply_delay_minutes: int
    max_new_per_check: int
    enabled: bool
    last_checked_at: datetime | None
    last_error: str | None
    found: int
    matched: int
    jobs: int
    tos_warning: str | None
    prompt: dict[str, Any] | None


def _registrable(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "ac", "com", "org", "edu", "gov", "net"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _portal_for(db: Session, body: SiteIn) -> Portal:
    if body.portal_id is not None:
        portal = db.get(Portal, body.portal_id)
        if portal is None:
            raise HTTPException(404, "portal not found")
    else:
        host = urlparse(body.search_urls[0]).hostname or ""
        portal = next(
            (p for p in db.scalars(select(Portal)) if host_allowed(host, p.allowed_domains)), None
        )
        if portal is None:  # create one scoped to this site's domain
            portal = Portal(
                name=body.name,
                base_url=f"https://{host}",
                allowed_domains=[host, "*." + _registrable(host)],
                login_method=PortalLoginMethod.PASSWORD,
            )
            db.add(portal)
            db.flush()
    for u in body.search_urls:
        if not host_allowed(urlparse(u).hostname or "", portal.allowed_domains):
            raise HTTPException(
                422,
                f"{u} isn't on {portal.name}'s allowed domains "
                f"({', '.join(portal.allowed_domains)})",
            )
    return portal


def _check_risk(body: SiteIn) -> None:
    warning = next((w for u in body.search_urls if (w := tos_warning(u))), None)
    if warning and body.mode is SiteMode.SEARCH_AND_APPLY and not body.acknowledge_risk:
        raise HTTPException(422, warning + " To enable applying anyway, confirm the risk.")


def _prompt(db: Session, site_id: int) -> dict[str, Any] | None:
    row = db.get(Setting, SITE_PROMPT_KEY.format(site_id=site_id))
    if row is None or not row.secret_value:
        return None
    data = json.loads(row.secret_value)
    return (
        None
        if data.get("answer")
        else {"kind": data["kind"], "question": data["question"], "asked_at": data["asked_at"]}
    )


def _out(db: Session, s: JobSite) -> SiteOut:
    counts = dict(
        db.execute(
            select(DiscoveredPosting.matched, func.count())
            .where(DiscoveredPosting.job_site_id == s.id)
            .group_by(DiscoveredPosting.matched)
        )
        .tuples()
        .all()
    )
    jobs = db.scalar(select(func.count(Job.id)).where(Job.job_site_id == s.id)) or 0
    return SiteOut(
        id=s.id,
        name=s.name,
        portal_id=s.portal_id,
        portal_name=s.portal.name,
        allowed_domains=s.portal.allowed_domains,
        has_login=bool(s.portal.credentials),
        search_urls=s.search_urls,
        categories=s.categories,
        exclude_keywords=s.exclude_keywords,
        mode=s.mode,
        check_every_minutes=s.check_every_minutes,
        apply_delay_minutes=s.apply_delay_minutes,
        max_new_per_check=s.max_new_per_check,
        enabled=s.enabled,
        last_checked_at=s.last_checked_at,
        last_error=s.last_error,
        found=sum(counts.values()),
        matched=counts.get(True, 0),
        jobs=jobs,
        tos_warning=next((w for u in s.search_urls if (w := tos_warning(u))), None),
        prompt=_prompt(db, s.id),
    )


def _get(db: Session, site_id: int) -> JobSite:
    s = db.get(JobSite, site_id)
    if s is None:
        raise HTTPException(404, "job site not found")
    return s


@router.get("", response_model=list[SiteOut])
def list_sites(db: Session = Depends(get_db)) -> list[SiteOut]:
    return [_out(db, s) for s in db.scalars(select(JobSite).order_by(JobSite.id))]


@router.post("", response_model=SiteOut, status_code=201)
def add_site(body: SiteIn, db: Session = Depends(get_db)) -> SiteOut:
    _check_risk(body)
    portal = _portal_for(db, body)
    s = JobSite(portal_id=portal.id, **body.model_dump(exclude={"portal_id", "acknowledge_risk"}))
    db.add(s)
    db.flush()
    audit(
        db,
        AuditActor.USER,
        "site.added",
        entity_type="job_site",
        entity_id=s.id,
        details={"name": s.name, "mode": s.mode.value, "risk_acknowledged": body.acknowledge_risk},
    )
    db.commit()
    db.refresh(s)
    return _out(db, s)


@router.put("/{site_id}", response_model=SiteOut)
def edit_site(site_id: int, body: SiteIn, db: Session = Depends(get_db)) -> SiteOut:
    s = _get(db, site_id)
    _check_risk(body)
    portal = _portal_for(db, body.model_copy(update={"portal_id": body.portal_id or s.portal_id}))
    for k, v in body.model_dump(exclude={"portal_id", "acknowledge_risk"}).items():
        setattr(s, k, v)
    s.portal_id = portal.id
    db.commit()
    db.refresh(s)
    return _out(db, s)


@router.delete("/{site_id}", status_code=204)
def delete_site(site_id: int, db: Session = Depends(get_db)) -> Response:
    db.delete(_get(db, site_id))
    db.commit()
    return Response(status_code=204)


@router.post("/{site_id}/check", status_code=202)
def check_now(site_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    s = _get(db, site_id)
    s.last_checked_at = None  # due immediately; the worker picks it up within a minute
    db.commit()
    return {"state": "queued"}


@router.get("/postings", response_model=list[PostingOut])
def postings(
    site_id: int | None = None,
    matched: bool | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[PostingOut]:
    stmt = (
        select(DiscoveredPosting, JobSite.name, Job.status)
        .join(JobSite, JobSite.id == DiscoveredPosting.job_site_id)
        .outerjoin(Job, Job.id == DiscoveredPosting.job_id)
        .order_by(DiscoveredPosting.id.desc())
        .limit(min(max(limit, 1), 500))
    )
    if site_id is not None:
        stmt = stmt.where(DiscoveredPosting.job_site_id == site_id)
    if matched is not None:
        stmt = stmt.where(DiscoveredPosting.matched.is_(matched))
    return [
        PostingOut(
            id=p.id,
            site_id=p.job_site_id,
            site_name=name,
            url=p.url,
            title=p.title,
            company=p.company,
            matched=p.matched,
            matched_category=p.matched_category,
            skip_reason=p.skip_reason,
            job_id=p.job_id,
            job_status=status.value if status else None,
            discovered_at=p.discovered_at,
        )
        for p, name, status in db.execute(stmt).all()
    ]


class PromptAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=200)


@router.post("/{site_id}/prompt", status_code=204)
def answer_prompt(site_id: int, body: PromptAnswer, db: Session = Depends(get_db)) -> Response:
    row = db.get(Setting, SITE_PROMPT_KEY.format(site_id=site_id))
    if row is None or not row.secret_value:
        raise HTTPException(404, "the agent isn't waiting for anything on this site")
    data = json.loads(row.secret_value)
    data["answer"] = body.answer.strip()
    row.secret_value = json.dumps(data)
    db.commit()
    return Response(status_code=204)


@router.get("/{site_id}/prompt/screenshot")
def prompt_screenshot(site_id: int, db: Session = Depends(get_db)) -> FileResponse:
    row = db.get(Setting, SITE_PROMPT_KEY.format(site_id=site_id))
    rel = json.loads(row.secret_value).get("screenshot_path") if row and row.secret_value else None
    root = Path(get_config().data_dir).resolve()
    path = (root / rel).resolve() if rel else None
    if path is None or not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "no screenshot")
    return FileResponse(path, media_type="image/png")
