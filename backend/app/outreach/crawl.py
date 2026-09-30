"""Reading a company's own website: what they do, open roles, and published emails.

Polite by design: robots.txt is respected, at most a handful of same-site pages are read,
spaced apart, and only addresses the company publishes on its own domain are collected.
Personal addresses are never guessed or pattern-generated.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import httpx

from app.boards.linkedin import html_text, registrable

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; AutoApplyAgent/1.0; personal job-search assistant)"
ROBOTS_AGENT = "AutoApplyAgent"
MAX_PAGES = 5
MAX_BYTES = 1_500_000
PAUSE_S = 1.5

_DOT = re.compile(r"\s*[\[(]\s*dot\s*[\])]\s*", re.I)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_OBFUSCATED = re.compile(
    r"([A-Za-z0-9._%+-]+)\s*[\[(]\s*at\s*[\])]\s*([A-Za-z0-9-]+(?:\s*[\[(]\s*dot\s*[\])]\s*[A-Za-z0-9-]+)+)",
    re.I,
)
_CAREERS_LINK = re.compile(r"career|jobs?\b|join|work-with-us|hiring|opening|vacanc", re.I)
_INFO_LINK = re.compile(r"contact|about|team|company", re.I)
# Inboxes that are not for job applicants (matched as the whole local part or its first
# word: "marketing", "sales-india", "support.team" ...).
_SKIP_LOCAL = re.compile(
    r"^(no-?reply|do-?not-?reply|privacy|abuse|security|legal|dpo|gdpr|press|media|pr|billing|"
    r"accounts?|invoices?|sales|partners?|partnerships|support|help|helpdesk|unsubscribe|"
    r"webmaster|postmaster|admin|compliance|investors?|ir|marketing|growth|business|bd|bizdev|"
    r"events?|community|feedback|orders?|care|customer\w*|success|demo|trial|finance|"
    r"procurement|vendors?|purchase|grievances?|complaints?|refunds?|returns?|shipping|"
    r"operations|ops|devrel|developers?|api|tech-?support|alerts?|notifications?|newsletter|"
    r"enquiry-sales|pricing|quotes?|advertis\w*|sponsor\w*|affiliates?|reseller\w*)"
    r"([._+-].*|\d*)$",
    re.I,
)
# addresses on these pages are for customers/advertisers/press, not for applicants
_SALES_PAGE = re.compile(r"advertis|sponsor|pricing|sales|press|media-kit|partner|investor", re.I)
_CAREERS_LOCAL = re.compile(r"career|jobs?|hr|talent|recruit|hiring|people|join", re.I)
_GENERAL_LOCAL = re.compile(r"^(hello|hi|info|contact|team|founders?|connect|reach)$", re.I)


@dataclass
class Page:
    url: str
    text: str
    careers: bool = False


@dataclass
class FoundEmail:
    address: str
    source_url: str
    kind: str  # careers | general | person


@dataclass
class SiteReport:
    pages: list[Page] = field(default_factory=list)
    emails: list[FoundEmail] = field(default_factory=list)
    blocked: str | None = None  # why nothing could be read


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((self._href.strip(), " ".join("".join(self._text).split())))
            self._href = None


def email_kind(local: str) -> str:
    if _CAREERS_LOCAL.search(local):
        return "careers"
    if _GENERAL_LOCAL.match(local):
        return "general"
    return "person"


def emails_in(html: str, text: str, domain: str) -> list[str]:
    """Addresses on the company's own domain (or a subdomain), minus role inboxes that
    aren't about hiring (privacy@, sales@, noreply@ ...)."""
    found: list[str] = []
    candidates = _EMAIL_RE.findall(html) + _EMAIL_RE.findall(text)
    for m in _OBFUSCATED.finditer(text):
        candidates.append(m.group(1) + "@" + _DOT.sub(".", m.group(2)))
    for raw in candidates:
        addr = raw.strip(".").lower()
        local, _, host = addr.partition("@")
        if not (host == domain or host.endswith("." + domain)):
            continue
        if _SKIP_LOCAL.match(local) or re.search(r"\.(png|jpe?g|gif|svg|webp)$", addr):
            continue
        if addr not in found:
            found.append(addr)
    return found


def rank_emails(emails: list[FoundEmail]) -> list[FoundEmail]:
    order = {"careers": 0, "general": 1, "person": 2}
    return sorted(emails, key=lambda e: order.get(e.kind, 3))


class SiteReader:
    def __init__(
        self, transport: httpx.BaseTransport | None = None, pause_s: float = PAUSE_S
    ) -> None:
        self._http = httpx.Client(
            timeout=15,
            follow_redirects=True,
            max_redirects=5,
            transport=transport,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
        )
        self._pause = pause_s

    def close(self) -> None:
        self._http.close()

    def _robots(self, base: str) -> robotparser.RobotFileParser | None:
        rp = robotparser.RobotFileParser()
        try:
            r = self._http.get(urljoin(base, "/robots.txt"))
        except httpx.HTTPError:
            return None
        if r.status_code >= 400:
            return None  # no robots.txt: everything allowed
        rp.parse(r.text.splitlines())
        return rp

    def _get(self, url: str, domain: str) -> tuple[str, str] | None:
        try:
            r = self._http.get(url)
        except httpx.HTTPError as exc:
            log.info("couldn't read %s: %s", url, exc)
            return None
        final = urlparse(str(r.url))
        if final.scheme not in ("http", "https") or registrable(final.hostname or "") != domain:
            return None  # redirected off the company's site
        if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
            return None
        return str(r.url), r.text[:MAX_BYTES]

    def page_contains(self, url: str, text: str) -> bool:
        """Does this page (robots.txt permitting) literally contain `text`?"""
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        robots = self._robots(f"{u.scheme}://{u.hostname}")
        if robots is not None and not robots.can_fetch(ROBOTS_AGENT, url):
            return False
        got = self._get(url, registrable(u.hostname))
        return got is not None and text.lower() in got[1].lower()

    def read(self, website: str) -> SiteReport:
        u = urlparse(website if "://" in website else f"https://{website}")
        base = f"{u.scheme or 'https'}://{u.hostname}"
        domain = registrable(u.hostname or "")
        report = SiteReport()
        robots = self._robots(base)
        if robots is not None and not robots.can_fetch(ROBOTS_AGENT, base + "/"):
            report.blocked = "the site's robots.txt asks automated readers not to visit"
            return report
        first = self._get(website if "://" in website else base, domain)
        if first is None:
            report.blocked = "the website couldn't be read"
            return report
        queue: list[tuple[int, str]] = []
        seen = {first[0]}
        pages_html = [(first[0], first[1], False)]
        parser = _Links()
        parser.feed(first[1])
        for href, label in parser.links:
            link = urljoin(first[0], href).split("#")[0]
            lu = urlparse(link)
            if lu.scheme not in ("http", "https") or registrable(lu.hostname or "") != domain:
                continue
            if link in seen:
                continue
            seen.add(link)
            probe = f"{lu.path} {label}"
            if _CAREERS_LINK.search(probe):
                queue.append((0, link))
            elif _INFO_LINK.search(probe):
                queue.append((1, link))
        queue.sort()
        for _prio, link in queue[: MAX_PAGES - 1]:
            if robots is not None and not robots.can_fetch(ROBOTS_AGENT, link):
                continue
            time.sleep(self._pause)
            got = self._get(link, domain)
            if got is not None:
                pages_html.append((got[0], got[1], bool(_CAREERS_LINK.search(urlparse(link).path))))
        for url, html, careers in pages_html:
            sales_page = bool(_SALES_PAGE.search(urlparse(url).path))
            text = html_text(re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html))
            report.pages.append(Page(url, text[:6000], careers))
            for addr in [] if sales_page else emails_in(html, text, domain):
                if all(e.address != addr for e in report.emails):
                    report.emails.append(FoundEmail(addr, url, email_kind(addr.split("@")[0])))
        report.emails = rank_emails(report.emails)
        return report
