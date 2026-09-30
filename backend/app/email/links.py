"""Apply-link extraction and the link safety check (hard rule 8)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

MAX_HOPS = 5
_JUNK_TEXT = re.compile(
    r"unsubscribe|privacy|preferences|view (it )?in (your )?browser|terms|help ?center|"
    r"contact us|facebook|twitter|linkedin|instagram|youtube|app store|google play",
    re.IGNORECASE,
)
_APPLY_TEXT = re.compile(
    r"apply|register|view (job|details|opening)|job details|click here|"
    r"know more|interested|opportunity|link",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


@dataclass(frozen=True)
class LinkCandidate:
    url: str
    text: str
    score: int  # heuristic relevance


class _Anchors(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            self.links.append((self._href.strip(), " ".join("".join(self._text).split())))
            self._href = None


def extract_links(html: str | None, text: str | None) -> list[LinkCandidate]:
    raw: list[tuple[str, str]] = []
    if html:
        p = _Anchors()
        p.feed(html)
        raw += p.links
    for m in _URL_RE.finditer(text or ""):
        raw.append((m.group(0).rstrip(".,;"), ""))
    seen: set[str] = set()
    out: list[LinkCandidate] = []
    for url, label in raw:
        if not url.lower().startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        inner = unwrap(url)
        if _JUNK_TEXT.search(label) or _JUNK_TEXT.search(inner):
            continue
        score = 2 if _APPLY_TEXT.search(label) else 0
        out.append(LinkCandidate(url=url, text=label, score=score))
    return sorted(out, key=lambda c: -c.score)


def unwrap(url: str) -> str:
    """Offline unwrapping of well-known redirect wrappers (no network)."""
    for _ in range(3):
        u = urlparse(url)
        host = (u.hostname or "").lower()
        qs = parse_qs(u.query)
        if host.endswith("safelinks.protection.outlook.com") and "url" in qs:
            url = unquote(qs["url"][0])
        elif (
            host in ("www.google.com", "google.com")
            and u.path == "/url"
            and ("q" in qs or "url" in qs)
        ):
            url = unquote((qs.get("q") or qs["url"])[0])
        elif host.endswith("urldefense.com") and "/v3/__" in u.path:
            url = u.path.split("/v3/__", 1)[1].split("__;", 1)[0]
        else:
            return url
    return url


def host_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for pattern in allowed:
        p = pattern.lower().strip().rstrip(".")
        if not p:
            continue
        if p.startswith("*."):
            if host == p[2:] or host.endswith(p[1:]):
                return True
        elif host == p:
            return True
    return False


class LinkResolver:
    """Follows HTTP redirects with HEAD requests only (no bodies, cookies or JS)."""

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            timeout=10,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "autoapply-link-check"},
        )

    def resolve(self, url: str) -> tuple[str, list[str]]:
        hops = [url]
        current = unwrap(url)
        for _ in range(MAX_HOPS):
            if current != hops[-1]:
                hops.append(current)
            try:
                r = self._http.head(current)
            except httpx.HTTPError:
                break
            location = r.headers.get("location")
            if r.status_code in (301, 302, 303, 307, 308) and location:
                current = unwrap(urljoin(current, location))
                continue
            break
        return current, hops

    def close(self) -> None:
        self._http.close()


@dataclass(frozen=True)
class SafetyResult:
    safe: bool
    final_url: str
    reason: str
    hops: tuple[str, ...]


def check_link(
    url: str, *, sender_allowed: bool, allowed_domains: list[str], resolver: LinkResolver | None
) -> SafetyResult:
    """Sender must match an allowlisted rule AND the final URL (after unwrapping and
    redirects) must be https on an allowlisted portal domain."""
    if not sender_allowed:
        return SafetyResult(False, url, "sender is not on the allowlist", (url,))
    if not allowed_domains:
        return SafetyResult(
            False, url, "the linked portal has no allowed domains configured", (url,)
        )
    final, hops = resolver.resolve(url) if resolver else (unwrap(url), [url])
    parsed = urlparse(final)
    if parsed.scheme != "https":
        return SafetyResult(False, final, f"final link is not https ({parsed.scheme})", tuple(hops))
    if parsed.username or parsed.password:
        return SafetyResult(False, final, "link contains embedded credentials", tuple(hops))
    if not host_allowed(parsed.hostname or "", allowed_domains):
        return SafetyResult(
            False,
            final,
            f"final domain {parsed.hostname} is not an allowed "
            f"portal domain ({', '.join(allowed_domains)})",
            tuple(hops),
        )
    return SafetyResult(True, final, "ok", tuple(hops))
