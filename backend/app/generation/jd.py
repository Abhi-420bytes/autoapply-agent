"""JD analysis (LLM, guarded) and eligibility check against the user's profile."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.knowledge.skills import normalize
from app.llm.gateway import LLMGateway, StructuredResult
from app.models.enums import LLMTask
from app.schemas.settings import Profile

JD_PROMPT_CHARS = 20_000


class Eligibility(BaseModel):
    min_cgpa: float | None = Field(default=None, description="CGPA cutoff on a 10-point scale")
    branches: list[str] = Field(default_factory=list, description="eligible degrees/branches")
    batches: list[int] = Field(default_factory=list, description="eligible graduation years")
    other: list[str] = Field(default_factory=list, description="other stated criteria")


class JDAnalysis(BaseModel):
    company: str | None = None
    role: str | None = None
    role_category: str | None = Field(
        default=None, description="e.g. backend, frontend, full-stack, data, ML, devops, QA"
    )
    seniority: str | None = Field(default=None, description="intern / entry / mid / senior")
    required_skills: list[str] = Field(default_factory=list, max_length=40)
    preferred_skills: list[str] = Field(default_factory=list, max_length=40)
    keywords: list[str] = Field(default_factory=list, max_length=40)
    responsibilities: list[str] = Field(default_factory=list, max_length=20)
    eligibility: Eligibility = Field(default_factory=Eligibility)
    location: str | None = None
    ctc: str | None = None
    deadline: str | None = Field(default=None, description="as written in the JD")


def _in_text(term: str, text_norm: str) -> bool:
    t = normalize(term)
    return bool(t) and f" {t} " in f" {text_norm} "


def guard_analysis(a: JDAnalysis, jd_text: str) -> tuple[JDAnalysis, list[str]]:
    """Keep only skills/keywords that literally appear in the JD."""
    text_norm = normalize(jd_text)
    dropped: list[str] = []

    def keep(items: list[str], label: str) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            key = normalize(item)
            if not key or key in seen:
                continue
            if _in_text(item, text_norm):
                out.append(item.strip())
                seen.add(key)
            else:
                dropped.append(f"{label}: {item}")
        return out

    required = keep(a.required_skills, "required")
    preferred = [
        p
        for p in keep(a.preferred_skills, "preferred")
        if normalize(p) not in {normalize(r) for r in required}
    ]
    keywords = keep(a.keywords, "keyword")
    return a.model_copy(
        update={"required_skills": required, "preferred_skills": preferred, "keywords": keywords}
    ), dropped


def analyze_jd(
    gateway: LLMGateway, jd_text: str, *, job_id: int | None = None, urgent: bool = False
) -> tuple[JDAnalysis, list[str], StructuredResult[JDAnalysis]]:
    result = gateway.structured(
        LLMTask.JD_ANALYZER,
        "jd_analysis",
        {"jd_text": jd_text[:JD_PROMPT_CHARS]},
        JDAnalysis,
        job_id=job_id,
        urgent=urgent,
    )
    clean, dropped = guard_analysis(result.value, jd_text)
    return clean, dropped, result


class EligibilityResult(BaseModel):
    eligible: bool | None  # None = can't tell (profile incomplete)
    reasons: list[str] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)


_BRANCH_ALIASES = {
    "cse": "computer science",
    "cs": "computer science",
    "it": "information technology",
    "ece": "electronics and communication",
    "eee": "electrical and electronics",
    "ee": "electrical",
    "me": "mechanical",
    "mech": "mechanical",
    "ce": "civil",
    "ai": "artificial intelligence",
    "aiml": "artificial intelligence",
    "ds": "data science",
}


def _branch_key(s: str) -> str:
    n = normalize(s)
    for w in ("engineering", "bachelor of technology", "b tech", "btech", "b e", "in", "and"):
        n = re.sub(rf"\b{w}\b", " ", n)
    n = " ".join(n.split())
    return _BRANCH_ALIASES.get(n.replace(" ", ""), _BRANCH_ALIASES.get(n, n))


def check_eligibility(e: Eligibility, profile: Profile) -> EligibilityResult:
    if not (e.min_cgpa or e.branches or e.batches):
        return EligibilityResult(eligible=True, reasons=["no eligibility criteria stated"])
    edu = profile.education[0] if profile.education else None
    reasons: list[str] = []
    unknown: list[str] = []
    ok = True

    if e.min_cgpa is not None:
        if edu is None or edu.cgpa is None:
            unknown.append(f"CGPA cutoff {e.min_cgpa} (your CGPA isn't in your profile)")
        elif edu.cgpa < e.min_cgpa:
            ok = False
            reasons.append(f"CGPA {edu.cgpa} is below the cutoff {e.min_cgpa}")
        else:
            reasons.append(f"CGPA {edu.cgpa} ≥ {e.min_cgpa}")

    if e.branches:
        if edu is None or not (edu.branch or edu.degree):
            unknown.append("branch requirement (your branch isn't in your profile)")
        else:
            mine = _branch_key(edu.branch or edu.degree or "")
            wanted = {_branch_key(b) for b in e.branches}
            if any(w in ("all", "any", "all branches") for w in wanted) or any(
                mine and (mine in w or w in mine) for w in wanted
            ):
                reasons.append(f"branch '{edu.branch or edu.degree}' is eligible")
            else:
                ok = False
                reasons.append(f"branch '{edu.branch or edu.degree}' isn't in {e.branches}")

    if e.batches:
        if edu is None or edu.graduation_year is None:
            unknown.append("batch requirement (your graduation year isn't in your profile)")
        elif edu.graduation_year not in e.batches:
            ok = False
            reasons.append(f"batch {edu.graduation_year} isn't in {e.batches}")
        else:
            reasons.append(f"batch {edu.graduation_year} is eligible")

    if not ok:
        return EligibilityResult(eligible=False, reasons=reasons, unknown=unknown)
    return EligibilityResult(eligible=None if unknown else True, reasons=reasons, unknown=unknown)
