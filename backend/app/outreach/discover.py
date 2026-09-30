"""Finding companies and startups to contact, with web search (Brave)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.boards.linkedin import is_board, registrable
from app.email.links import host_allowed
from app.outreach.crawl import _EMAIL_RE, _SKIP_LOCAL, email_kind
from app.search.web import Searcher

# Not company websites: media, directories, social networks, listicles, marketplaces.
NOT_COMPANIES = [
    "*.wikipedia.org",
    "*.youtube.com",
    "*.facebook.com",
    "*.instagram.com",
    "*.twitter.com",
    "*.x.com",
    "*.reddit.com",
    "*.quora.com",
    "*.medium.com",
    "*.github.com",
    "*.crunchbase.com",
    "*.tracxn.com",
    "*.yourstory.com",
    "*.inc42.com",
    "*.economictimes.com",
    "*.indiatimes.com",
    "*.livemint.com",
    "*.businessinsider.com",
    "*.techcrunch.com",
    "*.forbes.com",
    "*.justdial.com",
    "*.indiamart.com",
    "*.clutch.co",
    "*.goodfirms.co",
    "*.ambitionbox.com",
    "*.builtin.com",
    "*.f6s.com",
    "*.startupindia.gov.in",
    "*.google.com",
    "*.apple.com",
    "*.microsoft.com",
    "*.amazon.com",
    "*.gov.in",
    "*.nic.in",
    "*.edu",
    "*.ac.in",
    "*.substack.com",
    "*.wordpress.com",
    "*.blogspot.com",
    "*.notion.site",
    "*.angel.co",
    "*.producthunt.com",
    "*.g2.com",
    "*.zaubacorp.com",
    "*.tofler.in",
    "*.levels.fyi",
    "*.ycombinator.com",
    "*.seedtable.com",
    "*.startupblink.com",
    "*.jobs",
    "*.topstartups.io",
    "*.meetfrank.com",
    "*.cbinsights.com",
    "*.switchly.in",
    "*.wellfound.com",
    "*.startupjobs.com",
    "*.otta.com",
    "*.welcometothejungle.com",
    "*.remoterocketship.com",
    "*.glassdoor.co.in",
    "*.tracxn.com",
    "*.startuptalky.com",
    "*.growthlist.co",
]

# Result titles that are lists, articles or job pages rather than a company's own site.
_NOT_A_COMPANY_TITLE = re.compile(
    r"^\s*(top|best)?\s*\d+\b|\b(top|best) \d+|list of|\bjobs?\b|\bhiring\b|\bvacanc|"
    r"\bapply\b|remote,|startups? (in|to watch|data)|\bsalar(y|ies)\b|\breview",
    re.I,
)


# Large enterprises: skipped (outreach focuses on startups and mid-size companies).
KNOWN_LARGE = {
    "tcs.com",
    "infosys.com",
    "wipro.com",
    "hcltech.com",
    "techmahindra.com",
    "accenture.com",
    "ibm.com",
    "oracle.com",
    "sap.com",
    "cognizant.com",
    "capgemini.com",
    "deloitte.com",
    "pwc.com",
    "ey.com",
    "kpmg.com",
    "adobe.com",
    "salesforce.com",
    "cisco.com",
    "intel.com",
    "nvidia.com",
    "meta.com",
    "netflix.com",
    "uber.com",
    "flipkart.com",
    "paytm.com",
    "reliance.com",
    "jio.com",
    "hdfcbank.com",
    "icicibank.com",
    "sbi.co.in",
    "ltimindtree.com",
    "mphasis.com",
    "hexaware.com",
    "jpmorgan.com",
    "jpmorganchase.com",
    "goldmansachs.com",
    "morganstanley.com",
    "walmart.com",
    "samsung.com",
    "qualcomm.com",
    "dell.com",
    "hp.com",
    "siemens.com",
    "bosch.com",
    "honeywell.com",
    "philips.com",
    "ge.com",
    "optum.com",
}


@dataclass(frozen=True)
class Candidate:
    name: str
    domain: str
    website: str
    query: str


def is_company_site(host: str) -> bool:
    return bool(host) and not is_board(host) and not host_allowed(host, NOT_COMPANIES)


def company_name(title: str, domain: str) -> str:
    parts = [p.strip() for p in re.split(r"\s[|\-–—:]\s", title) if p.strip()]
    for p in parts:
        if not re.search(r"career|job|hiring|opening|work with|join", p, re.I) and len(p) <= 60:
            return p
    return domain.split(".")[0].replace("-", " ").title()


def queries(roles: list[str], locations: list[str], kinds: list[str]) -> list[str]:
    out: list[str] = []
    for loc in locations or [""]:
        for role in roles or ["software engineer"]:
            if "startups" in kinds:
                out.append(f"{role} startup hiring {loc} careers".strip())
                out.append(f"seed or series A startup {loc} hiring {role}".strip())
            if "companies" in kinds:
                out.append(f"{role} mid-size growing tech company {loc} careers".strip())
    return out


def discover(
    search: Searcher,
    roles: list[str],
    locations: list[str],
    kinds: list[str],
    known_domains: set[str],
    limit: int,
) -> list[Candidate]:
    found: list[Candidate] = []
    seen = set(known_domains)
    for q in queries(roles, locations, kinds):
        for r in search.search(q, count=20):
            host = (urlparse(r.url).hostname or "").lower()
            domain = registrable(host)
            if (
                not is_company_site(host)
                or domain in seen
                or domain in KNOWN_LARGE
                or _NOT_A_COMPANY_TITLE.search(r.title)
            ):
                continue
            seen.add(domain)
            found.append(
                Candidate(
                    name=company_name(r.title, domain),
                    domain=domain,
                    website=f"https://{host}",
                    query=q,
                )
            )
            if len(found) >= limit:
                return found
    return found


_HIRING_NEAR = re.compile(r"career|job|hiring|recruit|talent|\bhr\b|resume|cv\b|apply", re.I)
_GENERIC_LOCAL = re.compile(
    r"^(careers?|jobs?|hr|hiring|talent|recruit(ing|ment)?|people|join(us)?|hello|hi|info|"
    r"contact|team|founders?)$",
    re.I,
)


@dataclass(frozen=True)
class FoundAddress:
    address: str
    source_url: str
    kind: str  # careers | general | person
    source: str  # site | web | ai


def _on_domain(addr: str, domain: str) -> bool:
    host = addr.partition("@")[2]
    return host == domain or host.endswith("." + domain)


def search_emails(
    search: Searcher, name: str, domain: str, verify: Callable[[str, str], bool]
) -> list[FoundAddress]:
    """Hiring addresses at the company's domain that appear on real web pages (company
    listings, job posts, profiles...). Search text that a model wrote is only trusted after
    `verify(page_url, address)` finds the address on that page. Personal addresses count
    only when the text around them is about hiring."""
    found: dict[str, FoundAddress] = {}
    for q in (f'"@{domain}" careers OR jobs OR hiring OR hr', f"{name} careers email hiring hr"):
        for r in search.search(q, count=10):
            text = f"{r.title} {r.description}"
            for m in _EMAIL_RE.finditer(text):
                addr = m.group(0).lower().strip(".")
                local = addr.partition("@")[0]
                if addr in found or not _on_domain(addr, domain) or _SKIP_LOCAL.match(local):
                    continue
                kind = email_kind(local)
                # other addresses don't count as context ("careers@x" next to "ceo@x")
                around = _EMAIL_RE.sub(" ", text[max(0, m.start() - 80) : m.end() + 80])
                if kind == "person" and not _HIRING_NEAR.search(around):
                    continue
                if not r.extracted and not verify(r.url, addr):
                    continue
                found[addr] = FoundAddress(addr, r.url, kind, "web")
    order = {"careers": 0, "general": 1, "person": 2}
    return sorted(found.values(), key=lambda f: order.get(f.kind, 3))


def search_published_email(
    search: Searcher, domain: str, verify: Callable[[str, str], bool]
) -> tuple[str, str] | None:
    """Back-compat helper: the best hiring role inbox from web search, if any."""
    for f in search_emails(search, domain.split(".")[0], domain, verify):
        if f.kind == "careers":
            return f.address, f.source_url
    return None


def has_mail_server(domain: str, transport: httpx.BaseTransport | None = None) -> bool:
    """Does the domain accept email (MX records)? Uses DNS-over-HTTPS (no extra library)."""
    try:
        with httpx.Client(timeout=10, transport=transport) as http:
            r = http.get(
                "https://dns.google/resolve",
                params={"name": domain, "type": "MX"},
                headers={"Accept": "application/dns-json"},
            )
        return r.status_code == 200 and bool(r.json().get("Answer"))
    except (httpx.HTTPError, ValueError):
        return False


def acceptable_ai_address(address: str | None, domain: str) -> str | None:
    """An AI-suggested address is only kept if it's a hiring/general role inbox on the
    company's own domain (never a guessed personal name)."""
    if not address:
        return None
    addr = address.strip().lower()
    if not _EMAIL_RE.fullmatch(addr) or not _on_domain(addr, domain):
        return None
    local = addr.partition("@")[0]
    return addr if _GENERIC_LOCAL.match(local) and not _SKIP_LOCAL.match(local) else None
