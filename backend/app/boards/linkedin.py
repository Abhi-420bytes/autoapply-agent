"""LinkedIn job alerts → one job per posting, the full JD, and the company's own apply page.

LinkedIn's terms forbid bots that apply or scrape, so the agent never logs in to LinkedIn
and never clicks "Easy Apply". It only:
- reads the job ids in alert emails you received;
- reads each posting's public page once (what anyone sees when they click the link),
  politely spaced, for the full job description;
- looks for the same opening on the company's own hiring site with web search, verified
  by title and company. Known applicant-tracking systems are trusted automatically; any
  other site needs your one-time OK before the agent opens it.
If no company page is found, the resume is prepared and you apply on LinkedIn yourself.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from app.email.links import host_allowed, unwrap
from app.search.web import Searcher, SearchError

log = logging.getLogger(__name__)

JOB_ID_RE = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#\s\"']*-)?(\d{6,})")
MAX_PER_ALERT = 10
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130 Safari/537.36"
)

# Applicant-tracking systems: companies' official application forms live here.
ATS_DOMAINS = [
    "*.greenhouse.io",
    "*.lever.co",
    "*.myworkdayjobs.com",
    "*.myworkdaysite.com",
    "*.ashbyhq.com",
    "*.smartrecruiters.com",
    "*.workable.com",
    "*.bamboohr.com",
    "*.recruitee.com",
    "*.jobvite.com",
    "*.icims.com",
    "*.taleo.net",
    "*.successfactors.com",
    "*.successfactors.eu",
    "*.oraclecloud.com",
    "*.zohorecruit.com",
    "*.zohorecruit.in",
    "*.darwinbox.in",
    "*.keka.com",
    "*.freshteam.com",
    "*.breezy.hr",
    "*.teamtailor.com",
    "*.personio.de",
    "*.personio.com",
    "*.rippling.com",
]
# Job boards and aggregators: never "the company's own site".
BOARDS = [
    "*.linkedin.com",
    "*.naukri.com",
    "*.indeed.com",
    "*.glassdoor.com",
    "*.glassdoor.co.in",
    "*.foundit.in",
    "*.monsterindia.com",
    "*.instahyre.com",
    "*.wellfound.com",
    "*.cutshort.io",
    "*.hirist.tech",
    "*.iimjobs.com",
    "*.shine.com",
    "*.timesjobs.com",
    "*.internshala.com",
    "*.simplyhired.com",
    "*.ziprecruiter.com",
    "*.jooble.org",
    "*.talent.com",
    "*.bebee.com",
    "jobs.workable.com",  # Workable's public listing board (company pages: apply.workable.com)
    "*.jobrapido.com",
    "*.careerjet.co.in",
    "*.careerjet.com",
    "*.adzuna.in",
    "*.whatjobs.com",
    "*.jobleads.com",
    "*.jobgether.com",
    "*.expertini.com",
    "*.freshersworld.com",
    "*.apna.co",
    "*.workindia.in",
    "*.updazz.com",
    "*.learn4good.com",
    "*.builtinbangalore.com",
    "*.cutshort.io",
]


def canonical_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


def is_ats(host: str) -> bool:
    return host_allowed(host, ATS_DOMAINS)


def is_board(host: str) -> bool:
    return host_allowed(host, BOARDS)


# -- alert emails ---------------------------------------------------------------------------


class _Anchors(HTMLParser):
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
            self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


@dataclass(frozen=True)
class AlertPosting:
    job_id: str
    title: str  # the link text in the alert (usually the job title), may be ""
    snippet: str = ""  # this posting's own part of the alert (title, company, location...)


def alert_postings(html: str | None, text: str | None) -> list[AlertPosting]:
    """Distinct LinkedIn job postings linked from an alert email, in order."""
    anchors: list[tuple[str, str]] = []
    if html:
        p = _Anchors()
        p.feed(html)
        anchors = p.links
    titles: dict[str, str] = {}
    order: list[str] = []
    for href, label in anchors:
        m = JOB_ID_RE.search(unwrap(href))
        if not m:
            continue
        jid = m.group(1)
        if jid not in titles:
            order.append(jid)
            titles[jid] = ""
        if len(label) > len(titles[jid]) and len(label) < 120:
            titles[jid] = label
    for m in JOB_ID_RE.finditer(f"{html or ''} {text or ''}"):
        if m.group(1) not in titles:
            order.append(m.group(1))
            titles[m.group(1)] = ""
    # each posting's section of the email: from its first link to the next posting's
    starts: list[tuple[int, str]] = []
    body = html or ""
    for jid in order:
        m = re.search(rf"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#\s\"']*-)?{jid}", body)
        if m:
            a = body.rfind("<a", 0, m.start())
            starts.append((a if a >= 0 else m.start(), jid))
    starts.sort()
    snippets: dict[str, str] = {}
    for i, (start, jid) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else min(len(body), start + 3000)
        snippets[jid] = html_text(body[start:end])[:1500]
    return [AlertPosting(j, titles[j], snippets.get(j, "")) for j in order[:MAX_PER_ALERT]]


def title_skipped(title: str, words: list[str]) -> str | None:
    """The skip word the title contains (whole words, case-insensitive), if any."""
    tokens = set(re.findall(r"[a-z0-9+#]+", title.lower()))
    for w in words:
        w = w.strip().lower()
        if w and (w in tokens if " " not in w else w in title.lower()):
            return w
    return None


def is_linkedin_alert(sender: str, html: str | None, text: str | None) -> bool:
    return sender.lower().endswith("linkedin.com") or bool(
        JOB_ID_RE.search(f"{html or ''} {text or ''}")
    )


# -- public posting page ----------------------------------------------------------------------


@dataclass(frozen=True)
class Posting:
    job_id: str
    title: str
    company: str
    location: str
    description: str


class _Text(HTMLParser):
    BLOCK = {"p", "li", "br", "div", "h1", "h2", "h3", "h4", "ul", "ol", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_text(fragment: str) -> str:
    p = _Text()
    p.feed(fragment)
    lines = [" ".join(line.split()) for line in "".join(p.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def _first(pattern: str, page: str) -> str:
    m = re.search(pattern, page, re.S)
    return html_text(m.group(1)).strip() if m else ""


def parse_posting(job_id: str, page: str) -> Posting | None:
    title = _first(r'class="[^"]*top-card-layout__title[^"]*"[^>]*>(.*?)</h', page)
    company = _first(r'class="[^"]*topcard__org-name-link[^"]*"[^>]*>(.*?)</a>', page)
    location = _first(r'class="[^"]*topcard__flavor--bullet[^"]*"[^>]*>(.*?)</span>', page)
    m = re.search(r'class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', page, re.S)
    description = html_text(m.group(1)) if m else ""
    if not (title and description):
        return None
    return Posting(job_id, title, company, location, description)


def fetch_posting(job_id: str, http: httpx.Client) -> Posting | None:
    """The posting's public page, read once like a person clicking the email link."""
    try:
        r = http.get(
            canonical_url(job_id),
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
            follow_redirects=True,
            timeout=20,
        )
    except httpx.HTTPError as exc:
        log.info("linkedin posting %s unreachable: %s", job_id, exc)
        return None
    if r.status_code != 200 or "authwall" in str(r.url):
        log.info("linkedin posting %s not public (status %s)", job_id, r.status_code)
        return None
    return parse_posting(job_id, r.text)


# -- the same opening on the company's own site -----------------------------------------------


@dataclass(frozen=True)
class CompanyApply:
    url: str
    trusted: bool  # on a known applicant-tracking system (else: the user must allow the site)


_STOP = {"the", "and", "for", "a", "an", "of", "in", "at", "to", "with", "-", "–", "|"}


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#]+", s.lower()) if w not in _STOP}


def _slug(company: str) -> str:
    words = [
        w
        for w in re.findall(r"[a-z0-9]+", company.lower())
        if w not in {"pvt", "ltd", "private", "limited", "inc", "llc", "technologies", "the"}
    ]
    return "".join(words)


def registrable(host: str) -> str:
    parts = host.lower().split(".")
    if (
        len(parts) >= 3
        and parts[-2] in {"co", "com", "org", "net", "ac", "gov"}
        and len(parts[-1]) == 2
    ):
        return ".".join(parts[-3:])  # e.g. company.co.in
    return ".".join(parts[-2:])


def _flat(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def names_this_company(host: str, path: str, slug: str) -> bool:
    """Applicant-tracking page: the company must be in its address (jobs.lever.co/acme,
    acme.wd3.myworkdayjobs.com). Any other site must BE the company's own domain
    (acme.com, careers.acme.io); an aggregator merely mentioning the company in its path
    (bebee.com/.../acme-...) doesn't count."""
    if is_ats(host):
        return slug in _flat(host + path)
    label = _flat(registrable(host).split(".")[0])
    return len(label) >= 3 and (label in slug or slug in label)


def find_on_company_site(search: Searcher, posting: Posting) -> CompanyApply | None:
    """Search for the posting on the company's hiring site; accept only a result whose
    title matches the role and whose address or title names the company."""
    if not posting.company:
        return None
    sites = " OR ".join(f"site:{d[2:]}" for d in ATS_DOMAINS[:8])
    queries = [
        f'"{posting.title}" "{posting.company}" ({sites})',
        f'"{posting.title}" "{posting.company}" careers apply',
    ]
    want = _tokens(posting.title)
    slug = _slug(posting.company)
    for q in queries:
        try:
            results = search.search(q, count=10)
        except SearchError as exc:
            log.info("company-site search failed: %s", exc)
            return None
        for res in results:
            u = urlparse(res.url)
            host = (u.hostname or "").lower()
            if u.scheme != "https" or not host or is_board(host):
                continue
            about = f"{res.title} {res.description} {u.path.replace('/', ' ')}"
            names_company = bool(slug) and names_this_company(host, u.path, slug)
            words = _tokens(about.replace("-", " ").replace("_", " "))
            overlap = len(want & words) / len(want) if want else 0
            if not names_company or overlap < 0.6:
                continue
            return CompanyApply(url=res.url, trusted=is_ats(host))
    return None
