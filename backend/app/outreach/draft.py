"""Company research and the cold email draft (both via the LLM gateway)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.generation.facts import FactPack, build_facts
from app.generation.jd import JDAnalysis
from app.llm.errors import LLMError
from app.llm.gateway import LLMGateway
from app.models.enums import LLMTask
from app.outreach.crawl import Page
from app.schemas.settings import DEFAULT_CLOSING_NOTE, Profile

MAX_BODY_WORDS = 220
DEFAULT_AUTHOR = ""  # no hard-coded name: set yours in Settings → Profile


class OpenRole(BaseModel):
    title: str
    url: str | None = None


class CompanyResearch(BaseModel):
    is_company_site: bool = True
    name: str = ""
    what_they_do: str = ""
    products: list[str] = Field(default_factory=list)
    tech: list[str] = Field(default_factory=list)
    open_roles: list[OpenRole] = Field(default_factory=list)
    is_startup: bool | None = None
    size: Literal["startup", "mid-size", "large", "unknown"] = "unknown"
    size_evidence: str = ""
    location: str | None = None
    hook: str = ""


class EmailSuggestion(BaseModel):
    address: str | None = None
    confidence: Literal["seen", "pattern"] = "pattern"
    reason: str = ""


JOB_BOARD = re.compile(
    r"job (board|portal|listings?|search (site|platform))|recruit(ment|ing) (platform|agency|firm)|"
    r"staffing|hiring platform|talent marketplace|directory of|list of (top )?startups",
    re.I,
)


def looks_like_job_board(r: CompanyResearch) -> bool:
    return bool(JOB_BOARD.search(r.what_they_do))


def suggest_email(
    gateway: LLMGateway, name: str, domain: str, r: CompanyResearch, snippets: str = ""
) -> EmailSuggestion | None:
    try:
        return gateway.structured(
            LLMTask.JD_ANALYZER,
            "company_email",
            {"name": name, "domain": domain, "what": r.what_they_do, "snippets": snippets[:2000]},
            EmailSuggestion,
        ).value
    except LLMError:
        return None


class CompanyIdea(BaseModel):
    name: str
    website: str
    why: str = ""


class CompanyIdeas(BaseModel):
    companies: list[CompanyIdea] = Field(default_factory=list)


def suggest_companies(
    gateway: LLMGateway,
    roles: list[str],
    locations: list[str],
    kinds: list[str],
    known: list[str],
    limit: int,
) -> list[CompanyIdea]:
    labels = {"startups": "startups", "companies": "mid-size product companies"}
    try:
        return gateway.structured(
            LLMTask.JD_ANALYZER,
            "company_ideas",
            {
                "roles": ", ".join(roles) or "software engineer",
                "locations": ", ".join(locations) or "India",
                "kinds": " and ".join(labels.get(k, k) for k in kinds) or "startups",
                "known": known[:150],
                "limit": limit,
            },
            CompanyIdeas,
        ).value.companies
    except LLMError:
        return []


class ColdEmailOut(BaseModel):
    subject: str
    greeting: str = "Hi,"
    body: str
    projects_used: list[str] = Field(default_factory=list)
    adaptation: bool = False
    notes: str = ""


def research(
    gateway: LLMGateway, domain: str, pages: list[Page], job_id: int | None = None
) -> CompanyResearch:
    return gateway.structured(
        LLMTask.JD_ANALYZER,
        "company_research",
        {"domain": domain, "pages": [p.__dict__ for p in pages[:5]]},
        CompanyResearch,
        job_id=job_id,
    ).value


def synthetic_jd(company: str, role: str, r: CompanyResearch) -> str:
    """A job description for tailoring the resume to this company (no real posting)."""
    roles = "; ".join(o.title for o in r.open_roles[:6])
    return "\n".join(
        x
        for x in [
            f"{role} at {company}",
            f"About {company}: {r.what_they_do}",
            f"Products: {', '.join(r.products[:8])}" if r.products else "",
            f"Technologies used: {', '.join(r.tech[:15])}" if r.tech else "",
            f"Open roles on their site: {roles}" if roles else "",
            f"Looking for a {role} who can contribute to {company}'s products"
            + (f" using {', '.join(r.tech[:6])}." if r.tech else "."),
        ]
        if x
    )


@dataclass
class Draft:
    subject: str
    body: str
    warnings: list[str] = field(default_factory=list)
    blocking: bool = False  # a warning that must be checked by you before it can go out
    adaptation: bool = False
    notes: str = ""


def signature(profile: Profile, name: str = "") -> str:
    lines = ["Best regards,", profile.full_name or name]
    if profile.phone:
        lines.append(profile.phone)
    if profile.email:
        lines.append(str(profile.email))
    for label in ("linkedin", "github", "portfolio"):
        if label in profile.links:
            lines.append(str(profile.links[label]))
    return "\n".join(x for x in lines if x)


def author_name(profile: Profile) -> str:
    """Your name as it appears on the resume (profile name, else the resume author)."""
    return profile.full_name.strip() or DEFAULT_AUTHOR


def disclosure(name: str, note: str = DEFAULT_CLOSING_NOTE) -> str:
    """The closing note that says your agent sent the email ({name} = your name)."""
    return note.replace("{name}", name or "me").strip()


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?%?", text))


def draft_email(
    db: Session,
    gateway: LLMGateway,
    *,
    profile: Profile,
    company: str,
    website: str,
    role: str,
    r: CompanyResearch,
    base_regions: dict[str, str],
    job_id: int | None = None,
    closing_note: str = DEFAULT_CLOSING_NOTE,
    sender_name: str | None = None,
    jd_text: str | None = None,
    jd_structured: dict[str, Any] | None = None,
) -> Draft:
    if jd_text and jd_structured:  # a real job you gave: its analysed requirements
        analysis = JDAnalysis.model_validate(
            {k: v for k, v in jd_structured.items() if k in JDAnalysis.model_fields}
        )
        jd = jd_text
    else:
        analysis = JDAnalysis(
            role=role,
            company=company,
            required_skills=r.tech[:12],
            keywords=r.products[:8],
            responsibilities=[r.what_they_do] if r.what_they_do else [],
        )
        jd = jd_text or synthetic_jd(company, role, r)
    pack: FactPack = build_facts(db, gateway, analysis, jd, base_regions, k=25)
    prof = profile.model_dump(mode="json", exclude={"form_fields"})
    out = gateway.structured(
        LLMTask.RESUME_WRITER,
        "cold_email",
        {
            "name": sender_name or author_name(profile),
            "company": company,
            "website": website,
            "role": role,
            "profile": json.dumps(prof, indent=1)[:3000],
            "research": r.model_dump_json(indent=1)[:4000],
            "facts": pack.as_prompt_lines(),
            "job_posting": (jd_text or "")[:6000],
            "requirements": ", ".join(
                [*analysis.required_skills[:10], *analysis.preferred_skills[:6]]
            ),
        },
        ColdEmailOut,
        job_id=job_id,
    ).value
    name = sender_name or author_name(profile)
    body = "\n\n".join(
        x.strip()
        for x in (
            out.greeting,
            out.body,
            signature(profile, name),
            "—\n" + disclosure(name, closing_note),
        )
        if x.strip()
    )
    warnings: list[str] = []
    allowed = _numbers(pack.text + " " + r.model_dump_json() + " " + json.dumps(prof))
    unknown = sorted(_numbers(out.body) - allowed)
    blocking = False
    if unknown:
        blocking = True  # a possibly untrue claim: never sent automatically
        warnings.append(
            "Numbers not found in your facts or the company's site: "
            + ", ".join(unknown)
            + ". Check them before sending (this draft won't be sent automatically)."
        )
    if len(out.body.split()) > MAX_BODY_WORDS:
        warnings.append(f"Long email ({len(out.body.split())} words); shorter gets more replies.")
    if out.adaptation:
        warnings.append(
            "No project matched this company directly, so the email pitches adapting one "
            "of your projects. Make sure you're happy with that pitch."
        )
    return Draft(out.subject.strip()[:200], body, warnings, blocking, out.adaptation, out.notes)


def research_dict(r: CompanyResearch) -> dict[str, Any]:
    return r.model_dump(mode="json")
