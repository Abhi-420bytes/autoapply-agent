"""RFC 822 / MIME parsing shared by every provider (Gmail, Graph and IMAP all give MIME)."""

from __future__ import annotations

import email
import email.policy
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

MAX_ATTACHMENT_BYTES = 10_000_000
_ATTACH_TYPES = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "text/plain",
)


@dataclass
class Attachment:
    filename: str
    mime_type: str
    data: bytes


@dataclass
class ParsedEmail:
    message_id: str  # provider id (filled by the adapter) or Message-ID
    internet_message_id: str | None
    sender: str  # bare lowercase address
    sender_name: str
    to: list[str]
    subject: str
    received_at: datetime
    html: str | None
    text: str | None
    headers: dict[str, str]
    attachments: list[Attachment] = field(default_factory=list)

    @property
    def body_text(self) -> str:
        """The richer of the plain-text and HTML parts. Portal emails often put the details
        (company, CTC, eligibility tables) only in the HTML part."""
        plain = (self.text or "").strip()
        rendered = html_to_text(self.html or "")
        return rendered if len(rendered) > len(plain) else plain


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"):
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    text = "".join(p.parts)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def parse_mime(raw: bytes, provider_id: str | None = None) -> ParsedEmail:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(msg, EmailMessage)
    name, addr = parseaddr(str(msg.get("From", "")))
    try:
        received = parsedate_to_datetime(str(msg.get("Date"))) if msg.get("Date") else None
    except (TypeError, ValueError):
        received = None
    if received is None:
        received = datetime.now(UTC)
    elif received.tzinfo is None:
        received = received.replace(tzinfo=UTC)

    html_part = msg.get_body(preferencelist=("html",))
    text_part = msg.get_body(preferencelist=("plain",))
    html = html_part.get_content() if html_part is not None else None
    text = text_part.get_content() if text_part is not None else None

    attachments: list[Attachment] = []
    for part in msg.iter_attachments():
        ctype = part.get_content_type()
        fname = part.get_filename() or "attachment"
        if ctype not in _ATTACH_TYPES and not fname.lower().endswith((".pdf", ".docx", ".txt")):
            continue
        payload = part.get_payload(decode=True)
        if isinstance(payload, bytes) and len(payload) <= MAX_ATTACHMENT_BYTES:
            attachments.append(Attachment(filename=fname, mime_type=ctype, data=payload))

    mid = str(msg.get("Message-ID", "")).strip() or None
    wanted = (
        "Message-ID",
        "Authentication-Results",
        "ARC-Authentication-Results",
        "Received-SPF",
        "Reply-To",
        "Return-Path",
        "X-Forwarded-For",
        "X-MS-Exchange-Organization-AutoForwarded",
        "Auto-Submitted",
    )
    headers = {h: str(msg.get(h)) for h in wanted if msg.get(h)}
    return ParsedEmail(
        message_id=provider_id or mid or f"no-id-{hash(raw)}",
        internet_message_id=mid,
        sender=addr.lower(),
        sender_name=name,
        to=[a.lower() for _, a in getaddresses([str(msg.get("To", ""))]) if a],
        subject=str(msg.get("Subject", "")),
        received_at=received.astimezone(UTC),
        html=html,
        text=text,
        headers=headers,
        attachments=attachments,
    )


# -- forwarded mail -----------------------------------------------------------------------

_FWD_FROM_RE = re.compile(
    r"(?:^|\n)\s*(?:From|De|Von)\s*:\s*(?:\*\s*)?(?P<from>[^\n]+)", re.IGNORECASE
)
_FWD_MARKERS = (
    "forwarded message",
    "original message",
    "begin forwarded message",
    "________________________________",
)


def forwarded_original_sender(parsed: ParsedEmail) -> str | None:
    """The original sender inside a forwarded email's body (Gmail/Outlook formats), or
    None. The caller decides whether to TRUST it (see rules.trusted_original_sender)."""
    body = parsed.body_text
    lower = body.lower()
    auto = parsed.headers.get("X-MS-Exchange-Organization-AutoForwarded", "").lower() == "true"
    if (
        not auto
        and not any(m in lower for m in _FWD_MARKERS)
        and not parsed.subject.lower().startswith(("fw:", "fwd:"))
    ):
        return None
    m = _FWD_FROM_RE.search(body)
    if not m:
        return None
    _, addr = parseaddr(m.group("from").replace("mailto:", ""))
    if not addr:
        found = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", m.group("from"))
        addr = found.group(0) if found else ""
    return addr.lower() or None


def auth_passed(parsed: ParsedEmail) -> bool | None:
    """DMARC/DKIM result from the receiving server's Authentication-Results header:
    True (pass), False (fail), None (no header — can't tell)."""
    header = parsed.headers.get("Authentication-Results") or parsed.headers.get(
        "ARC-Authentication-Results"
    )
    if not header:
        return None
    h = header.lower()
    if "dmarc=fail" in h or ("dkim=fail" in h and "dkim=pass" not in h):
        return False
    if "dmarc=pass" in h or "dkim=pass" in h:
        return True
    return None
