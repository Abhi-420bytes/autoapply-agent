"""Sending an approved cold email from your own mailbox, with the tailored resume attached."""

from __future__ import annotations

import base64
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from app.email.oauth import OAuthError, TokenStore
from app.email.providers import explain_api_error
from app.models import EmailAccount
from app.models.enums import EmailProvider


class SendError(Exception):
    """User-facing sending problem. `transient`: a network/provider hiccup (offline laptop,
    DNS, timeout, 5xx, rate limit) that is retried automatically, not a real failure."""

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


def build_message(
    *,
    from_name: str,
    from_address: str,
    to: str,
    subject: str,
    body: str,
    attachment: Path | None,
    attachment_name: str,
) -> EmailMessage:
    m = EmailMessage()
    m["From"] = formataddr((from_name, from_address)) if from_name else from_address
    m["To"] = to
    m["Subject"] = subject
    m["Message-ID"] = make_msgid(domain=from_address.split("@")[-1])
    m.set_content(body)
    if attachment is not None:
        m.add_attachment(
            attachment.read_bytes(), maintype="application", subtype="pdf", filename=attachment_name
        )
    return m


_SMTP = {
    "imap.gmail.com": ("smtp.gmail.com", 465, "ssl"),
    "outlook.office365.com": ("smtp.office365.com", 587, "starttls"),
    "imap-mail.outlook.com": ("smtp-mail.outlook.com", 587, "starttls"),
    "imap.mail.yahoo.com": ("smtp.mail.yahoo.com", 465, "ssl"),
    "imap.zoho.com": ("smtp.zoho.com", 465, "ssl"),
    "imap.zoho.in": ("smtp.zoho.in", 465, "ssl"),
}


def send(
    db: Session,
    account: EmailAccount,
    message: EmailMessage,
    transport: httpx.BaseTransport | None = None,
) -> str:
    """Send `message`; returns the provider's message id."""
    if account.provider is EmailProvider.IMAP:
        return _send_smtp(account, message)
    try:
        token = TokenStore(db, transport).access_token(account)
    except OAuthError as exc:
        raise SendError(str(exc), transient="could not reach" in str(exc)) from None
    headers = {"Authorization": f"Bearer {token.access_token}"}
    raw = message.as_bytes()
    try:
        with httpx.Client(timeout=60, transport=transport) as http:
            if account.provider is EmailProvider.GMAIL_API:
                base = "https://gmail.googleapis.com/gmail/v1/users/me"
                r = http.post(
                    f"{base}/messages/send",
                    headers=headers,
                    json={"raw": base64.urlsafe_b64encode(raw).decode()},
                )
                if r.status_code >= 400:
                    raise SendError(_explain(r, base), transient=_transient_status(r))
                return str(r.json().get("id", ""))
            base = "https://graph.microsoft.com/v1.0/me"
            r = http.post(
                f"{base}/sendMail",
                headers=headers | {"Content-Type": "text/plain"},
                content=base64.b64encode(raw),
            )
            if r.status_code >= 400:
                raise SendError(_explain(r, base), transient=_transient_status(r))
            return str(message["Message-ID"])
    except httpx.HTTPError as exc:  # offline, DNS, timeout: try again later
        raise SendError(f"couldn't reach the mail service: {exc}", transient=True) from None


def _transient_status(r: httpx.Response) -> bool:
    return r.status_code == 429 or r.status_code >= 500


def _explain(r: httpx.Response, base: str) -> str:
    msg = explain_api_error(r, base)
    if r.status_code == 403 and ("scope" in msg.lower() or "permission" in msg.lower()):
        who = "Gmail" if "googleapis" in base else "Microsoft Graph"
        return (
            f"403 from {who}: this mailbox isn't allowed to send yet. Go to Settings → Email, "
            "click Reconnect, and allow 'Send email on your behalf'."
        )
    return msg


def _send_smtp(account: EmailAccount, message: EmailMessage) -> str:
    host = (account.imap_host or "").lower()
    server, port, mode = _SMTP.get(host, (host.replace("imap", "smtp", 1), 465, "ssl"))
    if not account.imap_password:
        raise SendError("this IMAP mailbox has no app password saved")
    try:
        if mode == "ssl":
            with smtplib.SMTP_SSL(
                server, port, context=ssl.create_default_context(), timeout=30
            ) as s:
                s.login(account.address, account.imap_password)
                s.send_message(message)
        else:
            with smtplib.SMTP(server, port, timeout=30) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(account.address, account.imap_password)
                s.send_message(message)
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPRecipientsRefused) as exc:
        raise SendError(f"sending via {server} failed: {exc}") from None
    except (smtplib.SMTPException, OSError) as exc:  # connection/network problems
        raise SendError(f"sending via {server} failed: {exc}", transient=True) from None
    return str(message["Message-ID"])
