"""Job level: fresher (entry level) or experienced, from the title and the JD text.

The configured AI decides (one short call through the gateway, with an exact quote from
the posting as evidence); deterministic keyword rules are the fallback when the AI is
unavailable or its evidence can't be found in the posting. Jobs outside your target levels
are set aside before any resume is generated.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import BaseModel

from app.llm.errors import LLMError
from app.llm.gateway import LLMGateway
from app.models.enums import LLMTask

log = logging.getLogger(__name__)

Level = Literal["fresher", "experienced"]

SENIOR_TITLE = re.compile(
    r"\b(senior|sr|lead|principal|staff|manager|architect|head|director|vp|sse|tl|"
    r"experienced|expert|specialist ii+|engineer iii|engineer iv|sde ?(ii|iii|2|3)|"
    r"l[3-9]|ii|iii|iv)\b",
    re.I,
)
FRESHER_TITLE = re.compile(
    r"\b(fresher|freshers|intern|internship|trainee|graduate|grad|entry[ -]level|junior|jr|"
    r"associate|apprentice|campus|sde ?(i|1)|engineer i|developer i|l1|l2)\b",
    re.I,
)
FRESHER_TEXT = re.compile(
    r"\b(fresher|freshers|entry[ -]level|new grad|recent graduates?|graduate engineer trainee|"
    r"campus (hire|hiring|drive)|0\s*(-|–|to)\s*[12]\s*(\+\s*)?(years?|yrs?)|"
    r"(no|zero) (prior )?experience (is )?(required|needed)|"
    r"20(2[4-9]) (batch|pass ?outs?|graduates?))\b",
    re.I,
)
# "3+ years", "minimum 2 years", "2-4 years of experience", "at least 5 yrs"
YEARS = re.compile(
    r"(?:minimum|min\.?|at least|atleast)?\s*(\d{1,2})\s*(?:\+|plus)?\s*"
    r"(?:(?:-|–|to)\s*(\d{1,2})\s*)?(?:years?|yrs?)\b(?:\s+of)?(?:\s+[\w-]+){0,3}\s+experience",
    re.I,
)


def min_years(text: str) -> int | None:
    """The smallest experience requirement stated (e.g. "2-4 years of experience" → 2)."""
    found = [int(m.group(1)) for m in YEARS.finditer(text) if int(m.group(1)) <= 20]
    return min(found) if found else None


def classify_level(title: str | None, jd_text: str | None) -> tuple[Level | None, str]:
    title = title or ""
    text = jd_text or ""
    if FRESHER_TITLE.search(title) and not re.search(r"\b(senior|sr|lead)\b", title, re.I):
        return "fresher", f"title says '{FRESHER_TITLE.search(title).group(0)}'"  # type: ignore[union-attr]
    if m := re.search(
        r"\b(senior|sr|lead|principal|staff|manager|architect|head|director)\b", title, re.I
    ):
        return "experienced", f"title says '{m.group(0)}'"
    years = min_years(text)
    if years is not None and years >= 2:
        return "experienced", f"asks for {years}+ years of experience"
    if FRESHER_TEXT.search(text):
        return "fresher", f"JD says '{FRESHER_TEXT.search(text).group(0)}'"  # type: ignore[union-attr]
    if years is not None:  # 0 or 1 year
        return "fresher", f"asks for {years} year(s) of experience"
    if SENIOR_TITLE.search(title):
        return "experienced", f"title says '{SENIOR_TITLE.search(title).group(0)}'"  # type: ignore[union-attr]
    return None, "no experience requirement stated"


class LevelOut(BaseModel):
    level: Literal["fresher", "experienced", "unknown"]
    min_years: int | None = None
    evidence: str = ""
    reason: str = ""


def classify_with_ai(
    gateway: LLMGateway, title: str | None, jd_text: str | None, job_id: int | None = None
) -> tuple[Level | None, str] | None:
    """The configured AI (via the gateway) decides; None if it failed or its evidence
    quote isn't really in the posting."""
    try:
        out = gateway.structured(
            LLMTask.JD_ANALYZER,
            "job_level",
            {"title": title or "", "description": (jd_text or "")[:5000]},
            LevelOut,
            job_id=job_id,
        ).value
    except LLMError as exc:
        log.info("AI level check unavailable: %s", exc)
        return None
    reason = " ".join(out.reason.split())[:120]
    if out.level == "unknown":
        return None, f"AI: {reason or 'no experience level stated'}"
    evidence = " ".join(out.evidence.split())
    posting = " ".join(f"{title or ''} {jd_text or ''}".split()).lower()
    if not evidence or evidence.lower() not in posting:
        log.info("AI level answer discarded: evidence %r not in the posting", evidence)
        return None
    return out.level, f'AI: {reason or out.level} ("{evidence[:80]}")'


def decide_level(
    title: str | None,
    jd_text: str | None,
    gateway: LLMGateway | None = None,
    job_id: int | None = None,
) -> tuple[Level | None, str]:
    """The AI decides when available; the keyword rules are the fallback (and win when
    the AI says 'not stated' but the posting has a clear keyword signal)."""
    rules = classify_level(title, jd_text)
    if gateway is None:
        return rules
    ai = classify_with_ai(gateway, title, jd_text, job_id)
    if ai is None or (ai[0] is None and rules[0] is not None):
        return rules
    return ai


def label_job(job: Any, gateway: LLMGateway | None = None, db: Any = None) -> Level | None:
    """Set job.level / job.level_reason; returns the level. With a gateway, the DB
    transaction is ended before the AI call."""
    title, jd, job_id = job.role, job.jd_text, job.id
    if db is not None and gateway is not None:
        db.commit()
    level, reason = decide_level(title, jd, gateway, job_id)
    job.level, job.level_reason = level, reason[:200]
    return level


def outside_targets(
    job: Any, targets: list[str], gateway: LLMGateway | None = None, db: Any = None
) -> bool:
    """Label the job; True if its level isn't one you target (it's then set aside)."""
    level = label_job(job, gateway, db)
    return (level or "unknown") not in targets


def set_aside_reason(job: Any) -> str:
    kind = "Experienced" if job.level == "experienced" else "Fresher" if job.level else "Unlabelled"
    return (
        f"{kind} role ({job.level_reason}), so it was set aside and no resume was made. "
        "To include these, change 'Job levels' in Settings, or click Regenerate on this job."
    )


def gate_and_queue(
    db: Any, job: Any, queue_generation: Any, gateway: LLMGateway | None = None
) -> bool:
    """Automatic pipeline: queue the resume only for your target levels; otherwise set the
    job aside (no resume is generated). Jobs you start yourself are never gated."""
    from app.models.enums import JobStatus
    from app.services.settings_service import get_app_settings

    targets: list[str] = list(get_app_settings(db).target_levels)
    if outside_targets(job, targets, gateway, db):
        job.status, job.status_reason = JobStatus.INELIGIBLE, set_aside_reason(job)
        db.commit()
        return False
    db.commit()
    queue_generation(db, job, force=False)
    return True
