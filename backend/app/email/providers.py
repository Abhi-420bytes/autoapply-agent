"""Mail provider adapters: IMAP, Gmail API (OAuth) and Microsoft Graph (OAuth).

Privacy by design: `list_candidates` fetches only sender + date metadata; the caller
matches senders against the sender rules, and only matching messages are downloaded in
full with `fetch_raw` (always as MIME, parsed by app.email.parse). Nothing is marked read.
"""

from __future__ import annotations

import base64
import imaplib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Protocol

import httpx

from app.email.oauth import OAuthTokens, TokenStore
from app.models import EmailAccount
from app.models.enums import EmailProvider

FIRST_SYNC_DAYS = 14
MAX_CANDIDATES = 500


class EmailError(Exception):
    """User-facing provider problem (bad password, revoked token, blocked by tenant...)."""


@dataclass(frozen=True)
class Candidate:
    provider_id: str
    sender: str
    received_at: datetime | None


class MailProvider(Protocol):
    def test(self, account: EmailAccount) -> str: ...

    def list_candidates(
        self, account: EmailAccount, since: datetime, cursor: str | None
    ) -> tuple[list[Candidate], str | None]: ...

    def fetch_raw(self, account: EmailAccount, provider_id: str) -> bytes: ...


# -- IMAP ---------------------------------------------------------------------------------


class ImapProvider:
    """Generic IMAP over SSL. The cursor is the highest UID seen (UIDs only increase)."""

    def __init__(self, connect: Callable[[str, int], Any] | None = None) -> None:
        self._connect = connect or (lambda host, port: imaplib.IMAP4_SSL(host, port, timeout=30))

    def _session(self, account: EmailAccount) -> Any:
        if not (account.imap_host and account.imap_password):
            raise EmailError("IMAP host and password (app password) are required")
        try:
            conn = self._connect(account.imap_host, account.imap_port or 993)
            conn.login(account.address, account.imap_password)
            conn.select("INBOX", readonly=True)  # readonly: never changes flags
        except (imaplib.IMAP4.error, OSError) as exc:
            raise EmailError(f"IMAP login failed: {exc}") from None
        return conn

    def test(self, account: EmailAccount) -> str:
        conn = self._session(account)
        try:
            typ, data = conn.uid("SEARCH", None, "ALL")
            n = len(data[0].split()) if typ == "OK" and data and data[0] else 0
            return f"connected; {n} message(s) in INBOX"
        finally:
            conn.logout()

    def list_candidates(
        self, account: EmailAccount, since: datetime, cursor: str | None
    ) -> tuple[list[Candidate], str | None]:
        conn = self._session(account)
        try:
            if cursor:
                typ, data = conn.uid("SEARCH", None, f"UID {int(cursor) + 1}:*")
            else:
                typ, data = conn.uid("SEARCH", None, "SINCE", since.strftime("%d-%b-%Y"))
            uids = [
                u
                for u in (data[0].split() if typ == "OK" and data and data[0] else [])
                if not cursor or int(u) > int(cursor)
            ][-MAX_CANDIDATES:]
            out: list[Candidate] = []
            for uid in uids:
                typ, msg = conn.uid("FETCH", uid, "(BODY.PEEK[HEADER.FIELDS (FROM DATE)])")
                header = b""
                for part in msg or []:
                    if isinstance(part, tuple):
                        header += part[1]
                text = header.decode(errors="replace")
                sender = parseaddr(_header(text, "From"))[1].lower()
                try:
                    received = parsedate_to_datetime(_header(text, "Date"))
                except (TypeError, ValueError):
                    received = None
                out.append(Candidate(uid.decode(), sender, received))
            new_cursor = uids[-1].decode() if uids else cursor
            return out, new_cursor
        finally:
            conn.logout()

    def fetch_raw(self, account: EmailAccount, provider_id: str) -> bytes:
        conn = self._session(account)
        try:
            typ, msg = conn.uid("FETCH", provider_id.encode(), "(BODY.PEEK[])")
            for part in msg or []:
                if isinstance(part, tuple):
                    return bytes(part[1])
            raise EmailError(f"message {provider_id} not found")
        finally:
            conn.logout()


def _header(text: str, name: str) -> str:
    m = re.search(rf"^{name}:\s*(.+(?:\r?\n[ \t].+)*)", text, re.IGNORECASE | re.MULTILINE)
    return " ".join(m.group(1).split()) if m else ""


# -- OAuth HTTP providers ------------------------------------------------------------------


class _OAuthHttp:
    base: str

    def __init__(self, tokens: TokenStore, transport: httpx.BaseTransport | None = None):
        self._tokens = tokens
        self._http = httpx.Client(base_url=self.base, timeout=30, transport=transport)

    def _get(self, account: EmailAccount, path: str, **kw: Any) -> httpx.Response:
        token: OAuthTokens = self._tokens.access_token(account)
        r = self._http.get(path, headers={"Authorization": f"Bearer {token.access_token}"}, **kw)
        if r.status_code == 401:
            token = self._tokens.access_token(account, force_refresh=True)
            r = self._http.get(
                path, headers={"Authorization": f"Bearer {token.access_token}"}, **kw
            )
        if r.status_code >= 400:
            raise EmailError(explain_api_error(r, self.base))
        return r


def explain_api_error(r: httpx.Response, base: str) -> str:
    """Turn a Gmail/Graph error into the provider's own reason plus the likely fix."""
    try:
        err = r.json().get("error", {})
    except ValueError:
        err = {}
    if isinstance(err, str):  # some endpoints return {"error": "..."}
        err = {"message": err}
    message = str(err.get("message") or r.text[:200]).strip()
    reasons = {str(e.get("reason", "")) for e in err.get("errors", []) if isinstance(e, dict)}
    reasons |= {str(d.get("reason", "")) for d in err.get("details", []) if isinstance(d, dict)}
    reasons.add(str(err.get("code", "")))
    gmail = "googleapis.com" in base
    hint = ""
    if gmail and (
        {"SERVICE_DISABLED", "accessNotConfigured"} & reasons
        or "has not been used in project" in message
        or "is disabled" in message
    ):
        hint = (
            "Enable the Gmail API for your Google Cloud project (APIs & Services → Library → "
            "Gmail API → Enable), wait a minute, then Test again."
        )
    elif gmail and (
        {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"} & reasons
        or "insufficient authentication scopes" in message.lower()
    ):
        hint = (
            "The Gmail read permission wasn't granted. Click Reconnect and tick the "
            "'Read your email' box on Google's consent screen."
        )
    elif r.status_code == 401:
        hint = "The sign-in expired or was revoked. Click Reconnect."
    elif not gmail and r.status_code == 403:
        hint = (
            "Your organization may block this app. Ask IT for consent, or forward Havlock "
            "mail from Outlook to Gmail and enable 'accept forwards' on the Gmail rule."
        )
    return f"{r.status_code} from {'Gmail' if gmail else 'Microsoft Graph'}: {message[:300]}" + (
        f" — {hint}" if hint else ""
    )


class GmailProvider(_OAuthHttp):
    base = "https://gmail.googleapis.com/gmail/v1/users/me"

    def test(self, account: EmailAccount) -> str:
        profile = self._get(account, "/profile").json()
        return f"connected as {profile.get('emailAddress')}"

    def list_candidates(
        self, account: EmailAccount, since: datetime, cursor: str | None
    ) -> tuple[list[Candidate], str | None]:
        after = int((datetime.fromisoformat(cursor) if cursor else since).timestamp())
        ids: list[str] = []
        page: str | None = None
        while len(ids) < MAX_CANDIDATES:
            params: dict[str, Any] = {"q": f"after:{after}", "maxResults": 100}
            if page:
                params["pageToken"] = page
            data = self._get(account, "/messages", params=params).json()
            ids += [m["id"] for m in data.get("messages", [])]
            page = data.get("nextPageToken")
            if not page:
                break
        out: list[Candidate] = []
        latest = datetime.fromisoformat(cursor) if cursor else since
        for mid in ids:
            meta = self._get(
                account,
                f"/messages/{mid}",
                params={"format": "metadata", "metadataHeaders": ["From"]},
            ).json()
            headers = {
                h["name"].lower(): h["value"] for h in meta.get("payload", {}).get("headers", [])
            }
            received = datetime.fromtimestamp(int(meta.get("internalDate", "0")) / 1000, UTC)
            latest = max(latest, received)
            out.append(Candidate(mid, parseaddr(headers.get("from", ""))[1].lower(), received))
        return out, latest.isoformat()

    def fetch_raw(self, account: EmailAccount, provider_id: str) -> bytes:
        data = self._get(account, f"/messages/{provider_id}", params={"format": "raw"}).json()
        return base64.urlsafe_b64decode(data["raw"] + "=" * (-len(data["raw"]) % 4))


class GraphProvider(_OAuthHttp):
    base = "https://graph.microsoft.com/v1.0/me"

    def test(self, account: EmailAccount) -> str:
        me = self._get(account, "").json()
        return f"connected as {me.get('mail') or me.get('userPrincipalName')}"

    def list_candidates(
        self, account: EmailAccount, since: datetime, cursor: str | None
    ) -> tuple[list[Candidate], str | None]:
        start = datetime.fromisoformat(cursor) if cursor else since
        params: dict[str, Any] | None = {
            "$filter": f"receivedDateTime ge {start.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "$select": "id,from,receivedDateTime",
            "$top": 100,
            "$orderby": "receivedDateTime asc",
        }
        path = "/mailFolders/inbox/messages"
        out: list[Candidate] = []
        latest = start
        while path and len(out) < MAX_CANDIDATES:
            data = self._get(account, path, params=params).json()
            for m in data.get("value", []):
                received = datetime.fromisoformat(m["receivedDateTime"].replace("Z", "+00:00"))
                latest = max(latest, received)
                sender = (m.get("from") or {}).get("emailAddress", {}).get("address", "")
                out.append(Candidate(m["id"], sender.lower(), received))
            nxt = data.get("@odata.nextLink")
            path, params = (nxt.replace(self.base, ""), None) if nxt else ("", None)
        return out, (latest + timedelta(seconds=1)).isoformat()

    def fetch_raw(self, account: EmailAccount, provider_id: str) -> bytes:
        return self._get(account, f"/messages/{provider_id}/$value").content


def provider_for(
    account: EmailAccount, tokens: TokenStore, transport: httpx.BaseTransport | None = None
) -> MailProvider:
    if account.provider is EmailProvider.IMAP:
        return ImapProvider()
    if account.provider is EmailProvider.GMAIL_API:
        return GmailProvider(tokens, transport)
    return GraphProvider(tokens, transport)


def first_sync_since() -> datetime:
    return datetime.now(UTC) - timedelta(days=FIRST_SYNC_DAYS)
