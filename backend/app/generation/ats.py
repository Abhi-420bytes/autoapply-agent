"""ATS score: a weighted mix of keyword coverage, semantic similarity and format checks,
computed on the text an ATS actually extracts from the PDF (pdftotext).

    score = 0.5 × keyword coverage + 0.3 × semantic similarity + 0.2 × format checks

The ats_scorer model may credit synonyms for missing keywords, but only with an exact
quote from the resume as evidence (verified here), so it can't inflate the score.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import BaseModel, Field

from app.generation.jd import JDAnalysis
from app.knowledge.bank import cosine
from app.knowledge.latex_text import to_text
from app.knowledge.skills import _pattern, normalize
from app.llm.errors import LLMError
from app.llm.gateway import LLMGateway
from app.models.enums import LLMTask

log = logging.getLogger(__name__)

W_KEYWORDS, W_SEMANTIC, W_FORMAT = 0.5, 0.3, 0.2
SIM_FLOOR, SIM_CEIL = 0.5, 0.9  # cosine similarity mapped linearly onto 0..100
STANDARD_HEADINGS = ("education", "experience", "projects", "skills")
_SECTION_RE = re.compile(r"\\section\*?\s*\{([^}]*)\}")


class SynonymMatch(BaseModel):
    term: str
    evidence: str = Field(description="exact quote from the resume text that covers the term")


class SynonymOutput(BaseModel):
    matches: list[SynonymMatch] = Field(default_factory=list)


class ATSBreakdown(BaseModel):
    score: float
    keyword_score: float
    semantic_score: float | None
    format_score: float
    matched_required: list[str]
    missing_required: list[str]
    matched_preferred: list[str]
    missing_preferred: list[str]
    matched_keywords: list[str]
    missing_keywords: list[str] = Field(default_factory=list)
    synonym_matches: dict[str, str] = Field(default_factory=dict)
    similarity: float | None = None
    format_checks: dict[str, Any] = Field(default_factory=dict)


def _found(term: str, text: str) -> bool:
    return bool(_pattern(term).search(text)) or f" {normalize(term)} " in f" {normalize(text)} "


def format_checks(tex: str, pdf_text: str, region_contents: dict[str, str]) -> dict[str, Any]:
    """Does the extracted text contain all content, standard headings, in order?"""
    body = tex.split("\\begin{document}", 1)[-1]
    titles = [to_text(t) for t in _SECTION_RE.findall(body)]
    text_lower = pdf_text.lower()

    region_words = set(normalize(" ".join(to_text(c) for c in region_contents.values())).split())
    text_words = set(normalize(pdf_text).split())
    intact = len(region_words & text_words) / len(region_words) if region_words else 1.0

    present = [t for t in titles if t.lower() in text_lower]
    positions = [text_lower.find(t.lower()) for t in present]
    in_order = positions == sorted(positions)
    standard = [h for h in STANDARD_HEADINGS if any(h in t.lower() for t in titles)]
    return {
        "content_intact": round(intact, 3),
        "headings_found": f"{len(present)}/{len(titles)}",
        "headings_ratio": len(present) / len(titles) if titles else 1.0,
        "reading_order_ok": in_order,
        "standard_headings": standard,
        "standard_ratio": min(1.0, len(standard) / 3),
    }


def _format_score(checks: dict[str, Any]) -> float:
    parts = [
        checks["content_intact"],
        checks["headings_ratio"],
        1.0 if checks["reading_order_ok"] else 0.0,
        checks["standard_ratio"],
    ]
    return float(100 * sum(parts) / len(parts))


def score_resume(
    gateway: LLMGateway,
    *,
    analysis: JDAnalysis,
    jd_text: str,
    pdf_text: str,
    tex: str,
    region_contents: dict[str, str],
    job_id: int | None = None,
    use_llm: bool = True,
) -> ATSBreakdown:
    req = analysis.required_skills
    pref = analysis.preferred_skills
    kws = [
        k for k in analysis.keywords if normalize(k) not in {normalize(x) for x in (*req, *pref)}
    ]

    matched_req = [t for t in req if _found(t, pdf_text)]
    matched_pref = [t for t in pref if _found(t, pdf_text)]
    matched_kw = [t for t in kws if _found(t, pdf_text)]

    synonyms: dict[str, str] = {}
    missing = [t for t in (*req, *pref) if t not in matched_req and t not in matched_pref]
    if use_llm and missing:
        synonyms = _synonym_matches(gateway, missing, pdf_text, job_id)
        matched_req += [t for t in req if t in synonyms and t not in matched_req]
        matched_pref += [t for t in pref if t in synonyms and t not in matched_pref]

    weights = 2 * len(req) + len(pref) + len(kws)
    got = 2 * len(matched_req) + len(matched_pref) + len(matched_kw)
    keyword_score = 100 * got / weights if weights else 100.0

    semantic: float | None = None
    similarity: float | None = None
    try:
        vecs = gateway.embed([jd_text[:8000], pdf_text[:8000]], job_id=job_id).vectors
        similarity = cosine(vecs[0], vecs[1])
        semantic = 100 * min(1.0, max(0.0, (similarity - SIM_FLOOR) / (SIM_CEIL - SIM_FLOOR)))
    except LLMError as exc:
        log.warning("semantic similarity skipped: %s", exc)

    checks = format_checks(tex, pdf_text, region_contents)
    fmt = _format_score(checks)
    if semantic is None:  # renormalize without the semantic part
        total = (W_KEYWORDS * keyword_score + W_FORMAT * fmt) / (W_KEYWORDS + W_FORMAT)
    else:
        total = W_KEYWORDS * keyword_score + W_SEMANTIC * semantic + W_FORMAT * fmt

    return ATSBreakdown(
        score=round(total, 1),
        keyword_score=round(keyword_score, 1),
        semantic_score=None if semantic is None else round(semantic, 1),
        format_score=round(fmt, 1),
        matched_required=matched_req,
        missing_required=[t for t in req if t not in matched_req],
        matched_preferred=matched_pref,
        missing_preferred=[t for t in pref if t not in matched_pref],
        matched_keywords=matched_kw,
        missing_keywords=[t for t in kws if t not in matched_kw],
        synonym_matches=synonyms,
        similarity=None if similarity is None else round(similarity, 4),
        format_checks=checks,
    )


def _synonym_matches(
    gateway: LLMGateway, missing: list[str], pdf_text: str, job_id: int | None
) -> dict[str, str]:
    try:
        result = gateway.structured(
            LLMTask.ATS_SCORER,
            "ats_synonyms",
            {"missing": missing, "resume_text": pdf_text[:12000]},
            SynonymOutput,
            job_id=job_id,
        )
    except LLMError as exc:
        log.warning("synonym check skipped: %s", exc)
        return {}
    text_norm = f" {normalize(pdf_text)} "
    wanted = {normalize(t): t for t in missing}
    out: dict[str, str] = {}
    for m in result.value.matches:
        term = wanted.get(normalize(m.term))
        evidence = normalize(m.evidence)
        # evidence must be a real quote (3+ words) that actually appears in the resume
        if term and len(evidence.split()) >= 2 and f" {evidence} " in text_norm:
            out[term] = m.evidence.strip()
    return out
