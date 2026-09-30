"""ATS reviewer ↔ resume writer conversation.

After each scored draft, the ATS side explains *where the points were lost* and *how to win
them back with the user's own facts*, and the writer answers with its next draft plus notes
(which the reviewer reads on the following round):

    writer ──draft + notes──▶ ATS score ──breakdown──▶ reviewer ──fact-backed fixes──▶ writer

Two layers:
- deterministic: which missing JD terms the facts support (with fact ids) and which they
  don't (true gaps, never to be added), the weakest-covered responsibilities, and the score
  ceiling reachable with the available facts;
- the ats_scorer model: concrete edits, each citing fact ids. Suggestions citing unknown
  facts are dropped, and the writer's truthfulness guard still applies to whatever it writes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.generation.ats import W_FORMAT, W_KEYWORDS, W_SEMANTIC, ATSBreakdown, _found
from app.generation.facts import FactPack
from app.generation.jd import JDAnalysis
from app.knowledge.skills import normalize
from app.llm.errors import LLMCallFailedError, LLMError
from app.llm.gateway import LLMGateway
from app.models.enums import LLMTask

log = logging.getLogger(__name__)

MAX_SUGGESTIONS = 6
_STOP = set(
    "a an and are as at be by for from in into is of on or our the to with you your will "
    "we this that using use work working ability strong experience".split()
)


class Suggestion(BaseModel):
    region: str = Field(description="editable region to change, e.g. PROJECTS")
    change: str = Field(description="the concrete edit, one sentence")
    fact_ids: list[str] = Field(default_factory=list, description="facts that support it")
    terms: list[str] = Field(default_factory=list, description="JD terms this edit covers")


class ReviewOutput(BaseModel):
    suggestions: list[Suggestion] = Field(default_factory=list)
    summary: str = ""


@dataclass
class Review:
    supported: dict[str, list[str]] = field(default_factory=dict)  # missing term → fact ids
    gaps: list[str] = field(default_factory=list)  # missing terms no fact supports
    weak_responsibilities: list[str] = field(default_factory=list)
    ceiling: float | None = None  # best score reachable by adding every supported term
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _words(text: str) -> set[str]:
    return {w for w in normalize(text).split() if len(w) > 2 and w not in _STOP}


def _singular(text: str) -> str:
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in text.split())


def _supports(term: str, text: str) -> bool:
    """Exact term match, or the same words ignoring plurals ("REST APIs" ~ "a REST API")."""
    if _found(term, text):
        return True
    t = _singular(normalize(term))
    return bool(t) and f" {t} " in f" {_singular(normalize(text))} "


def term_support(terms: list[str], pack: FactPack) -> dict[str, list[str]]:
    """Missing term → ids of facts that mention it (empty list = a real gap)."""
    return {t: [f.id for f in pack.facts if _supports(t, f.text)] for t in terms}


def weak_responsibilities(responsibilities: list[str], resume_text: str, n: int = 3) -> list[str]:
    """The JD responsibilities whose words the resume covers least (these drag semantic)."""
    have = _words(resume_text)
    scored = []
    for r in responsibilities:
        words = _words(r)
        if words:
            scored.append((len(words & have) / len(words), r))
    scored.sort(key=lambda x: x[0])
    return [r for cover, r in scored[:n] if cover < 0.6]


def score_ceiling(
    ats: ATSBreakdown, analysis: JDAnalysis, supported: dict[str, list[str]]
) -> float:
    """Total score if every fact-supported missing term were added (semantic unchanged)."""
    can = {t for t, ids in supported.items() if ids}
    req, pref = analysis.required_skills, analysis.preferred_skills
    kws = [*ats.matched_keywords, *ats.missing_keywords]
    weights = 2 * len(req) + len(pref) + len(kws)
    got = (
        2 * len(ats.matched_required)
        + len(ats.matched_preferred)
        + len(ats.matched_keywords)
        + 2 * len([t for t in ats.missing_required if t in can])
        + len([t for t in ats.missing_preferred if t in can])
        + len([t for t in ats.missing_keywords if t in can])
    )
    kw = 100 * got / weights if weights else 100.0
    if ats.semantic_score is None:
        return round((W_KEYWORDS * kw + W_FORMAT * ats.format_score) / (W_KEYWORDS + W_FORMAT), 1)
    return round(W_KEYWORDS * kw + W_SEMANTIC * ats.semantic_score + W_FORMAT * ats.format_score, 1)


def review_draft(
    gateway: LLMGateway,
    *,
    analysis: JDAnalysis,
    ats: ATSBreakdown,
    pack: FactPack,
    resume_text: str,
    regions: list[str],
    threshold: int,
    writer_notes: str = "",
    job_id: int | None = None,
    urgent: bool = False,
    use_llm: bool = True,
) -> Review:
    missing = [*ats.missing_required, *ats.missing_preferred, *ats.missing_keywords]
    supported = term_support(missing, pack)
    review = Review(
        supported={t: ids[:4] for t, ids in supported.items() if ids},
        gaps=[t for t, ids in supported.items() if not ids],
        weak_responsibilities=weak_responsibilities(analysis.responsibilities, resume_text),
        ceiling=score_ceiling(ats, analysis, supported),
    )
    if not use_llm:
        return review
    known = {f.id for f in pack.facts}
    try:
        out = gateway.structured(
            LLMTask.ATS_SCORER,
            "ats_review",
            {
                "role": analysis.role,
                "score": ats.score,
                "threshold": threshold,
                "keyword_score": ats.keyword_score,
                "semantic_score": ats.semantic_score,
                "format_score": ats.format_score,
                "supported": review.supported,
                "gaps": review.gaps,
                "weak": review.weak_responsibilities,
                "responsibilities": analysis.responsibilities,
                "resume_text": resume_text[:8000],
                "facts": pack.as_prompt_lines(),
                "regions": regions,
                "writer_notes": writer_notes,
                "max_suggestions": MAX_SUGGESTIONS,
            },
            ReviewOutput,
            job_id=job_id,
            urgent=urgent,
        ).value
    except LLMCallFailedError as exc:
        if exc.transient:
            raise  # overloaded: the runner retries this step later from the checkpoint
        log.warning("ATS review skipped: %s", exc)
        return review
    except LLMError as exc:
        log.warning("ATS review skipped: %s", exc)
        return review
    gap_norm = {normalize(g) for g in review.gaps}
    for s in out.suggestions[:MAX_SUGGESTIONS]:
        ids = [i for i in s.fact_ids if i in known]
        if not ids:  # every change must rest on the user's own facts
            continue
        if any(normalize(t) in gap_norm for t in s.terms):  # would add an unsupported skill
            continue
        review.suggestions.append(
            {"region": s.region, "change": s.change.strip(), "fact_ids": ids, "terms": s.terms}
        )
    review.summary = out.summary.strip()[:400]
    return review


def review_feedback(review: dict[str, Any]) -> list[str]:
    """Turn a stored review into lines for the writer's next prompt."""
    fb: list[str] = []
    if review.get("summary"):
        fb.append(f"ATS reviewer: {review['summary']}")
    for s in review.get("suggestions", []):
        fb.append(
            f"ATS reviewer [{s['region']}]: {s['change']} (facts: {', '.join(s['fact_ids'])})"
        )
    supported = review.get("supported") or {}
    if supported:
        fb.append(
            "Missing JD terms your facts DO support — work them in, in the JD's exact wording: "
            + "; ".join(f"{t} ({', '.join(ids)})" for t, ids in supported.items())
        )
    if review.get("gaps"):
        fb.append(
            "Missing JD terms with NO supporting fact — do NOT add these: "
            + ", ".join(review["gaps"])
        )
    if review.get("weak_responsibilities"):
        fb.append(
            "Least-covered job responsibilities (hurts the semantic match) — where facts allow, "
            "describe your work in these terms: "
            + " | ".join(re.sub(r"\s+", " ", r)[:160] for r in review["weak_responsibilities"])
        )
    return fb
