"""Startup directories (e.g. bangalorestartupmap.com) as a source of companies to contact.

The public page is read politely (robots.txt respected; never a site's /api/), then:
1. structured data embedded in the page (Next.js/React payloads, __NEXT_DATA__, JSON-LD):
   any object with a name and a website;
2. otherwise the page's outbound links (anchor text = name).
VCs, accelerators, communities, job boards and known enterprises are dropped, and entries
are ranked by how well their sector/tags fit your roles (seed to Series C preferred).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.boards.linkedin import registrable
from app.outreach.crawl import ROBOTS_AGENT, USER_AGENT, _Links
from app.outreach.discover import KNOWN_LARGE, is_company_site

MAX_BYTES = 4_000_000
NOT_STARTUP_KINDS = re.compile(
    r"\b(vc|venture|investor|fund|capital|accelerator|incubator|cowork|community|event|media|"
    r"angel|government|university|college|ngo)\b",
    re.I,
)
TECH_WORDS = {
    "ai",
    "ml",
    "saas",
    "software",
    "platform",
    "api",
    "apis",
    "developer",
    "devtools",
    "data",
    "cloud",
    "fintech",
    "b2b",
    "automation",
    "analytics",
    "security",
    "infra",
    "llm",
    "genai",
    "healthtech",
    "edtech",
    "deeptech",
    "robotics",
    "iot",
    "web3",
    "ecommerce",
    "logistics",
}
GOOD_STAGES = re.compile(r"seed|pre-?series|series ?[abc]\b|early", re.I)
LATE_STAGES = re.compile(r"series ?[e-z]\b|ipo|public|listed|acquired", re.I)


@dataclass
class DirectoryEntry:
    name: str
    website: str
    domain: str
    about: str = ""
    tags: list[str] = field(default_factory=list)
    stage: str = ""
    score: float = 0.0


@dataclass
class DirectoryRead:
    entries: list[DirectoryEntry] = field(default_factory=list)
    blocked: str | None = None


def _objects(page: str) -> list[Any]:
    """Every JSON object/array embedded in the page that parses (escaped or not)."""
    out: list[Any] = []
    decoder = json.JSONDecoder()
    for text in (page, page.replace('\\"', '"').replace("\\\\", "\\")):
        for m in re.finditer(r'\{"(?:name|@type|title)"\s*:', text):
            try:
                value, _ = decoder.raw_decode(text, m.start())
            except ValueError:
                continue
            out.append(value)
        for m in re.finditer(
            r'<script[^>]*type="application/(?:ld\+)?json"[^>]*>(.*?)</script>', page, re.S
        ):
            try:
                out.append(json.loads(m.group(1)))
            except ValueError:
                continue
    return out


def _walk(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    stack = [value]
    while stack:
        v = stack.pop()
        if isinstance(v, dict):
            found.append(v)
            stack.extend(v.values())
        elif isinstance(v, list):
            stack.extend(v)
    return found


def _text(v: Any) -> str:
    if isinstance(v, str) and v != "$undefined":
        return unescape(v).strip()
    if isinstance(v, list):
        return ", ".join(_text(x) for x in v if _text(x))
    return ""


def _entry(obj: dict[str, Any], base_host: str) -> DirectoryEntry | None:
    name = _text(obj.get("name") or obj.get("title"))
    site = _text(obj.get("website") or obj.get("homepage") or obj.get("url") or obj.get("link"))
    if not name or not site.startswith("http"):
        return None
    host = (urlparse(site).hostname or "").lower()
    if not host or registrable(host) == registrable(base_host):
        return None
    kind = " ".join(_text(obj.get(k)) for k in ("kind", "type", "@type", "category"))
    if NOT_STARTUP_KINDS.search(kind):
        return None
    tags = [t for t in re.split(r",\s*", _text(obj.get("tags") or obj.get("sector"))) if t]
    about = " ".join(x for x in (_text(obj.get("tagline")), _text(obj.get("description"))) if x)[
        :600
    ]
    stage = _text(obj.get("stage") or obj.get("funding_stage") or obj.get("funding"))
    return DirectoryEntry(name[:200], f"https://{host}", registrable(host), about, tags, stage)


def parse_directory(page: str, url: str) -> list[DirectoryEntry]:
    base = (urlparse(url).hostname or "").lower()
    entries: dict[str, DirectoryEntry] = {}
    for value in _objects(page):
        for obj in _walk(value):
            e = _entry(obj, base)
            if e and e.domain not in entries:
                entries[e.domain] = e
    if not entries:  # plain HTML list: outbound links
        links = _Links()
        links.feed(page)
        for href, label in links.links:
            target = urljoin(url, href)
            host = (urlparse(target).hostname or "").lower()
            if not host or registrable(host) == registrable(base) or not label or len(label) > 80:
                continue
            d = registrable(host)
            if d not in entries:
                entries[d] = DirectoryEntry(label, f"https://{host}", d)
    return [
        e
        for e in entries.values()
        if is_company_site(urlparse(e.website).hostname or "") and e.domain not in KNOWN_LARGE
    ]


def score(e: DirectoryEntry, roles: list[str]) -> float:
    """How good a fit: tech sector/tags, overlap with your roles, early stage."""
    words = set(re.findall(r"[a-z0-9]+", f"{e.about} {' '.join(e.tags)}".lower()))
    wanted = {w for r in roles for w in re.findall(r"[a-z]+", r.lower()) if len(w) > 2}
    s = len(words & TECH_WORDS) + 2 * len(words & wanted)
    if GOOD_STAGES.search(e.stage):
        s += 2
    if LATE_STAGES.search(e.stage):
        s -= 3
    return float(s)


def read_directory(url: str, transport: httpx.BaseTransport | None = None) -> DirectoryRead:
    from urllib import robotparser

    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return DirectoryRead(blocked="not a web address")
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"}
    with httpx.Client(timeout=30, follow_redirects=True, transport=transport, headers=headers) as h:
        try:
            robots = h.get(f"{u.scheme}://{u.hostname}/robots.txt")
            if robots.status_code < 400:
                rp = robotparser.RobotFileParser()
                rp.parse(robots.text.splitlines())
                if not rp.can_fetch(ROBOTS_AGENT, url):
                    return DirectoryRead(blocked="the site's robots.txt doesn't allow reading it")
            r = h.get(url)
        except httpx.HTTPError as exc:
            return DirectoryRead(blocked=f"couldn't read it: {exc}")
    if r.status_code != 200:
        return DirectoryRead(blocked=f"the site answered {r.status_code}")
    return DirectoryRead(entries=parse_directory(r.text[:MAX_BYTES], str(r.url)))
