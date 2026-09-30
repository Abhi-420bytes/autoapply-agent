"""ATS reviewer ↔ writer conversation, and LaTeX-safe JSON parsing of model replies."""
# ruff: noqa: F811  (the gen_env fixture is imported from test_generation)

from __future__ import annotations

import json
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.orm import Session

from app.generation.ats import ATSBreakdown
from app.generation.facts import Fact, FactPack
from app.generation.jd import JDAnalysis
from app.generation.review import review_draft, review_feedback, score_ceiling
from app.generation.runs import queue_generation, run_next
from app.knowledge.skills import SkillTagger
from app.llm.gateway import LLMGateway, extract_json
from app.models import Bullet, Resume
from app.schemas.settings import AppSettingsPatch
from app.services.settings_service import update_app_settings
from tests.fakes import FakeBackend
from tests.test_generation import ANALYSIS, gen_env, writer_json  # noqa: F401 (fixture)


def test_extract_json_repairs_single_backslash_latex() -> None:
    raw = r'{"regions": {"A": "\textbf{X} cut cost 20\% \& \resumeItem{y}\\\\ ok\nnext"}}'
    value = extract_json(raw, want_object=True)["regions"]["A"]
    assert value == "\\textbf{X} cut cost 20\\% \\& \\resumeItem{y}\\\\ ok\nnext"
    # correctly escaped JSON is untouched
    good = json.dumps({"a": "\\textbf{Z} \\% é\t."})
    assert extract_json(good, want_object=True) == {"a": "\\textbf{Z} \\% é\t."}


def _ats(**kw: Any) -> ATSBreakdown:
    base: dict[str, Any] = dict(
        score=60.0,
        keyword_score=50.0,
        semantic_score=40.0,
        format_score=100.0,
        matched_required=["Python"],
        missing_required=["FastAPI", "Kubernetes"],
        matched_preferred=[],
        missing_preferred=[],
        matched_keywords=[],
        missing_keywords=["microservices"],
    )
    return ATSBreakdown(**(base | kw))


def _pack() -> FactPack:
    return FactPack(
        facts=[
            Fact(id="b1", text="Built a REST API in FastAPI serving 2k requests", source="x"),
            Fact(id="b2", text="Split a monolith into microservices", source="x"),
        ],
        author="Abhi Ram",
        tagger=SkillTagger([]),
    )


ANALYSIS_MODEL = JDAnalysis(
    required_skills=["Python", "FastAPI", "Kubernetes"],
    keywords=["microservices"],
    responsibilities=["deploy containers on Kubernetes clusters", "write Python code"],
)


def test_deterministic_review_splits_supported_terms_from_gaps() -> None:
    review = review_draft(
        None,  # type: ignore[arg-type]  # use_llm=False never touches the gateway
        analysis=ANALYSIS_MODEL,
        ats=_ats(),
        pack=_pack(),
        resume_text="Python developer. Wrote Python code.",
        regions=["PROJECTS"],
        threshold=80,
        use_llm=False,
    )
    assert review.supported == {"FastAPI": ["b1"], "microservices": ["b2"]}
    assert review.gaps == ["Kubernetes"]
    assert review.weak_responsibilities == ["deploy containers on Kubernetes clusters"]
    # keywords: (2·1 + 2·1[FastAPI] + 1[microservices]) / (2·3 + 1) = 5/7
    assert review.ceiling == round(0.5 * 100 * 5 / 7 + 0.3 * 40 + 0.2 * 100, 1)
    fb = "\n".join(review_feedback(review.as_dict()))
    assert "FastAPI (b1)" in fb and "do NOT add these: Kubernetes" in fb


def test_ceiling_without_semantic() -> None:
    assert score_ceiling(_ats(semantic_score=None), ANALYSIS_MODEL, {}) < 100


def test_reviewer_suggestions_need_real_facts_and_no_gap_terms(
    gateway: LLMGateway,
    backend: FakeBackend,
    gen_env: dict[str, Any],
) -> None:
    backend.script(
        "ats",
        json.dumps(
            {
                "suggestions": [
                    {"region": "PROJECTS", "change": "Say FastAPI", "fact_ids": ["b1"]},
                    {"region": "PROJECTS", "change": "Invented", "fact_ids": ["b999"]},
                    {
                        "region": "SKILLS",
                        "change": "Add Kubernetes",
                        "fact_ids": ["b1"],
                        "terms": ["Kubernetes"],
                    },
                ],
                "summary": "FastAPI is the biggest lever",
            }
        ),
    )
    review = review_draft(
        gateway,
        analysis=ANALYSIS_MODEL,
        ats=_ats(),
        pack=_pack(),
        resume_text="Python",
        regions=["PROJECTS", "SKILLS"],
        threshold=80,
        writer_notes="I kept the ML project first",
    )
    assert [s["change"] for s in review.suggestions] == ["Say FastAPI"]
    assert review.summary == "FastAPI is the biggest lever"
    prompt = backend.calls[-1][1][-1]["content"]
    assert "WRITER'S NOTES ON THIS DRAFT: I kept the ML project first" in prompt


def test_pipeline_reviewer_and_writer_talk(
    gen_env: dict[str, Any],
    backend: FakeBackend,
    db: Session,
) -> None:
    update_app_settings(db, AppSettingsPatch(ats_threshold=100, max_quality_iterations=2))
    b1 = db.query(Bullet).order_by(Bullet.id).first()
    assert b1 is not None
    backend.script("jd", json.dumps(ANALYSIS))
    first = json.loads(writer_json())
    first["notes"] = "Led with the API project"
    second = json.loads(writer_json())
    second["notes"] = "Applied the REST API rewording"
    backend.script("writer", json.dumps(first), json.dumps(second))
    backend.script(
        "ats",
        json.dumps({"matches": []}),  # round 1: synonym check
        json.dumps(
            {
                "suggestions": [
                    {
                        "region": "EXPERIENCE",
                        "change": "Call it 'REST APIs' as the job does",
                        "fact_ids": [f"b{b1.id}"],
                        "terms": ["REST APIs"],
                    }
                ],
                "summary": "Use the job's wording for the API work",
            }
        ),
        json.dumps({"matches": []}),  # round 2: synonym check (last round: no review)
    )
    queue_generation(db, gen_env["job"])
    run = run_next(gen_env["deps"], InMemorySaver())
    assert run is not None and run.state.value == "done", run.error if run else None

    models = [c[0].model for c in backend.calls]
    ats_prompts = [c[1][-1]["content"] for c in backend.calls if c[0].model == "ats"]
    assert models.count("ats") == 3  # synonyms, review, synonyms
    assert "WRITER'S NOTES ON THIS DRAFT: Led with the API project" in ats_prompts[1]
    second_writer = [c[1][-1]["content"] for c in backend.calls if c[0].model == "writer"][1]
    assert "ATS reviewer [EXPERIENCE]: Call it 'REST APIs' as the job does" in second_writer
    assert "ATS reviewer: Use the job's wording for the API work" in second_writer
    assert "Kubernetes" in second_writer.split("do NOT add these:", 1)[1]

    rep = db.query(Resume).filter_by(job_id=gen_env["job"].id).one().score_report
    assert rep["review"]["summary"] == "Use the job's wording for the API work"
    assert rep["ceiling"] is not None
    db.expire_all()
    assert "reachable with your current facts" in (gen_env["job"].status_reason or "") or (
        rep["ceiling"] >= 100
    )


def test_extract_json_accepts_raw_newlines_in_strings() -> None:
    raw = '{"regions": {"A": "line one\nline two\twith tab"}, "used_facts": []}'
    assert extract_json(raw, want_object=True)["regions"]["A"] == "line one\nline two\twith tab"
