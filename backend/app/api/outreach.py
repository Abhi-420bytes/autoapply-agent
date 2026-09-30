"""Cold-email outreach: companies, drafts, and your Send/Skip decisions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.db.session import get_db
from app.llm import get_gateway
from app.models import LLMProvider, OutreachCompany, OutreachEmail, OutreachSource
from app.models.enums import AuditActor, LLMProviderKind
from app.outreach.directory import read_directory
from app.outreach.draft import disclosure
from app.outreach.service import (
    add_by_email,
    add_company,
    run_discovery,
    send_approved,
    sent_last_day,
    set_user_email,
)
from app.search.web import SearchError, make_searcher
from app.services.audit import audit
from app.services.settings_service import display_name, get_app_settings, get_secret

router = APIRouter(prefix="/api/outreach", tags=["outreach"])


class EmailOut(BaseModel):
    id: int
    company_id: int
    company: str
    to_address: str
    subject: str | None
    body: str | None
    status: str
    warnings: list[str]
    resume_id: int | None
    job_id: int | None
    error: str | None
    sent_at: datetime | None
    auto_send_at: datetime | None
    created_at: datetime


class CompanyOut(BaseModel):
    id: int
    name: str
    domain: str
    website: str
    location: str | None
    size: str | None
    careers_url: str | None
    source: str
    status: str
    summary: dict[str, Any] | None
    emails: list[dict[str, Any]]
    error: str | None
    email: EmailOut | None


class Overview(BaseModel):
    companies: list[CompanyOut]
    sent_last_24h: int
    daily_cap: int
    search_ready: bool


def _email_out(e: OutreachEmail, company: str) -> EmailOut:
    return EmailOut(
        id=e.id,
        company_id=e.company_id,
        company=company,
        to_address=e.to_address,
        subject=e.subject,
        body=e.body,
        status=e.status,
        warnings=list(e.warnings or []),
        resume_id=e.resume_id,
        job_id=e.job_id,
        error=e.error,
        sent_at=e.sent_at,
        auto_send_at=e.auto_send_at,
        created_at=e.created_at,
    )


def _gemini_ready(db: Session) -> bool:
    return bool(
        db.scalar(
            select(LLMProvider.id).where(
                LLMProvider.kind == LLMProviderKind.GEMINI, LLMProvider.enabled.is_(True)
            )
        )
    )


@router.get("", response_model=Overview)
def overview(db: Session = Depends(get_db)) -> Overview:
    emails = {e.company_id: e for e in db.scalars(select(OutreachEmail))}
    companies = [
        CompanyOut(
            id=c.id,
            name=c.name,
            domain=c.domain,
            website=c.website,
            location=c.location,
            size=c.size,
            careers_url=c.careers_url,
            source=c.source,
            status=c.status,
            summary=c.summary,
            emails=list(c.emails or []),
            error=c.error,
            email=_email_out(emails[c.id], c.name) if c.id in emails else None,
        )
        for c in db.scalars(select(OutreachCompany).order_by(OutreachCompany.id.desc()))
    ]
    return Overview(
        companies=companies,
        sent_last_24h=sent_last_day(db),
        daily_cap=get_app_settings(db).outreach_daily_cap,
        search_ready=bool(get_secret(db, "tavily_api_key")) or _gemini_ready(db),
    )


class CompanyIn(BaseModel):
    """A company website, or just an email address (or both)."""

    website: str | None = Field(default=None, max_length=500)
    name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None  # optional with a website: the agent finds one itself


class CompanyEmailIn(BaseModel):
    email: EmailStr


@router.post("/companies", response_model=Overview, status_code=201)
def create_company(body: CompanyIn, db: Session = Depends(get_db)) -> Overview:
    website = (body.website or "").strip()
    if not website and not body.email:
        raise HTTPException(422, "enter a company website or an email address")
    try:
        if website:
            c = add_company(db, website, name=body.name)
            if body.email:
                set_user_email(db, c, str(body.email))
        else:
            add_by_email(db, str(body.email), name=body.name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    db.commit()
    return overview(db)


@router.put("/companies/{company_id}/email", response_model=Overview)
def put_company_email(
    company_id: int, body: CompanyEmailIn, db: Session = Depends(get_db)
) -> Overview:
    c = _company(db, company_id)
    set_user_email(db, c, str(body.email))
    db.commit()
    return overview(db)


def _company(db: Session, company_id: int) -> OutreachCompany:
    c = db.get(OutreachCompany, company_id)
    if c is None:
        raise HTTPException(404, "company not found")
    return c


@router.post("/companies/{company_id}/retry", response_model=Overview)
def retry_company(company_id: int, db: Session = Depends(get_db)) -> Overview:
    c = _company(db, company_id)
    c.status, c.error = "new", None
    db.commit()
    return overview(db)


@router.post("/companies/{company_id}/skip", response_model=Overview)
def skip_company(company_id: int, db: Session = Depends(get_db)) -> Overview:
    c = _company(db, company_id)
    c.status = "skipped"
    e = db.scalar(select(OutreachEmail).where(OutreachEmail.company_id == c.id))
    if e is not None and e.status not in ("sent",):
        e.status = "skipped"
    db.commit()
    return overview(db)


@router.delete("/companies/{company_id}", status_code=204)
def delete_company(company_id: int, db: Session = Depends(get_db)) -> Response:
    c = _company(db, company_id)
    e = db.scalar(select(OutreachEmail).where(OutreachEmail.company_id == c.id))
    if e is not None and e.status == "sent":
        raise HTTPException(409, "an email was already sent to this company; skip it instead")
    db.delete(c)
    db.commit()
    return Response(status_code=204)


@router.post("/search", response_model=Overview)
async def search_now(db: Session = Depends(get_db)) -> Overview:
    if not (get_secret(db, "tavily_api_key") or _gemini_ready(db)):
        raise HTTPException(409, "add a Tavily API key in Settings → Outreach first")
    s = get_app_settings(db)
    if not s.outreach_locations or not s.outreach_roles:
        raise HTTPException(409, "set your locations and roles first")

    def run() -> int:
        with sessionmaker(bind=db.get_bind(), expire_on_commit=False)() as sdb:
            gw = get_gateway()
            return run_discovery(sdb, make_searcher(sdb, gw), force=True, gateway=gw)

    try:
        await run_in_threadpool(run)
    except SearchError as exc:
        raise HTTPException(502, str(exc)) from None
    db.expire_all()
    return overview(db)


def _email(db: Session, email_id: int) -> OutreachEmail:
    e = db.get(OutreachEmail, email_id)
    if e is None:
        raise HTTPException(404, "email not found")
    return e


class EmailEdit(BaseModel):
    to_address: EmailStr | None = None
    subject: str | None = Field(default=None, min_length=1, max_length=200)
    body: str | None = Field(default=None, min_length=10, max_length=10_000)


@router.patch("/emails/{email_id}", response_model=Overview)
def edit_email(email_id: int, body: EmailEdit, db: Session = Depends(get_db)) -> Overview:
    e = _email(db, email_id)
    if e.status not in ("ready", "failed"):
        raise HTTPException(409, f"can't edit an email that is {e.status}")
    if body.to_address is not None:
        e.to_address = str(body.to_address)
    if body.subject is not None:
        e.subject = body.subject
    if body.body is not None:
        text = body.body.rstrip()
        line = disclosure(display_name(db), get_app_settings(db).outreach_closing_note)
        if line not in text:  # your rule: every email says it was sent by your agent
            text += "\n\n—\n" + line
        e.body = text
    db.commit()
    return overview(db)


@router.post("/emails/{email_id}/send", response_model=Overview)
async def send_email(email_id: int, db: Session = Depends(get_db)) -> Overview:
    """Your approval. Sends now unless today's cap is reached (then within 24 hours)."""
    e = _email(db, email_id)
    if e.status not in ("ready", "failed"):
        raise HTTPException(409, f"this email is {e.status}")
    if not (e.subject and e.body):
        raise HTTPException(409, "the draft isn't ready yet")
    e.status, e.approved_at, e.error = "approved", datetime.now(UTC), None
    audit(db, AuditActor.USER, "outreach.approved", entity_type="outreach_email", entity_id=e.id)
    db.commit()

    def run() -> None:
        with sessionmaker(bind=db.get_bind(), expire_on_commit=False)() as sdb:
            send_approved(sdb, email_id)

    await run_in_threadpool(run)
    db.expire_all()
    return overview(db)


@router.post("/emails/{email_id}/skip", response_model=Overview)
def skip_email(email_id: int, db: Session = Depends(get_db)) -> Overview:
    e = _email(db, email_id)
    if e.status == "sent":
        raise HTTPException(409, "already sent")
    e.status = "skipped"
    db.commit()
    return overview(db)


# -- startup directories ------------------------------------------------------------------


class SourceOut(BaseModel):
    id: int
    url: str
    label: str | None
    location: str | None
    enabled: bool
    last_read_at: datetime | None
    entries_found: int
    added_total: int
    error: str | None


class SourceIn(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    label: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)


def _sources(db: Session) -> list[SourceOut]:
    return [
        SourceOut(
            id=x.id,
            url=x.url,
            label=x.label,
            location=x.location,
            enabled=x.enabled,
            last_read_at=x.last_read_at,
            entries_found=x.entries_found,
            added_total=x.added_total,
            error=x.error,
        )
        for x in db.scalars(select(OutreachSource).order_by(OutreachSource.id))
    ]


@router.get("/sources", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db)) -> list[SourceOut]:
    return _sources(db)


@router.post("/sources", response_model=list[SourceOut], status_code=201)
async def add_source(body: SourceIn, db: Session = Depends(get_db)) -> list[SourceOut]:
    """Add a startup directory; it's read once now so you can see how many startups it has."""
    url = body.url.strip()
    if "://" not in url:
        url = "https://" + url
    if not urlparse(url).hostname:
        raise HTTPException(422, "enter the directory's web address")
    src = db.scalar(select(OutreachSource).where(OutreachSource.url == url))
    if src is None:
        src = OutreachSource(url=url, label=body.label, location=body.location)
        db.add(src)
    db.commit()
    got = await run_in_threadpool(read_directory, url)
    src.last_read_at, src.error, src.entries_found = (
        datetime.now(UTC),
        got.blocked,
        len(got.entries),
    )
    if not got.blocked and not got.entries:
        src.error = "no startups with websites were found on this page"
    db.commit()
    return _sources(db)


@router.delete("/sources/{source_id}", response_model=list[SourceOut])
def remove_source(source_id: int, db: Session = Depends(get_db)) -> list[SourceOut]:
    src = db.get(OutreachSource, source_id)
    if src is None:
        raise HTTPException(404, "directory not found")
    db.delete(src)
    db.commit()
    return _sources(db)
