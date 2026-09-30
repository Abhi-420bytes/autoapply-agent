from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_config
from app.db.session import get_db
from app.email import oauth
from app.email.oauth import OAuthError, TokenStore
from app.email.providers import EmailError, provider_for
from app.models import EmailAccount, Portal, SenderRule, Setting, SourceEmail
from app.models.enums import ApplyMode, AuditActor, ConnectionStatus, EmailProvider
from app.security.crypto import mask_secret
from app.services.audit import audit

router = APIRouter(prefix="/api/email", tags=["email"])
SYNC_REQUEST_PREFIX = "email.sync_request."


_ADDRESS_RE = re.compile(r"^[a-z0-9._%+'-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
_DOMAIN_RE = re.compile(r"^@?[a-z0-9-]+(\.[a-z0-9-]+)+$")


def normalize_sender_match(v: str) -> str:
    """An email address (jobs@haveloc.com) or a domain (@haveloc.com). Not a URL."""
    v = v.strip().lower()
    if "://" in v or "/" in v or "?" in v:
        raise ValueError(
            "that looks like a web link. A sender rule is the email address the notices come "
            "FROM (e.g. noreply@haveloc.com) or its domain (@haveloc.com). Put the portal's web "
            "address under Settings → Portals instead."
        )
    if _ADDRESS_RE.match(v):
        return v
    if _DOMAIN_RE.match(v):
        return v if v.startswith("@") else "@" + v
    raise ValueError("use an email address (jobs@haveloc.com) or a domain (@haveloc.com)")


class RuleOut(BaseModel):
    id: int
    sender_match: str
    portal_id: int | None
    apply_mode: ApplyMode
    delay_minutes: int | None
    match_forwarded: bool
    enabled: bool


class AccountOut(BaseModel):
    id: int
    provider: EmailProvider
    address: str
    display_name: str | None
    imap_host: str | None
    imap_port: int | None
    imap_password_masked: str | None
    oauth_connected: bool
    status: ConnectionStatus
    last_sync_at: datetime | None
    last_error: str | None
    enabled: bool
    rules: list[RuleOut]


class AccountIn(BaseModel):
    provider: EmailProvider
    address: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    display_name: str | None = Field(default=None, max_length=120)
    imap_host: str | None = Field(default=None, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_password: str | None = Field(default=None, max_length=1024)

    @field_validator("address")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()


class AccountPatch(BaseModel):
    display_name: str | None = None
    imap_host: str | None = None
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_password: str | None = Field(default=None, max_length=1024)  # blank → keep
    enabled: bool | None = None


class RuleIn(BaseModel):
    sender_match: str = Field(min_length=3, max_length=320)
    portal_id: int | None = None
    apply_mode: ApplyMode = ApplyMode.DIRECT_LINK
    delay_minutes: int | None = Field(default=None, ge=0, le=7 * 24 * 60)
    match_forwarded: bool = True
    enabled: bool = True

    @field_validator("sender_match")
    @classmethod
    def _norm(cls, v: str) -> str:
        return normalize_sender_match(v)


class RulePatch(BaseModel):
    sender_match: str | None = None
    portal_id: int | None = None
    apply_mode: ApplyMode | None = None
    delay_minutes: int | None = Field(default=None, ge=0, le=7 * 24 * 60)
    match_forwarded: bool | None = None
    enabled: bool | None = None


class MessageOut(BaseModel):
    id: int
    account_id: int
    sender: str
    original_sender: str | None
    subject: str | None
    received_at: datetime
    classification: str
    extracted_link: str | None
    resolved_link: str | None
    link_safe: bool | None
    link_check_reason: str | None
    job_id: int | None


def _rule_out(r: SenderRule) -> RuleOut:
    return RuleOut(
        id=r.id,
        sender_match=r.sender_match,
        portal_id=r.portal_id,
        apply_mode=r.apply_mode,
        delay_minutes=r.delay_minutes,
        match_forwarded=r.match_forwarded,
        enabled=r.enabled,
    )


def _account_out(a: EmailAccount) -> AccountOut:
    return AccountOut(
        id=a.id,
        provider=a.provider,
        address=a.address,
        display_name=a.display_name,
        imap_host=a.imap_host,
        imap_port=a.imap_port,
        imap_password_masked=mask_secret(a.imap_password),
        oauth_connected=bool(
            (a.oauth_tokens or {}).get("refresh_token")
            or (a.oauth_tokens or {}).get("access_token")
        ),
        status=a.status,
        last_sync_at=a.last_sync_at,
        last_error=a.last_error,
        enabled=a.enabled,
        rules=[_rule_out(r) for r in a.sender_rules],
    )


def _account(db: Session, account_id: int) -> EmailAccount:
    a = db.get(EmailAccount, account_id)
    if a is None:
        raise HTTPException(404, "email account not found")
    return a


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(db: Session = Depends(get_db)) -> list[AccountOut]:
    return [_account_out(a) for a in db.scalars(select(EmailAccount).order_by(EmailAccount.id))]


@router.post("/accounts", response_model=AccountOut, status_code=201)
def add_account(body: AccountIn, db: Session = Depends(get_db)) -> AccountOut:
    if db.scalar(select(EmailAccount.id).where(EmailAccount.address == body.address)):
        raise HTTPException(409, "that address is already added")
    if body.provider is EmailProvider.IMAP and not (body.imap_host and body.imap_password):
        raise HTTPException(422, "IMAP needs a host and an (app) password")
    a = EmailAccount(
        provider=body.provider,
        address=body.address,
        display_name=body.display_name,
        imap_host=body.imap_host,
        imap_port=body.imap_port or (993 if body.imap_host else None),
        imap_password=body.imap_password or None,
    )
    db.add(a)
    db.flush()
    audit(
        db,
        AuditActor.USER,
        "email.account_added",
        entity_type="email_account",
        entity_id=a.id,
        details={"provider": a.provider.value, "address": a.address},
    )
    db.commit()
    return _account_out(a)


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def edit_account(account_id: int, body: AccountPatch, db: Session = Depends(get_db)) -> AccountOut:
    a = _account(db, account_id)
    data = body.model_dump(exclude_unset=True)
    if not data.get("imap_password"):
        data.pop("imap_password", None)
    for k, v in data.items():
        setattr(a, k, v)
    db.commit()
    return _account_out(a)


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(account_id: int, db: Session = Depends(get_db)) -> Response:
    a = _account(db, account_id)
    db.delete(a)
    audit(
        db,
        AuditActor.USER,
        "email.account_deleted",
        entity_type="email_account",
        entity_id=account_id,
    )
    db.commit()
    return Response(status_code=204)


@router.post("/accounts/{account_id}/test")
async def test_account(account_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    a = _account(db, account_id)
    try:
        message = await run_in_threadpool(provider_for(a, TokenStore(db)).test, a)
    except (EmailError, OAuthError) as exc:
        a.status, a.last_error = ConnectionStatus.ERROR, str(exc)[:500]
        db.commit()
        return {"ok": False, "message": str(exc)}
    a.status, a.last_error = ConnectionStatus.CONNECTED, None
    db.commit()
    return {"ok": True, "message": message}


@router.post("/accounts/{account_id}/sync", status_code=202)
def request_sync(account_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    _account(db, account_id)
    key = f"{SYNC_REQUEST_PREFIX}{account_id}"
    row = db.get(Setting, key) or Setting(key=key, is_secret=False)
    row.value = {"requested": True}
    db.add(row)
    db.commit()
    return {"state": "queued", "message": "the worker checks this inbox within a minute"}


@router.post("/accounts/{account_id}/oauth/start")
def oauth_start(account_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    a = _account(db, account_id)
    if a.provider is EmailProvider.IMAP:
        raise HTTPException(422, "IMAP accounts use a password, not OAuth")
    try:
        return {"auth_url": oauth.start(db, a, get_config().dashboard_url)}
    except OAuthError as exc:
        raise HTTPException(409, str(exc)) from None


class OAuthCallbackIn(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=200)


@router.post("/oauth/callback")
async def oauth_callback(body: OAuthCallbackIn, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        a = await run_in_threadpool(
            oauth.finish, db, body.state, body.code, get_config().dashboard_url
        )
    except OAuthError as exc:
        raise HTTPException(400, str(exc)) from None
    audit(db, AuditActor.USER, "email.oauth_connected", entity_type="email_account", entity_id=a.id)
    db.commit()
    return {"ok": True, "account_id": a.id, "address": a.address}


@router.post("/accounts/{account_id}/rules", response_model=RuleOut, status_code=201)
def add_rule(account_id: int, body: RuleIn, db: Session = Depends(get_db)) -> RuleOut:
    _account(db, account_id)
    if body.portal_id is not None and db.get(Portal, body.portal_id) is None:
        raise HTTPException(404, "portal not found")
    r = SenderRule(email_account_id=account_id, **body.model_dump())
    db.add(r)
    db.commit()
    return _rule_out(r)


@router.patch("/rules/{rule_id}", response_model=RuleOut)
def edit_rule(rule_id: int, body: RulePatch, db: Session = Depends(get_db)) -> RuleOut:
    r = db.get(SenderRule, rule_id)
    if r is None:
        raise HTTPException(404, "rule not found")
    data = body.model_dump(exclude_unset=True)
    if data.get("sender_match"):
        try:
            data["sender_match"] = normalize_sender_match(data["sender_match"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    for k, v in data.items():
        setattr(r, k, v)
    db.commit()
    return _rule_out(r)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db)) -> Response:
    r = db.get(SenderRule, rule_id)
    if r is None:
        raise HTTPException(404, "rule not found")
    db.delete(r)
    db.commit()
    return Response(status_code=204)


@router.get("/messages", response_model=list[MessageOut])
def messages(
    account_id: int | None = None, limit: int = 50, db: Session = Depends(get_db)
) -> list[MessageOut]:
    stmt = (
        select(SourceEmail).order_by(SourceEmail.received_at.desc()).limit(min(max(limit, 1), 200))
    )
    if account_id is not None:
        stmt = stmt.where(SourceEmail.account_id == account_id)
    return [
        MessageOut(
            id=m.id,
            account_id=m.account_id,
            sender=m.sender,
            original_sender=m.original_sender,
            subject=m.subject,
            received_at=m.received_at,
            classification=m.classification.value,
            extracted_link=m.extracted_link,
            resolved_link=m.resolved_link,
            link_safe=m.link_safe,
            link_check_reason=m.link_check_reason,
            job_id=m.job_id,
        )
        for m in db.scalars(stmt)
    ]
