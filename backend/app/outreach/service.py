"""Outreach state machine, advanced a little on every worker tick.

company:  new → researched (emails found) → queued (resume being tailored)
          new → no_email | skipped (not a company site / robots.txt) | failed
email:    resume_pending → ready (draft waiting for you) → approved (you clicked Send)
          → sent | failed;  ready → skipped (you skipped it)

Nothing is ever sent without your approval; at most `outreach_daily_cap` emails are sent
(and drafted) per 24 hours, and each company gets at most one email.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.boards.linkedin import is_ats, registrable, title_skipped
from app.boards.service import ensure_portal
from app.llm.errors import LLMError
from app.llm.gateway import LLMGateway
from app.models import (
    EmailAccount,
    Job,
    OutreachCompany,
    OutreachEmail,
    OutreachSource,
    PipelineRun,
    Resume,
    Setting,
)
from app.models.enums import (
    ConnectionStatus,
    EmailProvider,
    JobSource,
    JobStatus,
    NotificationLevel,
    ResumeKind,
    RunState,
)
from app.outreach.crawl import _SKIP_LOCAL, SiteReader, email_kind
from app.outreach.directory import DirectoryRead, read_directory
from app.outreach.directory import score as directory_score
from app.outreach.discover import (
    KNOWN_LARGE,
    Candidate,
    acceptable_ai_address,
    discover,
    has_mail_server,
    is_company_site,
    search_emails,
)
from app.outreach.draft import (
    CompanyResearch,
    draft_email,
    looks_like_job_board,
    research,
    suggest_companies,
    suggest_email,
    synthetic_jd,
)
from app.outreach.send import SendError, build_message, send
from app.search.web import Searcher, SearchError, make_searcher
from app.services.notifications import notify
from app.services.settings_service import (
    display_name,
    get_app_settings,
    get_profile,
    resume_filename,
)
from app.services.templates import active_template, data_root

log = logging.getLogger(__name__)

LAST_SEARCH_KEY = "outreach.last_search"
RETRY_SEND_HOURS = 24  # temporary send failures are retried this long
RESEARCH_RETRY_MINUTES = 30  # wait after an AI rate limit before researching again
RESEARCH_PER_TICK = 2
QUEUE_PER_TICK = 1


def _since_minutes(minutes: int) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


def _since(hours: int) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


def default_role(roles: list[str]) -> str:
    return roles[0] if roles else "Software Engineer"


def add_company(db: Session, website: str, *, name: str | None = None) -> OutreachCompany:
    from urllib.parse import urlparse

    from app.boards.linkedin import registrable

    url = website.strip()
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    if not host or "." not in host:
        raise ValueError("enter a company website, e.g. https://example.com")
    domain = registrable(host)
    existing = db.scalar(select(OutreachCompany).where(OutreachCompany.domain == domain))
    if existing is not None:
        return existing
    c = OutreachCompany(
        name=name or domain.split(".")[0].replace("-", " ").title(),
        domain=domain,
        website=f"https://{host}",
        source="manual",
        status="new",
    )
    db.add(c)
    db.flush()
    return c


FREE_MAIL = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.in",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "msn.com",
    "icloud.com",
    "me.com",
    "proton.me",
    "protonmail.com",
    "rediffmail.com",
    "aol.com",
    "gmx.com",
    "yandex.com",
    "zohomail.in",
}


def add_by_email(db: Session, address: str, name: str | None = None) -> OutreachCompany:
    """You gave only an email address. A work address → learn the company from its domain's
    website; a personal (gmail...) address → no site to read, write from your profile."""
    addr = address.strip().lower()
    domain = addr.partition("@")[2]
    if domain and domain not in FREE_MAIL:
        c = add_company(db, f"https://{domain}", name=name)
    else:
        found = db.scalar(select(OutreachCompany).where(OutreachCompany.domain == addr))
        if found is not None:
            c = found
        else:
            label = name or addr.partition("@")[0].replace(".", " ").title()
            c = OutreachCompany(
                name=label[:200],
                domain=addr,  # unique key: no company domain for personal addresses
                website="",
                source="manual",
                status="researched",
                summary={"name": label, "is_company_site": True},
            )
            db.add(c)
            db.flush()
    set_user_email(db, c, addr)
    return c


def set_job_description(
    db: Session, company: OutreachCompany, jd_text: str, role: str | None = None
) -> None:
    """A specific job you're emailing about: the resume and the email are tailored to it."""
    company.jd_text = jd_text.strip()[:30000]
    company.role = (role or "").strip()[:200] or None
    email = db.scalar(select(OutreachEmail).where(OutreachEmail.company_id == company.id))
    if email is not None and email.status in ("ready", "failed", "resume_pending", "skipped"):
        # redo the tailored resume and draft for this JD
        db.delete(email)
        if company.status in ("queued", "skipped"):
            company.status = "researched" if company.summary else "new"


def set_user_email(db: Session, company: OutreachCompany, address: str) -> None:
    """Your address for this company: always used first (and may be sent automatically)."""
    addr = address.strip().lower()
    entry = {
        "address": addr,
        "source_url": "",
        "kind": email_kind(addr.split("@")[0]),
        "source": "user",
    }
    company.emails = [entry] + [e for e in (company.emails or []) if e.get("address") != addr]
    if company.status in ("no_email", "watching", "failed", "skipped"):
        company.status, company.error = ("researched" if company.summary else "new"), None
    email = db.scalar(select(OutreachEmail).where(OutreachEmail.company_id == company.id))
    if email is not None and email.status in ("ready", "failed", "resume_pending"):
        email.to_address = addr
        email.warnings = [w for w in (email.warnings or []) if "suggested by the AI" not in w]


# -- 1. discovery ------------------------------------------------------------------------------


def run_discovery(
    db: Session,
    search: Searcher,
    *,
    force: bool = False,
    gateway: LLMGateway | None = None,
    read_dir: Callable[[str], DirectoryRead] = read_directory,
) -> int:
    s = get_app_settings(db)
    row = db.get(Setting, LAST_SEARCH_KEY)
    last = datetime.fromisoformat(row.value) if row and row.value else None
    if not force and last and last > _since(s.outreach_search_every_hours):
        return 0
    known = set(db.scalars(select(OutreachCompany.domain)))
    db.commit()
    limit = s.outreach_new_per_search
    found: list[Candidate] = []
    locations: dict[str, str | None] = {}
    # 0) startup directories you added: best-fitting unseen startups first
    sources = [
        (x.id, x.url, x.label or x.url, x.location)
        for x in db.scalars(select(OutreachSource).where(OutreachSource.enabled.is_(True)))
    ]
    db.commit()
    taken_dir = set(known)
    for sid, url, label, loc in sources:
        if len(found) >= limit:
            break
        got = read_dir(url)  # network: no transaction open
        src = db.get(OutreachSource, sid)
        assert src is not None
        src.last_read_at, src.error, src.entries_found = (
            datetime.now(UTC),
            got.blocked,
            len(got.entries),
        )
        fresh = [e for e in got.entries if e.domain not in taken_dir]
        fresh.sort(key=lambda e: -directory_score(e, s.outreach_roles))
        for e in fresh[: limit - len(found)]:
            taken_dir.add(e.domain)
            locations[e.domain] = loc
            src.added_total += 1
            found.append(
                Candidate(
                    name=e.name,
                    domain=e.domain,
                    website=e.website,
                    query=f"{label}: {e.stage + ' · ' if e.stage else ''}{e.about}"[:500],
                )
            )
        db.commit()
    known = known | taken_dir
    # 1) the AI (chatbot) suggests startups / mid-size companies; each is verified later by
    #    reading its website (research skips unreadable sites, job boards, large firms)
    if gateway is not None and len(found) < limit:
        taken = set(known)
        for idea in suggest_companies(
            gateway,
            s.outreach_roles,
            s.outreach_locations,
            list(s.outreach_company_kinds),
            sorted(taken),
            limit + 3,
        ):
            url = idea.website if "://" in idea.website else "https://" + idea.website
            host = (urlparse(url).hostname or "").lower()
            domain = registrable(host) if host else ""
            if not domain or domain in taken or domain in KNOWN_LARGE or not is_company_site(host):
                continue
            taken.add(domain)
            found.append(
                Candidate(
                    name=idea.name[:200],
                    domain=domain,
                    website=f"https://{host}",
                    query=f"AI: {idea.why}"[:500],
                )
            )
            if len(found) >= limit:
                break
    # 2) web search fills any remaining slots
    if len(found) < limit:
        found += discover(
            search,
            s.outreach_roles,
            s.outreach_locations,
            list(s.outreach_company_kinds),
            known | {c.domain for c in found},
            limit - len(found),
        )
    for c in found:
        db.add(
            OutreachCompany(
                name=c.name,
                domain=c.domain,
                website=c.website,
                source="search",
                source_query=c.query,
                location=locations.get(c.domain) or ", ".join(s.outreach_locations[:1]) or None,
                status="new",
            )
        )
    row = row or Setting(key=LAST_SEARCH_KEY, is_secret=False)
    row.value = datetime.now(UTC).isoformat()
    db.add(row)
    db.commit()
    return len(found)


# -- 2. research -------------------------------------------------------------------------------


def research_company(
    db: Session,
    gateway: LLMGateway,
    company_id: int,
    reader: SiteReader,
    search: Searcher | None,
) -> None:
    c = db.get(OutreachCompany, company_id)
    if c is None:
        return
    website, domain, c_name = c.website, c.domain, c.name
    prior_emails = list(c.emails or [])
    db.commit()  # no transaction open during network calls
    site = reader.read(website)
    r: CompanyResearch | None = None
    error: str | None = site.blocked
    if not site.blocked and site.pages:
        try:
            r = research(gateway, domain, site.pages)
        except LLMError as exc:
            if getattr(exc, "transient", False):
                # AI rate limit / outage: not a real failure; research again later
                c = db.get(OutreachCompany, company_id)
                assert c is not None
                c.status, c.researched_at = "new", datetime.now(UTC)
                c.error = f"Will retry in {RESEARCH_RETRY_MINUTES} min (AI busy or rate-limited)"
                db.commit()
                return
            error = f"research failed: {str(exc)[:200]}"
    site_emails = [e.__dict__ | {"source": "site"} for e in site.emails]
    is_company = r is not None and r.is_company_site and not looks_like_job_board(r)
    user_emails = [e for e in (prior_emails or []) if e.get("source") == "user"]
    emails: list[dict[str, Any]] = []
    if r is not None and is_company:
        # 1) a hiring email (careers@ / jobs@ / hr@ ...) on their site or on the web
        hiring = [e for e in site_emails if e["kind"] != "general"]
        if not hiring and not user_emails and search is not None:
            try:
                hiring = [
                    f.__dict__
                    for f in search_emails(search, r.name or c_name, domain, reader.page_contains)
                ]
            except SearchError as exc:
                log.info("email web search skipped: %s", exc)
        # 2) else their Contact Us / general inbox
        general = [e for e in site_emails if e["kind"] == "general"]
        emails = rank_addresses(user_emails + hiring + general)
        # 3) else the AI's suggestion (always waits for your OK)
        if not emails:
            emails = find_elsewhere(gateway, r.name or c_name, domain, r, reader, None)
    elif user_emails:
        emails = user_emails
    careers_url = careers_page(site.pages, r, domain)

    c = db.get(OutreachCompany, company_id)
    assert c is not None
    c.researched_at = datetime.now(UTC)
    c.emails = emails
    c.careers_url = careers_url or c.careers_url
    size_skip: str | None = None
    if r is not None:
        c.summary = r.model_dump(mode="json")
        c.name = r.name or c.name
        c.location = r.location or c.location
        c.size = "large" if c.domain in KNOWN_LARGE else verified_size(r, site.pages)
        wanted: list[str] = list(get_app_settings(db).outreach_company_sizes)
        if (c.size or "unknown") not in wanted:
            quote = f' ("{r.size_evidence[:80]}")' if r.size_evidence else ""
            size_skip = f"{c.size} company{quote}: outreach focuses on " + ", ".join(wanted)
    if error and r is None:
        c.status, c.error = ("skipped" if site.blocked else "failed"), error
    elif r is not None and not is_company:
        c.status, c.error = (
            "skipped",
            "not a company to apply to (job board, recruiter, directory or news site)",
        )
    elif size_skip:
        c.status, c.error = "skipped", size_skip
    elif not emails and c.careers_url:
        # 4) no email anywhere: watch their careers page and apply when a match is posted
        c.status, c.error = "watching", None
    elif not emails:
        c.status, c.error = (
            "no_email",
            "no email found (site, web or AI) and no careers page to watch",
        )
    else:
        c.status, c.error = "researched", None
    db.commit()


def rank_addresses(emails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Yours first, then hiring inboxes, hiring people, contact-us, AI guesses (deduped)."""

    def score(e: dict[str, Any]) -> int:
        if e.get("source") == "user":
            return 0
        if e.get("source") == "ai":
            return 5
        return {"careers": 1, "person": 2, "general": 3}.get(str(e.get("kind")), 4)

    out: list[dict[str, Any]] = []
    for e in sorted(emails, key=score):
        if all(x["address"] != e["address"] for x in out):
            out.append(e)
    return out


def careers_page(pages: list[Any], r: CompanyResearch | None, domain: str) -> str | None:
    """The company's careers page: on their own site, or their applicant-tracking page."""
    for p in pages:
        if getattr(p, "careers", False):
            return str(p.url)
    for role in r.open_roles if r else []:
        host = (urlparse(role.url or "").hostname or "").lower()
        if host and (registrable(host) == domain or is_ats(host)):
            return role.url
    return None


def find_elsewhere(
    gateway: LLMGateway,
    name: str,
    domain: str,
    r: CompanyResearch,
    reader: SiteReader,
    search: Searcher | None,
) -> list[dict[str, Any]]:
    """No address on the company's site: look on the web, then ask the AI.

    Web: addresses on real pages (verified). AI: a role inbox on the company's domain whose
    domain accepts mail; marked source "ai" so the email always waits for your approval."""
    snippets = ""
    if search is not None:
        try:
            found = search_emails(search, name, domain, reader.page_contains)
        except SearchError as exc:
            log.info("email web search skipped: %s", exc)
            found = []
        if found:
            return [f.__dict__ for f in found]
        try:
            snippets = "\n".join(
                f"- {x.title}: {x.description[:200]}"
                for x in search.search(f"{name} {domain} careers contact", count=5)
            )
        except SearchError:
            snippets = ""
    guess = suggest_email(gateway, name, domain, r, snippets)
    addr = acceptable_ai_address(guess.address if guess else None, domain)
    if addr and has_mail_server(domain):
        return [
            {
                "address": addr,
                "source_url": "",
                "kind": email_kind(addr.split("@")[0]),
                "source": "ai",
                "note": (guess.reason if guess else "")[:120],
            }
        ]
    return []


def verified_size(r: CompanyResearch, pages: list[Any]) -> str | None:
    """The AI's size only counts with a quote that's really on the site."""
    if r.size == "unknown":
        return None
    ev = " ".join(r.size_evidence.split()).lower()
    text = " ".join(" ".join(p.text for p in pages).split()).lower()
    return r.size if ev and ev in text else None


# -- watching careers pages (no email anywhere) ----------------------------------------------

_TECH_TITLE = re.compile(
    r"engineer|developer|sde|programmer|scientist|analyst|intern|trainee|architect|devops|"
    r"\bml\b|\bai\b|backend|frontend|full ?stack|software",
    re.I,
)


def matches_roles(title: str, roles: list[str], skip_words: list[str]) -> bool:
    """A tech opening that shares a word with one of your roles and isn't a skipped
    (senior) title. The AI level check still runs before any resume is made."""
    if not _TECH_TITLE.search(title) or title_skipped(title, skip_words):
        return False
    words = {w for w in re.findall(r"[a-z]+", title.lower()) if len(w) > 2}
    wanted = {w for r in roles for w in re.findall(r"[a-z]+", r.lower()) if len(w) > 2}
    return not wanted or bool(words & wanted)


def check_careers_page(
    db: Session, gateway: LLMGateway, company_id: int, reader: SiteReader
) -> int:
    """Read a watched company's careers page; new matching openings become jobs (the normal
    pipeline tailors the resume and applies on their site). Returns jobs created."""
    c = db.get(OutreachCompany, company_id)
    if c is None or not c.careers_url:
        return 0
    url, domain, name = c.careers_url, c.domain, c.name
    seen = set(c.seen_openings or [])
    s = get_app_settings(db)
    db.commit()
    site = reader.read(url)
    try:
        r = research(gateway, domain, site.pages) if site.pages else None
    except LLMError as exc:
        log.info("careers page check failed for %s: %s", name, exc)
        r = None
    c = db.get(OutreachCompany, company_id)
    assert c is not None
    c.watch_checked_at = datetime.now(UTC)
    created = 0
    for role in r.open_roles if r else []:
        key = (role.url or role.title).strip().lower()
        if not role.title or key in seen:
            continue
        seen.add(key)
        if not matches_roles(role.title, s.outreach_roles, s.skip_title_words):
            continue
        link = role.url or url
        host = (urlparse(link).hostname or "").lower()
        if not host or not (registrable(host) == domain or is_ats(host)):
            link, host = url, (urlparse(url).hostname or "").lower()
        dedupe = f"careers:{domain}:{key}"[:500]
        if db.scalar(select(Job.id).where(Job.dedupe_key == dedupe)):
            continue
        job = Job(
            source=JobSource.SITE,
            status=JobStatus.DETECTED,
            company=name,
            role=role.title[:300],
            apply_url=link,
            portal_id=ensure_portal(db, link).id,
            dedupe_key=dedupe,
            notes=f"Found on {name}'s careers page (watched because they publish no email).",
            detected_at=datetime.now(UTC),
            scheduled_at=datetime.now(UTC),
        )
        db.add(job)
        db.flush()
        created += 1
        notify(
            db,
            NotificationLevel.INFO,
            "outreach.opening",
            f"New opening at {name}: {role.title}",
            "The agent is preparing a tailored resume and will apply on their careers site "
            "(following your auto-apply setting).",
            job_id=job.id,
        )
    c.seen_openings = sorted(seen)[-500:]
    db.commit()
    return created


# -- 3. tailored resume -----------------------------------------------------------------------


def queue_company(
    db: Session, company: OutreachCompany, queue_generation: Callable[..., object]
) -> OutreachEmail:
    s = get_app_settings(db)
    r = CompanyResearch.model_validate(company.summary or {})
    if company.jd_text:  # you gave the job description: tailor to it (role read from it)
        role: str | None = company.role
        jd = company.jd_text
        note = f"Tailored to the job description you gave, for an email to {company.name}."
    else:
        role = default_role(s.outreach_roles)
        jd = synthetic_jd(company.name, role, r)
        note = f"Tailored resume for a cold email to {company.name} ({company.website})."
    job = Job(
        source=JobSource.MANUAL,
        status=JobStatus.DETECTED,
        company=company.name,
        role=role,
        jd_text=jd,
        notes=note,
        detected_at=datetime.now(UTC),
    )
    db.add(job)
    db.flush()
    email = OutreachEmail(
        company_id=company.id,
        job_id=job.id,
        to_address=company.emails[0]["address"],
        status="resume_pending",
    )
    db.add(email)
    company.status = "queued"
    db.commit()
    queue_generation(db, job, force=True)  # no eligibility rules for a cold email
    return email


# -- 4. drafting -------------------------------------------------------------------------------


def draft_ready(db: Session, gateway: LLMGateway, email_id: int) -> None:
    e = db.get(OutreachEmail, email_id)
    if e is None or e.job_id is None:
        return
    resume = db.scalar(
        select(Resume)
        .where(Resume.job_id == e.job_id, Resume.kind == ResumeKind.GENERATED)
        .order_by(Resume.id.desc())
    )
    if resume is None:
        run = db.scalar(
            select(PipelineRun)
            .where(PipelineRun.job_id == e.job_id)
            .order_by(PipelineRun.id.desc())
        )
        if run is not None and run.state is RunState.FAILED:
            e.status, e.error = "failed", f"the tailored resume failed: {(run.error or '')[:300]}"
            db.commit()
        return
    company = db.get(OutreachCompany, e.company_id)
    assert company is not None
    from app.generation.pipeline import _base_regions

    template = active_template(db)
    base = _base_regions(template.tex_source) if template else {}
    s = get_app_settings(db)
    job = db.get(Job, e.job_id)
    try:
        d = draft_email(
            db,
            gateway,
            profile=get_profile(db),
            company=company.name,
            website=company.website,
            role=(job.role if job else None) or default_role(s.outreach_roles),
            r=CompanyResearch.model_validate(company.summary or {}),
            jd_text=company.jd_text,
            jd_structured=job.jd_structured if job else None,
            base_regions=base,
            job_id=e.job_id,
            closing_note=s.outreach_closing_note,
            sender_name=display_name(db),
        )
    except LLMError as exc:
        if getattr(exc, "transient", False):
            return  # try again next tick
        e.status, e.error = "failed", f"drafting failed: {str(exc)[:300]}"
        db.commit()
        return
    e = db.get(OutreachEmail, email_id)
    assert e is not None
    e.subject, e.body, e.warnings = d.subject, d.body, d.warnings
    e.resume_id, e.status, e.error = resume.id, "ready", None
    chosen = next((x for x in company.emails if x.get("address") == e.to_address), {})
    local = e.to_address.partition("@")[0]
    if chosen.get("source") != "user" and _SKIP_LOCAL.match(local):
        d.blocking = True
        e.warnings = [
            *e.warnings,
            f"{local}@ looks like a non-hiring inbox (marketing, sales, support...). Change the "
            "address to a careers/HR/contact one, or skip.",
        ]
    if chosen.get("source") == "ai":
        d.blocking = True
        e.warnings = [
            *e.warnings,
            f"{e.to_address} was suggested by the AI; the company doesn't publish it anywhere "
            "the agent could find. Confirm it (or change it) before sending.",
        ]
    auto = s.outreach_auto_send and not d.blocking
    # an address you gave yourself goes out straight away (no review window)
    delay = 0 if chosen.get("source") == "user" else s.outreach_send_delay_minutes
    when = datetime.now(UTC) + timedelta(minutes=delay)
    e.auto_send_at = when if auto else None
    db.commit()
    if auto:
        notify(
            db,
            NotificationLevel.INFO,
            "outreach.scheduled",
            f"Cold email to {company.name} sends at {when:%H:%M} UTC",
            f"To {e.to_address}. It goes out automatically unless you skip or edit it on the "
            "Outreach page.",
        )
    else:
        notify(
            db,
            NotificationLevel.WARNING if d.blocking else NotificationLevel.INFO,
            "outreach.ready",
            f"Cold email needs your review: {company.name}",
            f"To {e.to_address}. "
            + (
                "It has something to check (see the warning), so it waits for your Send."
                if d.blocking
                else "Review it on the Outreach page and click Send."
            ),
        )


# -- 5. sending --------------------------------------------------------------------------------


def sender_account(db: Session) -> EmailAccount | None:
    s = get_app_settings(db)
    if s.outreach_account_id:
        return db.get(EmailAccount, s.outreach_account_id)
    return db.scalar(
        select(EmailAccount)
        .where(
            EmailAccount.provider.in_([EmailProvider.GMAIL_API, EmailProvider.OUTLOOK_GRAPH]),
            EmailAccount.status == ConnectionStatus.CONNECTED,
        )
        .order_by(EmailAccount.id)
    ) or db.scalar(select(EmailAccount).order_by(EmailAccount.id))


def sent_last_day(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(OutreachEmail)
            .where(OutreachEmail.sent_at.is_not(None), OutreachEmail.sent_at >= _since(24))
        )
        or 0
    )


def send_approved(db: Session, email_id: int) -> None:
    e = db.get(OutreachEmail, email_id)
    if e is None or e.status != "approved":
        return
    s = get_app_settings(db)
    if sent_last_day(db) >= s.outreach_daily_cap:
        return  # daily cap reached: it goes out in a later tick
    account = sender_account(db)
    if account is None:
        e.status, e.error = "failed", "connect a mailbox in Settings → Email to send from"
        db.commit()
        return
    resume = db.get(Resume, e.resume_id) if e.resume_id else None
    pdf = data_root() / resume.pdf_path if resume and resume.pdf_path else None
    if pdf is None or not pdf.exists():
        e.status, e.error = "failed", "the tailored resume PDF is missing"
        db.commit()
        return
    message = build_message(
        from_name=display_name(db),
        from_address=account.address,
        to=e.to_address,
        subject=e.subject or f"{default_role(s.outreach_roles)} opportunities",
        body=e.body or "",
        attachment=pdf,
        attachment_name=resume_filename(db),
    )
    company = db.get(OutreachCompany, e.company_id)
    try:
        msg_id = send(db, account, message)
    except SendError as exc:
        approved = (
            e.approved_at.replace(tzinfo=UTC)
            if e.approved_at and not e.approved_at.tzinfo
            else e.approved_at
        )
        if exc.transient and approved and approved > _since(RETRY_SEND_HOURS):
            # offline laptop / DNS / provider hiccup: stays approved, retried every tick
            first = not (e.error or "").startswith("Will retry")
            e.error = f"Will retry automatically: {str(exc)[:500]}"
            db.commit()
            if first:
                notify(
                    db,
                    NotificationLevel.INFO,
                    "outreach.retrying",
                    f"Cold email delayed: {company.name if company else e.to_address}",
                    "The network or mail service wasn't reachable (e.g. the laptop was asleep "
                    "or offline). It will be sent automatically once it's back.",
                )
            return
        e.status, e.error = "failed", str(exc)[:1000]
        db.commit()
        notify(
            db,
            NotificationLevel.WARNING,
            "outreach.failed",
            f"Cold email not sent: {company.name if company else ''}",
            e.error,
        )
        return
    e.status, e.sent_at, e.provider_message_id, e.error = "sent", datetime.now(UTC), msg_id, None
    db.commit()
    notify(
        db,
        NotificationLevel.INFO,
        "outreach.sent",
        f"Cold email sent: {company.name if company else e.to_address}",
        f"To {e.to_address}.",
    )


# -- the tick ------------------------------------------------------------------------------------


def process_outreach(
    gateway: LLMGateway,
    sessions: Callable[[], Session],
    queue_generation: Callable[..., object],
    *,
    reader: SiteReader | None = None,
    search: Searcher | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    with sessions() as db:
        s = get_app_settings(db)
        search = search or make_searcher(db, gateway)

    if s.outreach_enabled and search is not None:
        with sessions() as db:
            try:
                report["discovered"] = run_discovery(db, search, gateway=gateway)
            except SearchError as exc:
                report["discovery_error"] = str(exc)
                log.warning("outreach discovery failed: %s", exc)

    own_reader = reader is None
    reader = reader or SiteReader()
    try:
        with sessions() as db:
            due_watch = list(
                db.scalars(
                    select(OutreachCompany.id)
                    .where(
                        OutreachCompany.status == "watching",
                        (OutreachCompany.watch_checked_at.is_(None))
                        | (OutreachCompany.watch_checked_at <= _since(s.outreach_watch_hours)),
                    )
                    .order_by(OutreachCompany.id)
                    .limit(RESEARCH_PER_TICK)
                )
            )
        for cid in due_watch:
            with sessions() as db:
                report["openings"] = report.get("openings", 0) + check_careers_page(
                    db, gateway, cid, reader
                )
        with sessions() as db:
            new_ids = list(
                db.scalars(
                    select(OutreachCompany.id)
                    .where(
                        OutreachCompany.status == "new",
                        (OutreachCompany.researched_at.is_(None))
                        | (OutreachCompany.researched_at <= _since_minutes(RESEARCH_RETRY_MINUTES)),
                    )
                    .order_by(OutreachCompany.id)
                    .limit(RESEARCH_PER_TICK)
                )
            )
        for cid in new_ids:
            with sessions() as db:
                research_company(db, gateway, cid, reader, search)
    finally:
        if own_reader:
            reader.close()

    with sessions() as db:
        drafted_today = int(
            db.scalar(
                select(func.count())
                .select_from(OutreachEmail)
                .where(OutreachEmail.created_at >= _since(24))
            )
            or 0
        )
        if drafted_today < s.outreach_daily_cap:
            for c in db.scalars(
                select(OutreachCompany)
                .where(OutreachCompany.status == "researched")
                .order_by(OutreachCompany.id)
                .limit(QUEUE_PER_TICK)
            ):
                queue_company(db, c, queue_generation)
        # automatic drafts whose review window has passed are approved (you can still skip)
        for due in db.scalars(
            select(OutreachEmail).where(
                OutreachEmail.status == "ready",
                OutreachEmail.auto_send_at.is_not(None),
                OutreachEmail.auto_send_at <= datetime.now(UTC),
            )
        ):
            due.status, due.approved_at = "approved", datetime.now(UTC)
        db.commit()
        pending = list(
            db.scalars(select(OutreachEmail.id).where(OutreachEmail.status == "resume_pending"))
        )
        approved = list(
            db.scalars(select(OutreachEmail.id).where(OutreachEmail.status == "approved"))
        )
    for eid in pending:
        with sessions() as db:
            draft_ready(db, gateway, eid)
    for eid in approved:
        with sessions() as db:
            send_approved(db, eid)
    return report
