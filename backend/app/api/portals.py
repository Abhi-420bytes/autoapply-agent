from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.email.links import host_allowed
from app.models import Portal
from app.models.enums import AuditActor, PortalLoginMethod
from app.portal.agent import clear_session as drop_session
from app.portal.agent import save_session, session_saved_at
from app.services.audit import audit

router = APIRouter(prefix="/api/portals", tags=["portals"])
_DOMAIN_RE = r"^(\*\.)?[a-z0-9-]+(\.[a-z0-9-]+)+$"


class PortalIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(max_length=500)
    allowed_domains: list[str] = Field(default_factory=list, max_length=20)
    login_method: PortalLoginMethod = PortalLoginMethod.MANUAL
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def _https(cls, v: str) -> str:
        u = urlparse(v.strip())
        if u.scheme != "https" or not u.hostname:
            raise ValueError("base URL must be an https:// URL")
        return v.strip().rstrip("/")

    @field_validator("allowed_domains")
    @classmethod
    def _domains(cls, v: list[str]) -> list[str]:
        import re

        out = []
        for d in v:
            d = d.strip().lower()
            if not d:
                continue
            if not re.match(_DOMAIN_RE, d):
                raise ValueError(f"'{d}' isn't a domain (use app.havlock.in or *.havlock.in)")
            out.append(d)
        return out


class PortalOut(PortalIn):
    id: int
    has_credentials: bool
    session_saved_at: datetime | None = None


def _out(p: Portal) -> PortalOut:
    return PortalOut(
        id=p.id,
        name=p.name,
        base_url=p.base_url,
        allowed_domains=p.allowed_domains,
        login_method=p.login_method,
        enabled=p.enabled,
        has_credentials=bool(p.credentials),
        session_saved_at=session_saved_at(p),
    )


@router.get("", response_model=list[PortalOut])
def list_portals(db: Session = Depends(get_db)) -> list[PortalOut]:
    return [_out(p) for p in db.scalars(select(Portal).order_by(Portal.id))]


@router.post("", response_model=PortalOut, status_code=201)
def add_portal(body: PortalIn, db: Session = Depends(get_db)) -> PortalOut:
    domains = body.allowed_domains or [urlparse(body.base_url).hostname or ""]
    p = Portal(
        name=body.name,
        base_url=body.base_url,
        allowed_domains=domains,
        login_method=body.login_method,
        enabled=body.enabled,
    )
    db.add(p)
    db.flush()
    audit(
        db,
        AuditActor.USER,
        "portal.added",
        entity_type="portal",
        entity_id=p.id,
        details={"name": p.name, "domains": domains},
    )
    db.commit()
    return _out(p)


@router.put("/{portal_id}", response_model=PortalOut)
def edit_portal(portal_id: int, body: PortalIn, db: Session = Depends(get_db)) -> PortalOut:
    p = db.get(Portal, portal_id)
    if p is None:
        raise HTTPException(404, "portal not found")
    p.name, p.base_url, p.login_method, p.enabled = (
        body.name,
        body.base_url,
        body.login_method,
        body.enabled,
    )
    p.allowed_domains = body.allowed_domains or [urlparse(body.base_url).hostname or ""]
    db.commit()
    return _out(p)


@router.delete("/{portal_id}", status_code=204)
def delete_portal(portal_id: int, db: Session = Depends(get_db)) -> Response:
    p = db.get(Portal, portal_id)
    if p is None:
        raise HTTPException(404, "portal not found")
    db.delete(p)
    db.commit()
    return Response(status_code=204)


class CredentialsIn(BaseModel):
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


@router.put("/{portal_id}/credentials", response_model=PortalOut)
def set_credentials(
    portal_id: int, body: CredentialsIn, db: Session = Depends(get_db)
) -> PortalOut:
    """Stored encrypted (EncryptedJSON); never returned by any endpoint."""
    p = db.get(Portal, portal_id)
    if p is None:
        raise HTTPException(404, "portal not found")
    p.credentials = {"username": body.username, "password": body.password}
    audit(db, AuditActor.USER, "portal.credentials_set", entity_type="portal", entity_id=p.id)
    db.commit()
    return _out(p)


@router.delete("/{portal_id}/credentials", response_model=PortalOut)
def clear_credentials(portal_id: int, db: Session = Depends(get_db)) -> PortalOut:
    p = db.get(Portal, portal_id)
    if p is None:
        raise HTTPException(404, "portal not found")
    p.credentials = None
    db.commit()
    return _out(p)


class SessionIn(BaseModel):
    """A browser storage state (Playwright format) from the user's own sign-in."""

    cookies: list[dict[str, Any]] = Field(default_factory=list, max_length=500)
    origins: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    # sessionStorage per origin (not part of a browser's saved state; captured separately)
    session_storage: dict[str, dict[str, str]] = Field(default_factory=dict)


@router.put("/{portal_id}/session", response_model=PortalOut)
def set_session(portal_id: int, body: SessionIn, db: Session = Depends(get_db)) -> PortalOut:
    """Save the session from `scripts/portal_login.py` (the user solved any CAPTCHA and
    logged in themselves). Only the portal's own cookies are kept; stored encrypted."""
    p = db.get(Portal, portal_id)
    if p is None:
        raise HTTPException(404, "portal not found")
    domains = p.allowed_domains or [urlparse(p.base_url).hostname or ""]
    cookies = [
        c for c in body.cookies if host_allowed(str(c.get("domain", "")).lstrip("."), domains)
    ]
    origins = [
        o
        for o in body.origins
        if host_allowed(urlparse(str(o.get("origin", ""))).hostname or "", domains)
    ]
    session_storage = {
        o: items
        for o, items in body.session_storage.items()
        if host_allowed(urlparse(o).hostname or "", domains)
    }
    if (
        not cookies
        and not session_storage
        and not any(o.get("localStorage") or o.get("indexedDB") for o in origins)
    ):
        raise HTTPException(
            422,
            f"no {p.name} login in that session (no cookies or site storage); "
            "finish logging in before closing the window",
        )
    save_session(p, {"cookies": cookies, "origins": origins, "session_storage": session_storage})
    audit(db, AuditActor.USER, "portal.session_saved", entity_type="portal", entity_id=p.id)
    db.commit()
    return _out(p)


@router.delete("/{portal_id}/session", response_model=PortalOut)
def clear_session(portal_id: int, db: Session = Depends(get_db)) -> PortalOut:
    p = db.get(Portal, portal_id)
    if p is None:
        raise HTTPException(404, "portal not found")
    drop_session(p)
    return _out(p)
