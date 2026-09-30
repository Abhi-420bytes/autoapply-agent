from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.generation.ats import score_resume
from app.generation.facts import Fact, FactPack, guard_regions
from app.generation.jd import Eligibility, JDAnalysis, check_eligibility, guard_analysis
from app.generation.pipeline import Deps, build_graph, initial_state
from app.generation.runs import queue_generation, recover_interrupted, run_next
from app.knowledge.bank import content_hash, embed_missing
from app.knowledge.skills import SkillTagger
from app.llm.gateway import LLMGateway
from app.models import Bullet, Job, LLMProvider, LLMTaskConfig, PipelineRun, Resume
from app.models.enums import BulletSource, JobSource, JobStatus, LLMProviderKind, LLMTask, RunState
from app.schemas.settings import Education, Profile
from app.services.settings_service import set_profile
from app.services.templates import create_template
from tests.fakes import FakeBackend, FakeCompiler

SAMPLE = (Path(__file__).resolve().parents[1] / "templates" / "sample" / "resume.tex").read_bytes()
JD = """Backend Engineering Intern at Acme. Required: Python, FastAPI, PostgreSQL, Kubernetes.
Nice to have: Docker, REST APIs. You will build microservices and CI pipelines.
Eligibility: B.Tech CSE, 2026 batch, CGPA 7.0 and above."""

ANALYSIS = {
    "company": "Acme",
    "role": "Backend Engineering Intern",
    "role_category": "backend",
    "seniority": "intern",
    "required_skills": ["Python", "FastAPI", "PostgreSQL", "Kubernetes", "Rust"],
    "preferred_skills": ["Docker", "REST APIs"],
    "keywords": ["microservices", "CI"],
    "responsibilities": ["build microservices"],
    "eligibility": {"min_cgpa": 7.0, "branches": ["CSE"], "batches": [2026], "other": []},
}

EXPERIENCE = "\n".join(
    [
        r"\entry{Backend Intern}{May 2025 -- Jul 2025}{Example Corp}{Remote}",
        r"\begin{bullets}",
        r"  \resumeItem{Built a REST API in FastAPI and PostgreSQL serving 2k daily requests} %%prio:0.9",
        r"  \resumeItem{Cut CI time by 40\% by caching Docker layers} %%prio:0.7",
        r"  \resumeItem{Boosted revenue by 99\% with Kubernetes autoscaling} %%prio:0.8",  # invented
        r"\end{bullets}",
    ]
)
PROJECTS = "\n".join(
    [
        r"\project{AutoApply Agent}{Python, FastAPI, LangGraph}",
        r"\begin{bullets}",
        r"  \resumeItem{Built by Abhi Ram: LLM agent that tailored and generated this resume} %%pin",
        r"\end{bullets}",
    ]
)
SKILLS = (
    r"\textbf{Languages:} Python, SQL \\" + "\n" + r"\textbf{Tools:} FastAPI, PostgreSQL, Docker"
)


def writer_json(**regions: str) -> str:
    base = {"EXPERIENCE": EXPERIENCE, "PROJECTS": PROJECTS, "SKILLS": SKILLS}
    return json.dumps({"regions": base | regions, "used_facts": ["b1"], "notes": "ok"})


@pytest.fixture
def gen_env(
    db: Session, engine: Engine, gateway: LLMGateway, backend: FakeBackend
) -> dict[str, Any]:
    p = LLMProvider(label="T", kind=LLMProviderKind.OPENAI, api_key="sk-test-key-000000")
    db.add(p)
    db.flush()
    for task, model in [
        (LLMTask.JD_ANALYZER, "jd"),
        (LLMTask.RESUME_WRITER, "writer"),
        (LLMTask.ATS_SCORER, "ats"),
        (LLMTask.FINAL_POLISH, "polish"),
        (LLMTask.EMBEDDING, "emb"),
    ]:
        db.add(LLMTaskConfig(task=task, provider_id=p.id, model=model, params={}))
    for text in [
        "Built a REST API in FastAPI and PostgreSQL serving 2k daily requests",
        "Cut CI time by 40% by caching Docker layers",
    ]:
        db.add(
            Bullet(
                text=text,
                section="Experience",
                heading="Example Corp",
                skills=[],
                source_type=BulletSource.PAST_RESUME,
                content_hash=content_hash(text),
            )
        )
    db.commit()
    set_profile(
        db,
        Profile(
            full_name="Abhi Ram",
            skills=["Python", "SQL"],
            education=[
                Education(
                    institution="Example Institute",
                    branch="Computer Science",
                    cgpa=8.5,
                    graduation_year=2026,
                )
            ],
        ),
    )
    # these tests exercise the creator's setup (the AutoApply project on the resume)
    from app.schemas.settings import AppSettingsPatch
    from app.services.settings_service import update_app_settings

    update_app_settings(db, AppSettingsPatch(include_signature_project=True))
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    embed_missing(gateway, maker)
    compiler = FakeCompiler(per_page=10)
    template = create_template(db, compiler, filename="resume.tex", data=SAMPLE)
    job = Job(source=JobSource.MANUAL, status=JobStatus.DETECTED, jd_text=JD)
    db.add(job)
    db.commit()
    deps = Deps(gateway=gateway, sessions=maker, compiler=compiler)
    return {"job": job, "template": template, "deps": deps, "maker": maker}


def script_happy(backend: FakeBackend) -> None:
    backend.script("jd", json.dumps(ANALYSIS))
    backend.script("writer", writer_json())
    backend.script(
        "ats",
        json.dumps(
            {
                "matches": [
                    {"term": "REST APIs", "evidence": "Built a REST API in FastAPI"},
                    {
                        "term": "Kubernetes",
                        "evidence": "deployed on Kubernetes clusters",
                    },  # not in resume
                ]
            }
        ),
    )
    backend.script("polish", writer_json())


# -- unit: JD guard, eligibility, generated-content guard ------------------------------------


def test_jd_guard_keeps_only_terms_in_the_jd() -> None:
    clean, dropped = guard_analysis(JDAnalysis.model_validate(ANALYSIS), JD)
    assert "Rust" not in clean.required_skills and "required: Rust" in dropped
    assert clean.required_skills == ["Python", "FastAPI", "PostgreSQL", "Kubernetes"]


@pytest.mark.parametrize(
    ("edu", "expected"),
    [
        (
            Education(institution="X", branch="Computer Science", cgpa=8.5, graduation_year=2026),
            True,
        ),
        (
            Education(institution="X", branch="Computer Science", cgpa=6.5, graduation_year=2026),
            False,
        ),
        (Education(institution="X", branch="Mechanical", cgpa=9.0, graduation_year=2026), False),
        (Education(institution="X", branch="CSE", cgpa=9.0, graduation_year=2025), False),
        (Education(institution="X", branch="CSE"), None),  # CGPA/batch unknown
    ],
)
def test_eligibility(edu: Education, expected: bool | None) -> None:
    e = Eligibility(min_cgpa=7.0, branches=["Computer Science and Engineering"], batches=[2026])
    assert check_eligibility(e, Profile(education=[edu])).eligible is expected


def test_guard_regions_removes_invented_claims() -> None:
    pack = FactPack(
        facts=[
            Fact("b1", "Built a REST API in FastAPI and PostgreSQL serving 2k daily requests", "x"),
            Fact("b2", "Cut CI time by 40% by caching Docker layers", "x"),
            Fact("base:SKILLS", "Languages: Python, SQL", "x"),
            Fact(
                "signature",
                "AutoApply Agent built by Abhi Ram with Python, FastAPI, LangGraph",
                "x",
            ),
            Fact("base:EXPERIENCE", "Backend Intern May 2025 Jul 2025 Example Corp Remote", "x"),
        ],
        author="Abhi Ram",
        tagger=SkillTagger(),
    )
    base = {"EXPERIENCE": "old", "PROJECTS": "old", "SKILLS": "old skills"}
    out = guard_regions(
        {"EXPERIENCE": EXPERIENCE, "PROJECTS": PROJECTS, "SKILLS": SKILLS + r", Kubernetes"},
        base,
        pack,
        "resumeItem",
    )
    assert "99" not in out.regions["EXPERIENCE"] and "40\\%" in out.regions["EXPERIENCE"]
    assert out.regions["SKILLS"] == "old skills"  # unsupported skill in a non-bullet line
    assert out.signature_present
    assert any("number 99" in r for r in out.removed)


def test_ats_scoring_and_verified_synonyms(
    gateway: LLMGateway, backend: FakeBackend, db: Session
) -> None:
    p = LLMProvider(label="T", kind=LLMProviderKind.OPENAI, api_key="sk-test-key-000000")
    db.add(p)
    db.flush()
    db.add(LLMTaskConfig(task=LLMTask.ATS_SCORER, provider_id=p.id, model="ats", params={}))
    db.add(LLMTaskConfig(task=LLMTask.EMBEDDING, provider_id=p.id, model="emb", params={}))
    db.commit()
    backend.script(
        "ats",
        json.dumps(
            {
                "matches": [
                    {"term": "REST APIs", "evidence": "Built a REST API"},
                    {"term": "Kubernetes", "evidence": "ran Kubernetes"},
                ]
            }
        ),
    )
    analysis = JDAnalysis(required_skills=["Python", "Kubernetes"], preferred_skills=["REST APIs"])
    text = "Education\nExperience\nBuilt a REST API in Python\nProjects\nSkills\nPython"
    tex = "\\begin{document}\\section{Education}\\section{Experience}\\section{Projects}\\section{Skills}"
    ats = score_resume(
        gateway,
        analysis=analysis,
        jd_text="Python Kubernetes REST",
        pdf_text=text,
        tex=tex,
        region_contents={"X": "Built a REST API in Python"},
    )
    assert ats.matched_required == ["Python"] and ats.missing_required == ["Kubernetes"]
    assert ats.synonym_matches == {"REST APIs": "Built a REST API"}  # fake evidence rejected
    assert ats.keyword_score == pytest.approx(100 * 3 / 5, abs=0.1)
    assert ats.format_checks["reading_order_ok"] and ats.format_score == 100.0


# -- pipeline ----------------------------------------------------------------------------------


def test_pipeline_end_to_end(gen_env: dict[str, Any], backend: FakeBackend, db: Session) -> None:
    script_happy(backend)
    job = gen_env["job"]
    queue_generation(db, job)
    run = run_next(gen_env["deps"], InMemorySaver())
    assert run is not None and run.state is RunState.DONE, run.error if run else None

    db.expire_all()
    job = db.get(Job, job.id)
    assert job is not None and job.status is JobStatus.RESUME_READY
    assert job.company == "Acme" and job.eligibility["eligible"] is True
    resume = db.query(Resume).filter_by(job_id=job.id).one()
    rep = resume.score_report
    assert rep["ats"]["matched_required"][:3] == ["Python", "FastAPI", "PostgreSQL"]
    assert "Kubernetes" in rep["gaps"]  # required but not in any fact → reported, not invented
    assert "Kubernetes" not in rep["ats"]["synonym_matches"]
    assert any("number 99" in r for r in rep["guard_removed"])
    assert "required: Rust" in rep["jd_terms_dropped"]
    assert "AutoApply Agent" in resume.tex_source and "99\\%" not in resume.tex_source
    assert rep["changes"]["EXPERIENCE"]["added"]
    assert rep["cost_usd"] > 0 and "openai:writer" in rep["models_used"]
    assert rep["build"]["page_count"] == 1 and rep["build"]["within_limit"]
    assert resume.ats_score == rep["ats"]["score"]


def test_ineligible_stops_unless_forced(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    backend.script("jd", json.dumps(ANALYSIS | {"eligibility": {"min_cgpa": 9.5}}))
    queue_generation(db, gen_env["job"], force=False)
    run = run_next(gen_env["deps"], InMemorySaver())
    assert run is not None and run.state is RunState.DONE and run.result["stopped"] == "ineligible"
    db.expire_all()
    job = db.get(Job, gen_env["job"].id)
    assert (
        job is not None
        and job.status is JobStatus.INELIGIBLE
        and "below the cutoff" in (job.status_reason or "")
    )
    assert db.query(Resume).filter_by(job_id=job.id).count() == 0


def test_invalid_latex_triggers_feedback_loop(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    backend.script("jd", json.dumps(ANALYSIS))
    backend.script("writer", writer_json(SKILLS=r"\input{/etc/passwd}"), writer_json())
    backend.script("ats", json.dumps({"matches": []}))
    queue_generation(db, gen_env["job"])
    run = run_next(gen_env["deps"], InMemorySaver())
    assert run is not None and run.state is RunState.DONE
    rep = db.query(Resume).filter_by(job_id=gen_env["job"].id).one().score_report
    assert "LaTeX not allowed" in rep["iterations"][0]["issues"][0]
    second_prompt = backend.calls[[c[0].model for c in backend.calls].index("writer", 2)][1][-1][
        "content"
    ]
    assert "FEEDBACK ON YOUR LAST DRAFT" in second_prompt and "not allowed" in second_prompt


def test_interrupted_run_resumes_from_checkpoint(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    script_happy(backend)
    saver = InMemorySaver()
    run = queue_generation(db, gen_env["job"])
    graph = build_graph(gen_env["deps"], saver)
    config = {"configurable": {"thread_id": run.thread_id}}
    with gen_env["maker"]() as s:
        state = initial_state(s, s.get(Job, gen_env["job"].id))
    for update in graph.stream(state, config, stream_mode="updates"):
        if "retrieve" in update:
            break  # simulate a crash right after retrieval
    run.state = RunState.RUNNING
    db.commit()

    assert recover_interrupted(db) == 1
    jd_calls = [c for c in backend.calls if c[0].model == "jd"]
    run = run_next(gen_env["deps"], saver)
    assert run is not None and run.state is RunState.DONE
    assert [c for c in backend.calls if c[0].model == "jd"] == jd_calls  # not re-analyzed


def test_llm_failure_marks_job_failed_and_notifies(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    from app.llm.errors import ErrorKind, LLMProviderError
    from app.models import Notification

    backend.script("jd", LLMProviderError(ErrorKind.NOT_FOUND, "404 retired model"))
    queue_generation(db, gen_env["job"])
    run = run_next(gen_env["deps"], InMemorySaver())
    assert run is not None and run.state is RunState.FAILED and "404" in (run.error or "")
    db.expire_all()
    assert db.get(Job, gen_env["job"].id).status is JobStatus.FAILED  # type: ignore[union-attr]
    assert db.query(Notification).filter_by(kind="pipeline.failed").count() == 1


# -- API ---------------------------------------------------------------------------------------


def test_manual_job_api(
    client: TestClient, gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    r = client.post("/api/jobs/manual", json={"jd_text": JD, "company": "Acme"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["status"] == "detected" and job["active_run"]["state"] == "queued"
    assert client.post(f"/api/jobs/{job['id']}/regenerate", json={}).status_code == 409

    script_happy(backend)
    run_next(gen_env["deps"], InMemorySaver())
    detail = client.get(f"/api/jobs/{job['id']}").json()
    assert detail["status"] == "resume_ready" and detail["latest_resume_id"] and detail["ats_score"]
    assert detail["runs"][0]["state"] == "done" and detail["jd_structured"]["company"] == "Acme"
    assert client.get(f"/api/resumes/{detail['latest_resume_id']}/pdf").status_code == 200

    r = client.patch(
        f"/api/jobs/{job['id']}", json={"status": "shortlisted", "notes": "call on Monday"}
    )
    assert r.json()["status"] == "shortlisted" and r.json()["notes"] == "call on Monday"
    assert any(j["id"] == job["id"] for j in client.get("/api/jobs?status=shortlisted").json())
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_manual_job_requires_template(client: TestClient) -> None:
    assert client.post("/api/jobs/manual", json={"jd_text": JD}).status_code == 409


def test_recover_ignores_finished_runs(db: Session, gen_env: dict[str, Any]) -> None:
    db.add(
        PipelineRun(job_id=gen_env["job"].id, kind="generate", thread_id="t", state=RunState.DONE)
    )
    db.commit()
    assert recover_interrupted(db) == 0


def test_studio_edit_respects_template_lock(
    client: TestClient, gen_env: dict[str, Any], backend: FakeBackend
) -> None:
    script_happy(backend)
    job = client.post("/api/jobs/manual", json={"jd_text": JD}).json()
    run_next(gen_env["deps"], InMemorySaver())
    rid = client.get(f"/api/jobs/{job['id']}").json()["latest_resume_id"]
    tex = client.get(f"/api/resumes/{rid}/tex").text

    edited = tex.replace("SQL", "SQL, Bash")
    r = client.post(f"/api/resumes/{rid}/edit", json={"tex": edited})
    assert r.status_code == 200, r.text
    new = r.json()["resume"]
    assert (
        new["kind"] == "manual_edit"
        and new["job_id"] == job["id"]
        and "Bash" in new["contents"]["SKILLS"]
    )

    locked = tex.replace("margin=0.5in", "margin=0.3in")  # preamble change
    r = client.post(f"/api/resumes/{rid}/edit", json={"tex": locked})
    assert r.status_code == 422 and "lock" in r.text
    unsafe = tex.replace("SQL", r"SQL \input{/etc/passwd}")
    assert client.post(f"/api/resumes/{rid}/edit", json={"tex": unsafe}).status_code == 422


def test_approve_and_withdraw(
    client: TestClient, gen_env: dict[str, Any], backend: FakeBackend
) -> None:
    job = client.post("/api/jobs/manual", json={"jd_text": JD}).json()
    assert (
        client.post(f"/api/jobs/{job['id']}/approve", json={}).status_code == 409
    )  # no resume yet
    script_happy(backend)
    run_next(gen_env["deps"], InMemorySaver())
    r = client.post(f"/api/jobs/{job['id']}/approve", json={})
    assert r.status_code == 200 and r.json()["applications"][0]["status"] == "approved"
    assert client.post(f"/api/jobs/{job['id']}/approve", json={}).status_code == 409  # twice
    r = client.delete(f"/api/jobs/{job['id']}/approval")
    assert r.status_code == 200 and r.json()["applications"] == []


def test_transient_outage_requeues_and_resumes(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.llm.errors import ErrorKind, LLMProviderError

    overloaded = LLMProviderError(ErrorKind.UNAVAILABLE, '503 {"message": "model overloaded"}')
    backend.script("jd", json.dumps(ANALYSIS))
    backend.script("writer", overloaded, overloaded)  # primary + retry both fail
    saver = InMemorySaver()
    queue_generation(db, gen_env["job"])
    run = run_next(gen_env["deps"], saver)
    assert run is not None and run.state is RunState.QUEUED and run.retries == 1
    assert run.not_before is not None and run.not_before.replace(tzinfo=UTC) > datetime.now(UTC)
    assert (
        "model overloaded" in (run.error or "")
        and "{" not in (run.error or "").split("-", 1)[-1][:5]
    )
    db.expire_all()
    job = db.get(Job, gen_env["job"].id)
    assert job is not None and job.status is not JobStatus.FAILED
    assert (job.status_reason or "").startswith("Waiting")

    assert run_next(gen_env["deps"], saver) is None  # not due yet
    r = db.get(PipelineRun, run.id)
    assert r is not None
    r.not_before = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    backend.script("writer", writer_json())
    backend.script("ats", json.dumps({"matches": []}))
    jd_calls = len([c for c in backend.calls if c[0].model == "jd"])
    run = run_next(gen_env["deps"], saver)
    assert run is not None and run.state is RunState.DONE
    assert len([c for c in backend.calls if c[0].model == "jd"]) == jd_calls  # resumed, not redone


def test_retry_endpoint_resumes_failed_run(
    client: TestClient, gen_env: dict[str, Any], backend: FakeBackend
) -> None:
    from app.llm.errors import ErrorKind, LLMProviderError

    job = client.post("/api/jobs/manual", json={"jd_text": JD}).json()
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409  # nothing failed
    backend.script("jd", LLMProviderError(ErrorKind.AUTH, "bad key"))
    run_next(gen_env["deps"], InMemorySaver())
    assert client.get(f"/api/jobs/{job['id']}").json()["status"] == "failed"
    r = client.post(f"/api/jobs/{job['id']}/retry")
    assert r.status_code == 202 and r.json()["runs"][0]["state"] == "queued"
    assert r.json()["status"] == "detected"


def test_transient_rewrite_failure_pauses_instead_of_fake_attempts(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.llm.errors import ErrorKind, LLMProviderError

    busy = LLMProviderError(ErrorKind.UNAVAILABLE, "503 overloaded")
    backend.script("jd", json.dumps(ANALYSIS))
    # first draft is invalid LaTeX (forces a rewrite); the rewrite then hits an outage
    backend.script("writer", writer_json(SKILLS=r"\input{x}"), busy, busy)
    backend.script("ats", json.dumps({"matches": []}))
    saver = InMemorySaver()
    queue_generation(db, gen_env["job"])
    run = run_next(gen_env["deps"], saver)
    assert (
        run is not None and run.state is RunState.QUEUED and run.retries == 1
    )  # paused, not faked

    stored = db.get(PipelineRun, run.id)
    assert stored is not None
    stored.not_before = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    backend.script("writer", writer_json())
    run = run_next(gen_env["deps"], saver)
    assert run is not None and run.state is RunState.DONE, run.error
    rep = db.query(Resume).filter_by(job_id=gen_env["job"].id).one().score_report
    sources = [i["source"] for i in rep["iterations"]]
    assert (
        sources[:2] == ["writer", "writer"]
        and rep["iterations"][1]["score"] != rep["iterations"][0]["score"]
    )


def test_permanent_rewrite_failure_ends_loop_without_duplicates(
    gen_env: dict[str, Any], backend: FakeBackend, db: Session
) -> None:
    from app.llm.errors import ErrorKind, LLMProviderError

    backend.script("jd", json.dumps(ANALYSIS))
    backend.script(
        "writer",
        writer_json(SKILLS=r"\input{x}"),
        LLMProviderError(ErrorKind.BAD_REQUEST, "400 too long"),
    )
    backend.script("ats", json.dumps({"matches": []}))
    queue_generation(db, gen_env["job"])
    run = run_next(gen_env["deps"], InMemorySaver())
    assert run is not None and run.state is RunState.DONE, run.error
    rep = db.query(Resume).filter_by(job_id=gen_env["job"].id).one().score_report
    assert [i["source"] for i in rep["iterations"]] == ["writer", "polish"] or [
        i["source"] for i in rep["iterations"]
    ] == ["writer"]


def test_other_users_resumes_never_claim_the_autoapply_project() -> None:
    from app.generation.facts import FactPack, guard_regions

    pack = FactPack(
        facts=[Fact(id="b1", text="Built a REST API in FastAPI and PostgreSQL", source="x")],
        author="Someone",
        tagger=SkillTagger([]),
    )
    projects = "\n".join(
        [
            r"\textbf{API Project}\\",
            r"\begin{bullets}",
            r"\item Built a REST API in FastAPI and PostgreSQL",
            r"\end{bullets}",
            r"\textbf{AutoApply Agent}\\",
            r"\begin{itemize}",
            r"\item Built an agent that applies to jobs",
            r"\end{itemize}",
        ]
    )
    out = guard_regions({"PROJECTS": projects}, {"PROJECTS": ""}, pack, "item")
    assert "AutoApply" not in out.regions["PROJECTS"] and "API Project" in out.regions["PROJECTS"]
    assert any("not your project" in r for r in out.removed)
