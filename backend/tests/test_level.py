"""Job level (fresher / experienced) and the target-level filter before resume generation."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.generation.level import classify_level, gate_and_queue
from app.models import Job
from app.models.enums import JobSource, JobStatus
from app.schemas.settings import AppSettingsPatch
from app.services.settings_service import update_app_settings


@pytest.mark.parametrize(
    ("title", "jd", "level"),
    [
        ("Software Development Engineer I", "", "fresher"),
        ("Graduate Engineer Trainee", "", "fresher"),
        ("Python Developer", "Freshers welcome. 2026 batch.", "fresher"),
        ("Backend Engineer", "Experience: 0-1 years", "fresher"),
        ("Senior Backend Engineer", "", "experienced"),
        ("Software Engineer II", "", "experienced"),
        ("Python Developer", "We need 3+ years of experience in Python.", "experienced"),
        ("GenAI Engineer", "Minimum 2 years of hands-on experience with LLMs", "experienced"),
        ("Software Engineer", "Build APIs in FastAPI.", None),
    ],
)
def test_levels(title: str, jd: str, level: str | None) -> None:
    got, reason = classify_level(title, jd)
    assert got == level and reason


def test_experienced_jobs_are_set_aside_before_any_resume(db: Session) -> None:
    queued: list[int] = []
    senior = Job(
        source=JobSource.EMAIL,
        status=JobStatus.DETECTED,
        role="Backend Engineer",
        jd_text="5+ years of experience with Go",
    )
    fresher = Job(
        source=JobSource.EMAIL,
        status=JobStatus.DETECTED,
        role="Python Developer",
        jd_text="0-1 years experience",
    )
    db.add_all([senior, fresher])
    db.commit()
    assert not gate_and_queue(db, senior, lambda _db, j, **kw: queued.append(j.id))
    assert gate_and_queue(db, fresher, lambda _db, j, **kw: queued.append(j.id))
    assert queued == [fresher.id]
    assert senior.status is JobStatus.INELIGIBLE and "5+ years" in (senior.status_reason or "")
    assert senior.level == "experienced" and fresher.level == "fresher"

    update_app_settings(db, AppSettingsPatch(target_levels=["fresher", "experienced", "unknown"]))
    assert gate_and_queue(db, senior, lambda _db, j, **kw: queued.append(j.id))


def test_jobs_can_be_filtered_by_level(client: Any, db: Session) -> None:
    db.add_all(
        [
            Job(source=JobSource.MANUAL, status=JobStatus.DETECTED, role="A", level="fresher"),
            Job(source=JobSource.MANUAL, status=JobStatus.DETECTED, role="B", level="experienced"),
            Job(source=JobSource.MANUAL, status=JobStatus.DETECTED, role="C"),
        ]
    )
    db.commit()
    roles = lambda q: [j["role"] for j in client.get(f"/api/jobs?level={q}").json()]  # noqa: E731
    assert roles("fresher") == ["A"] and roles("experienced") == ["B"] and roles("unknown") == ["C"]


@pytest.fixture
def ai(db: Session, gateway: Any) -> Any:
    from app.models import LLMProvider, LLMTaskConfig
    from app.models.enums import LLMProviderKind, LLMTask

    p = LLMProvider(label="G", kind=LLMProviderKind.GEMINI, api_key="AIza-test-key-0000")
    db.add(p)
    db.flush()
    db.add(LLMTaskConfig(task=LLMTask.JD_ANALYZER, provider_id=p.id, model="lvl", params={}))
    db.commit()
    return gateway


def _answer(level: str, evidence: str, years: int | None = None) -> str:
    import json

    return json.dumps(
        {"level": level, "min_years": years, "evidence": evidence, "reason": "stated"}
    )


def test_ai_decides_with_a_verified_quote(ai: Any, backend: Any) -> None:
    from app.generation.level import decide_level

    jd = "We welcome final-year students for this role. You'll build FastAPI services."
    backend.script("lvl", _answer("fresher", "welcome final-year students"))
    level, reason = decide_level("Software Engineer", jd, ai)  # rules alone: not stated
    assert level == "fresher" and reason.startswith("AI:") and "final-year" in reason


def test_ai_answer_without_real_evidence_falls_back_to_rules(ai: Any, backend: Any) -> None:
    from app.generation.level import decide_level

    backend.script("lvl", _answer("fresher", "freshers welcome"))  # not in the posting
    assert decide_level("Backend Engineer", "Needs 4+ years of experience in Go", ai) == (
        "experienced",
        "asks for 4+ years of experience",
    )


def test_ai_failure_or_unsure_uses_rules(ai: Any, backend: Any) -> None:
    from app.generation.level import decide_level
    from app.llm.errors import ErrorKind, LLMProviderError

    backend.script("lvl", *[LLMProviderError(ErrorKind.QUOTA, "quota")] * 2)
    assert decide_level("Senior Engineer", "", ai)[0] == "experienced"
    backend.script("lvl", _answer("unknown", ""))
    assert decide_level("Graduate Trainee", "", ai)[0] == "fresher"  # clear keyword wins
    backend.script("lvl", _answer("unknown", ""))
    level, reason = decide_level("Software Engineer", "Build APIs.", ai)
    assert level is None and reason.startswith("AI:")
