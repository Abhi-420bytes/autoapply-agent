"""OAuth 2.0 (authorization code + PKCE) for Gmail and Microsoft Graph.

The OAuth *app* credentials (client id/secret you create in Google Cloud / Azure) are
stored as encrypted secret settings. Per-account tokens live in the encrypted
`email_accounts.encrypted_oauth_tokens` column; refresh happens automatically.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.models import EmailAccount, Setting
from app.models.enums import ConnectionStatus, EmailProvider
from app.security.redaction import register_secret
from app.services.settings_service import get_secret

STATE_TTL = timedelta(minutes=15)
REDIRECT_PATH = "/oauth/callback"

GOOGLE = {
    "auth": "https://accounts.google.com/o/oauth2/v2/auth",
    "token": "https://oauth2.googleapis.com/token",
    # read job mail; send only the cold emails you approve (Outreach)
    "scope": "https://www.googleapis.com/auth/gmail.readonly "
    "https://www.googleapis.com/auth/gmail.send",
}
MS_SCOPE = (
    "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.Send "
    "https://graph.microsoft.com/User.Read"
)


class OAuthError(Exception):
    pass


@dataclass
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime


def _app_creds(db: Session, provider: EmailProvider) -> tuple[str, str, str]:
    if provider is EmailProvider.GMAIL_API:
        cid, secret, tenant = (
            get_secret(db, "google_client_id"),
            get_secret(db, "google_client_secret"),
            "",
        )
    else:
        cid, secret = (
            get_secret(db, "microsoft_client_id"),
            get_secret(db, "microsoft_client_secret"),
        )
        tenant = get_secret(db, "microsoft_tenant") or "common"
    if not cid or not secret:
        name = "Google" if provider is EmailProvider.GMAIL_API else "Microsoft"
        raise OAuthError(f"add your {name} OAuth app client ID and secret in Email settings first")
    return cid, secret, tenant


def _endpoints(provider: EmailProvider, tenant: str) -> dict[str, str]:
    if provider is EmailProvider.GMAIL_API:
        return GOOGLE
    base = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    return {"auth": f"{base}/authorize", "token": f"{base}/token", "scope": MS_SCOPE}


def start(db: Session, account: EmailAccount, redirect_base: str) -> str:
    """Build the consent URL. State + PKCE verifier are stored server-side (single use)."""
    cid, _, tenant = _app_creds(db, account.provider)
    ep = _endpoints(account.provider, tenant)
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    row = Setting(key=f"oauth.state.{state}", is_secret=True)
    row.secret_value = f"{account.id}|{verifier}|{datetime.now(UTC).isoformat()}"
    db.add(row)
    db.commit()
    params = {
        "client_id": cid,
        "redirect_uri": redirect_base.rstrip("/") + REDIRECT_PATH,
        "response_type": "code",
        "scope": ep["scope"],
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "login_hint": account.address,
    }
    if account.provider is EmailProvider.GMAIL_API:
        params |= {"access_type": "offline", "prompt": "consent"}
    return f"{ep['auth']}?{urlencode(params)}"


def _token_request(
    url: str, data: dict[str, str], transport: httpx.BaseTransport | None
) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=20, transport=transport) as http:
            r = http.post(url, data=data)
    except httpx.HTTPError as exc:
        raise OAuthError(f"could not reach the sign-in service: {exc}") from None
    body: dict[str, Any] = (
        r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    )
    if r.status_code >= 400 or "access_token" not in body:
        desc = body.get("error_description") or body.get("error") or r.text[:200]
        raise OAuthError(f"token exchange failed: {desc}")
    register_secret(body.get("access_token"))
    register_secret(body.get("refresh_token"))
    return body


def _store(
    account: EmailAccount, body: dict[str, Any], old_refresh: str | None = None
) -> OAuthTokens:
    tokens = OAuthTokens(
        access_token=body["access_token"],
        refresh_token=body.get("refresh_token") or old_refresh,
        expires_at=datetime.now(UTC) + timedelta(seconds=int(body.get("expires_in", 3600)) - 60),
    )
    account.oauth_tokens = {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_at": tokens.expires_at.isoformat(),
    }
    return tokens


def finish(
    db: Session,
    state: str,
    code: str,
    redirect_base: str,
    transport: httpx.BaseTransport | None = None,
) -> EmailAccount:
    row = db.get(Setting, f"oauth.state.{state}")
    if row is None or not row.secret_value:
        raise OAuthError("sign-in link expired or already used; start again")
    account_id, verifier, created = row.secret_value.split("|", 2)
    db.delete(row)
    db.commit()
    if datetime.now(UTC) - datetime.fromisoformat(created) > STATE_TTL:
        raise OAuthError("sign-in took too long; start again")
    account = db.get(EmailAccount, int(account_id))
    if account is None:
        raise OAuthError("account no longer exists")
    cid, secret, tenant = _app_creds(db, account.provider)
    body = _token_request(
        _endpoints(account.provider, tenant)["token"],
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": cid,
            "client_secret": secret,
            "code_verifier": verifier,
            "redirect_uri": redirect_base.rstrip("/") + REDIRECT_PATH,
        },
        transport,
    )
    _store(account, body)
    account.status, account.last_error = ConnectionStatus.CONNECTED, None
    db.commit()
    return account


class TokenStore:
    """Hands out valid access tokens, refreshing (and persisting) them when needed."""

    def __init__(self, db: Session, transport: httpx.BaseTransport | None = None) -> None:
        self._db = db
        self._transport = transport

    def access_token(self, account: EmailAccount, force_refresh: bool = False) -> OAuthTokens:
        t = account.oauth_tokens or {}
        if not t.get("access_token"):
            raise OAuthError("account isn't connected; click Connect")
        expires = (
            datetime.fromisoformat(t["expires_at"]) if t.get("expires_at") else datetime.now(UTC)
        )
        if not force_refresh and expires > datetime.now(UTC):
            return OAuthTokens(t["access_token"], t.get("refresh_token"), expires)
        if not t.get("refresh_token"):
            raise OAuthError("access expired and no refresh token; reconnect the account")
        cid, secret, tenant = _app_creds(self._db, account.provider)
        body = _token_request(
            _endpoints(account.provider, tenant)["token"],
            {
                "grant_type": "refresh_token",
                "refresh_token": t["refresh_token"],
                "client_id": cid,
                "client_secret": secret,
            },
            self._transport,
        )
        tokens = _store(account, body, t["refresh_token"])
        self._db.commit()
        return tokens
