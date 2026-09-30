"""Resume generation pipeline (LangGraph).

    analyze → eligibility → retrieve → write → evaluate ─┬─ passed / out of tries → polish
                   │ (ineligible, not forced)             └─ else, with feedback → write
                   └→ END                                              polish → finalize → END

State is plain JSON so LangGraph can checkpoint it after every node (per job thread);
a crashed run resumes from the last completed node. Heavy dependencies (gateway, DB,
compiler) are closed over, never stored in state.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.generation.ats import ATSBreakdown, score_resume
from app.generation.facts import Fact, FactPack, build_facts, guard_regions
from app.generation.jd import JDAnalysis, analyze_jd, check_eligibility
from app.generation.review import review_draft, review_feedback
from app.generation.structure import check_structure, projects_available
from app.knowledge.latex_text import to_text
from app.knowledge.past_resumes import build_tagger
from app.latex.compiler import Compiler
from app.latex.fit import find_bullets
from app.latex.pdf import extract_text
from app.latex.regions import TemplateError, bullet_command, parse_regions
from app.latex.render import render
from app.latex.sanitize import ContentError, build_policy, strip_comments
from app.learning import link_resume_bullets
from app.llm.errors import LLMCallFailedError, LLMError
from app.llm.gateway import LLMGateway
from app.models import Job, LLMUsage, Resume
from app.models.enums import JobStatus, LLMTask, ResumeKind
from app.services.settings_service import get_app_settings, get_profile
from app.services.templates import (
    _template_dir,
    active_template,
    get_template,
    render_resume,
    report_json,
)

log = logging.getLogger(__name__)


class WriterOutput(BaseModel):
    regions: dict[str, str]
    used_facts: list[str] = Field(default_factory=list)
    notes: str = ""


class PolishOutput(BaseModel):
    regions: dict[str, str]


class GenState(TypedDict, total=False):
    job_id: int
    jd_text: str
    template_id: int
    page_limit: int
    threshold: int
    max_iterations: int
    force: bool  # generate even if ineligible (manual mode)
    urgent: bool
    analysis: dict[str, Any]
    analysis_dropped: list[str]
    eligibility: dict[str, Any]
    facts: list[dict[str, Any]]
    author: str
    iteration: int
    feedback: list[str]
    attempts: list[dict[str, Any]]  # {regions, score, passed, issues, report, ...}
    best: int  # index into attempts
    stopped: str  # why the graph ended early (ineligible)
    signature_required: bool  # add the AutoApply Agent project (its creator's resumes only)
    writer_stopped: str  # non-empty when a rewrite failed permanently
    resume_id: int


@dataclass
class Deps:
    gateway: LLMGateway
    sessions: Callable[[], Session]
    compiler: Compiler


# -- helpers ------------------------------------------------------------------------------


def _pack(state: GenState, db: Session) -> FactPack:
    return FactPack(
        facts=[Fact(**f) for f in state["facts"]], author=state["author"], tagger=build_tagger(db)
    )


def _base_regions(template_tex: str) -> dict[str, str]:
    return {r.name: r.content for r in parse_regions(template_tex)}


def _bullet_texts(content: str, command: str) -> list[str]:
    return [to_text(strip_comments(b.text)) for b in find_bullets("", content, command)]


def changes_vs_base(base: dict[str, str], new: dict[str, str], command: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in base:
        before = _bullet_texts(base[name], command)
        after = _bullet_texts(new.get(name, base[name]), command)
        added = [b for b in after if b not in before]
        removed = [b for b in before if b not in after]
        if added or removed or new.get(name, base[name]) != base[name]:
            out[name] = {
                "added": added,
                "removed": removed,
                "kept": len([b for b in after if b in before]),
            }
    return out


def _passes(attempt: dict[str, Any], threshold: int) -> bool:
    r = attempt.get("report") or {}
    return bool(
        attempt.get("compiled")
        and r.get("within_limit")
        and r.get("alignment_ok")
        and attempt.get("signature")
        and (attempt.get("score") or 0) >= threshold
        and (attempt.get("structure") or {}).get("ok", True)  # filled page, 3×2+ projects
    )


# -- the graph ----------------------------------------------------------------------------


def build_graph(deps: Deps, checkpointer: Any = None) -> Any:
    gw, sessions, compiler = deps.gateway, deps.sessions, deps.compiler

    def analyze(state: GenState) -> GenState:
        analysis, dropped, _ = analyze_jd(
            gw, state["jd_text"], job_id=state["job_id"], urgent=state.get("urgent", False)
        )
        with sessions() as db:
            job = db.get(Job, state["job_id"])
            if job is not None:
                job.company = job.company or analysis.company
                job.role = job.role or analysis.role
                job.location = job.location or analysis.location
                job.ctc = job.ctc or analysis.ctc
                job.jd_structured = analysis.model_dump()
                if job.status is JobStatus.DETECTED:
                    job.status = JobStatus.SCRAPED
                db.commit()
        return {"analysis": analysis.model_dump(), "analysis_dropped": dropped}

    def eligibility(state: GenState) -> GenState:
        analysis = JDAnalysis.model_validate(state["analysis"])
        with sessions() as db:
            result = check_eligibility(analysis.eligibility, get_profile(db))
            job = db.get(Job, state["job_id"])
            if job is not None:
                job.eligibility = {**analysis.eligibility.model_dump(), **result.model_dump()}
                if result.eligible is False:
                    job.status_reason = "Ineligible: " + "; ".join(result.reasons)
                    if not state.get("force"):
                        job.status = JobStatus.INELIGIBLE
                db.commit()
        update: GenState = {"eligibility": result.model_dump()}
        if result.eligible is False and not state.get("force"):
            update["stopped"] = "ineligible"
        return update

    def retrieve(state: GenState) -> GenState:
        analysis = JDAnalysis.model_validate(state["analysis"])
        with sessions() as db:
            template = get_template(db, state["template_id"])
            pack = build_facts(
                db, gw, analysis, state["jd_text"], _base_regions(template.tex_source)
            )
        return {
            "facts": [asdict(f) for f in pack.facts],
            "author": pack.author,
            "iteration": 0,
            "feedback": [],
            "attempts": [],
        }

    def write(state: GenState) -> GenState:
        analysis = JDAnalysis.model_validate(state["analysis"])
        with sessions() as db:
            template = get_template(db, state["template_id"])
            base = _base_regions(template.tex_source)
            pack = _pack(state, db)
        attempts = state.get("attempts") or []
        previous = attempts[state["best"]]["regions"] if attempts and "best" in state else None
        feedback = state.get("feedback") or []
        try:
            out = gw.structured(
                LLMTask.RESUME_WRITER,
                "resume_write",
                {
                    "role": analysis.role,
                    "company": analysis.company,
                    "required": analysis.required_skills,
                    "preferred": analysis.preferred_skills,
                    "keywords": analysis.keywords,
                    "responsibilities": analysis.responsibilities,
                    "regions": base,
                    "bullet_command": bullet_command(template.tex_source),
                    "facts": pack.as_prompt_lines(),
                    "author": pack.author,
                    "page_limit": state["page_limit"],
                    "include_signature": state.get("signature_required", True),
                    "previous": previous,
                    "feedback": feedback,
                },
                WriterOutput,
                job_id=state["job_id"],
                urgent=state.get("urgent", False),
            ).value
            regions = {k: v for k, v in out.regions.items() if k in base}
            attempt: dict[str, Any] = {
                "regions": regions,
                "used_facts": out.used_facts,
                "writer_notes": out.notes,
                "source": "writer",
            }
        except LLMCallFailedError as exc:
            if not attempts or exc.transient:
                # Provider overloaded / rate-limited: let the runner retry later from this
                # checkpoint instead of burning a loop iteration on a non-attempt.
                raise
            return {"writer_stopped": f"rewrite failed: {str(exc)[:300]}"}
        except LLMError as exc:
            if not attempts:
                raise
            return {"writer_stopped": f"rewrite failed: {str(exc)[:300]}"}
        return {"attempts": [*attempts, attempt], "writer_stopped": ""}

    def evaluate_regions(
        state: GenState, regions: dict[str, str], *, review: bool = False, writer_notes: str = ""
    ) -> dict[str, Any]:
        """Guard → render (fit) → ATS score (→ ATS review for the writer when `review` and
        the draft didn't pass). Returns an attempt record (JSON)."""
        analysis = JDAnalysis.model_validate(state["analysis"])
        with sessions() as db:
            template = get_template(db, state["template_id"])
            base = _base_regions(template.tex_source)
            pack = _pack(state, db)
            tex_template = template.tex_source

        command = bullet_command(tex_template)
        guarded = guard_regions(regions, base, pack, command)
        record: dict[str, Any] = {
            "regions": guarded.regions,
            "guard_removed": guarded.removed,
            # not required unless you built it (setting include_signature_project)
            "signature": guarded.signature_present or not state.get("signature_required", True),
            "issues": [],
        }
        # Invalid LaTeX from the writer: that region falls back to the user's current
        # content (so every attempt stays renderable) and the writer gets told why.
        regions = dict(guarded.regions)
        report = None
        for _ in range(len(regions) + 1):
            try:
                report = render(
                    tex_template,
                    regions,
                    compiler=compiler,
                    page_limit=state["page_limit"],
                    fit=True,
                    assets_dir=_template_dir(state["template_id"]),
                )
                break
            except (ContentError, TemplateError) as exc:
                record["issues"].append(f"LaTeX not allowed: {exc}")
                bad = str(exc).split(":", 1)[0].strip()
                if bad not in regions or regions[bad] == base.get(bad):
                    record.update(compiled=False, report=None, score=0, regions=regions)
                    return record
                regions[bad] = base[bad]
        assert report is not None
        record["report"] = report_json(report)
        record["regions"] = report.contents  # after fitting
        record["compiled"] = report.compiled
        if not report.compiled or report.pdf is None:
            record["score"] = 0
            record["issues"].append("did not compile: " + "; ".join(report.errors[:3]))
            return record
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "r.pdf"
            pdf_path.write_bytes(report.pdf)
            pdf_text = extract_text(pdf_path)
        ats: ATSBreakdown = score_resume(
            gw,
            analysis=analysis,
            jd_text=state["jd_text"],
            pdf_text=pdf_text,
            tex=report.tex,
            region_contents=report.contents,
            job_id=state["job_id"],
        )
        record["ats"] = ats.model_dump()
        record["score"] = ats.score
        fact_skills = pack.skills()
        record["addable"] = [
            s for s in ats.missing_required + ats.missing_preferred if s.lower() in fact_skills
        ]
        record["gaps"] = [
            s for s in ats.missing_required + ats.missing_preferred if s.lower() not in fact_skills
        ]
        base_project_groups = 0
        project_region = next((r for r in base if "PROJECT" in r.upper()), None)
        if project_region:
            base_project_groups = len(
                {
                    b.group
                    for b in find_bullets(project_region, base[project_region], command)
                    if "autoapply" not in b.text.lower()
                }
            )
        structure = check_structure(
            report.contents,
            command,
            pages=report.page_count,
            page_limit=state["page_limit"],
            last_page_fill=report.last_page_fill,
            projects_available=projects_available(
                [f["id"] for f in state["facts"]], base_project_groups
            ),
        )
        record["structure"] = structure.as_dict()
        record["fill"] = report.last_page_fill
        needs_review = review and not _passes(record, state["threshold"])
        critique = review_draft(
            gw,
            analysis=analysis,
            ats=ats,
            pack=pack,
            resume_text=pdf_text,
            regions=list(base),
            threshold=state["threshold"],
            writer_notes=writer_notes,
            job_id=state["job_id"],
            urgent=state.get("urgent", False),
            use_llm=needs_review,
        )
        record["review"] = critique.as_dict()
        record["ceiling"] = critique.ceiling
        return record

    def evaluate(state: GenState) -> GenState:
        attempts = list(state["attempts"])
        latest = attempts[-1]
        more_rounds = state.get("iteration", 0) + 1 < state["max_iterations"]
        record = evaluate_regions(
            state,
            latest["regions"],
            review=more_rounds,
            writer_notes=str(latest.get("writer_notes") or ""),
        )
        record.update({k: v for k, v in latest.items() if k not in record})
        record["passed"] = _passes(record, state["threshold"])
        attempts[-1] = record
        best = max(
            range(len(attempts)),
            key=lambda i: (
                attempts[i].get("passed", False),
                attempts[i].get("score", 0),
                attempts[i].get("fill") or 0,
            ),
        )
        return {
            "attempts": attempts,
            "best": best,
            "iteration": state.get("iteration", 0) + 1,
            "feedback": _feedback(record, state),
        }

    def route_after_eval(state: GenState) -> str:
        if state["attempts"][-1].get("passed"):
            return "polish"
        if state["iteration"] >= state["max_iterations"]:
            return "polish"
        return "write"

    def polish(state: GenState) -> GenState:
        analysis = JDAnalysis.model_validate(state["analysis"])
        attempts = list(state["attempts"])
        best = attempts[state["best"]]
        if not best.get("compiled"):
            return {}
        try:
            out = gw.structured(
                LLMTask.FINAL_POLISH,
                "final_polish",
                {
                    "role": analysis.role,
                    "regions": best["regions"],
                    "terms": [*analysis.required_skills, *analysis.preferred_skills],
                },
                PolishOutput,
                job_id=state["job_id"],
                urgent=state.get("urgent", False),
            ).value
        except LLMCallFailedError as exc:
            if exc.transient:
                raise  # retry later from this checkpoint (polish is cheap to redo)
            log.warning("polish skipped: %s", exc)
            return {}
        except LLMError as exc:
            log.warning("polish skipped: %s", exc)
            return {}
        regions = {k: out.regions.get(k, v) for k, v in best["regions"].items()}
        record = evaluate_regions(state, regions)
        record["source"] = "polish"
        record["passed"] = _passes(record, state["threshold"])
        attempts.append(record)
        # keep the polish only if it isn't worse
        keep = (record.get("passed", False), record.get("score", 0)) >= (
            best.get("passed", False),
            best.get("score", 0),
        )
        return {"attempts": attempts, "best": len(attempts) - 1 if keep else state["best"]}

    def finalize(state: GenState) -> GenState:
        best = state["attempts"][state["best"]]
        with sessions() as db:
            template = get_template(db, state["template_id"])
            base = _base_regions(template.tex_source)
            command = bullet_command(template.tex_source)
            resume, report = render_resume(
                db,
                compiler,
                contents=best["regions"],
                template_id=state["template_id"],
                page_limit=state["page_limit"],
                fit=True,
                label="generated",
                job_id=state["job_id"],
                kind=ResumeKind.GENERATED,
            )
            cost = db.scalar(
                select(func.coalesce(func.sum(LLMUsage.cost_usd), 0)).where(
                    LLMUsage.job_id == state["job_id"]
                )
            ) or Decimal(0)
            models = sorted(
                {
                    f"{k}:{m}"
                    for k, m in db.execute(
                        select(LLMUsage.provider_kind, LLMUsage.model)
                        .where(LLMUsage.job_id == state["job_id"], LLMUsage.success.is_(True))
                        .distinct()
                    )
                }
            )
            gaps = sorted(
                {g for a in state["attempts"] for g in a.get("gaps", [])}
                - set(best.get("ats", {}).get("matched_required", []))
                - set(best.get("ats", {}).get("matched_preferred", []))
            )
            score_report = {
                "passed": bool(best.get("passed")),
                "threshold": state["threshold"],
                "ats": best.get("ats"),
                "build": report_json(report),
                "gaps": gaps,
                "guard_removed": best.get("guard_removed", []),
                "jd_terms_dropped": state.get("analysis_dropped", []),
                "changes": changes_vs_base(base, report.contents, command),
                "iterations": [
                    {
                        "source": a.get("source"),
                        "score": a.get("score"),
                        "passed": a.get("passed", False),
                        "issues": a.get("issues", []),
                        "pages": (a.get("report") or {}).get("page_count"),
                    }
                    for a in state["attempts"]
                ],
                "structure": best.get("structure"),
                "review": _last_review(state["attempts"]),
                "ceiling": best.get("ceiling"),
                "writer_stopped": state.get("writer_stopped") or None,
                "eligibility": state.get("eligibility"),
                "used_facts": best.get("used_facts", []),
                "models_used": models,
                "cost_usd": float(cost),
            }
            if resume is None:
                raise RuntimeError("final render failed: " + "; ".join(report.errors))
            link_resume_bullets(db, resume, best.get("used_facts", []))
            resume.ats_score = best.get("score")
            resume.score_report = score_report
            resume.models_used = models
            resume.cost_usd = Decimal(str(cost))
            job = db.get(Job, state["job_id"])
            if job is not None:
                if job.status not in (JobStatus.INELIGIBLE,):
                    job.status = JobStatus.RESUME_READY
                if not best.get("passed"):
                    job.status_reason = (
                        f"Best ATS score {best.get('score')} (threshold {state['threshold']})"
                        if best.get("compiled")
                        else "No attempt compiled cleanly"
                    )
                    ceiling = best.get("ceiling")
                    if (
                        best.get("compiled")
                        and ceiling is not None
                        and ceiling < state["threshold"]
                    ):
                        job.status_reason += (
                            f"; about {ceiling:.0f} is reachable with your current facts"
                        )
            db.commit()
            return {"resume_id": resume.id}

    g = StateGraph(GenState)
    for name, fn in [
        ("analyze", analyze),
        ("eligibility", eligibility),
        ("retrieve", retrieve),
        ("write", write),
        ("evaluate", evaluate),
        ("polish", polish),
        ("finalize", finalize),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "analyze")
    g.add_edge("analyze", "eligibility")
    g.add_conditional_edges("eligibility", lambda s: END if s.get("stopped") else "retrieve")
    g.add_edge("retrieve", "write")
    # a permanently failed rewrite ends the loop with the best real attempt so far
    g.add_conditional_edges("write", lambda s: "polish" if s.get("writer_stopped") else "evaluate")
    g.add_conditional_edges("evaluate", route_after_eval)
    g.add_edge("polish", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


def _last_review(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The most recent reviewer critique that had model suggestions (what the writer saw)."""
    for a in reversed(attempts):
        review = a.get("review")
        if review and (review.get("suggestions") or review.get("summary")):
            return dict(review)
    return None


def _feedback(record: dict[str, Any], state: GenState) -> list[str]:
    fb: list[str] = list(record.get("issues", []))
    fb += (record.get("structure") or {}).get("problems", [])
    report = record.get("report") or {}
    for r in record.get("guard_removed", []):
        fb.append(f"Unsupported content was removed ({r}). Use only facts from the list.")
    if not record.get("signature"):
        fb.append(
            "The AutoApply Agent signature project is missing; add it to the projects "
            "region with %%pin bullets."
        )
    if report and not report.get("within_limit"):
        fb.append(
            f"Too long for {state['page_limit']} page(s) even after dropping bullets: "
            "use fewer entries and shorter bullets."
        )
    for b in report.get("overfull") or []:
        fb.append(
            f"Region {b.get('region') or '?'} has a line {b['amount_pt']:.0f}pt too wide: "
            "shorten it or break long words/URLs."
        )
    # (trimming low-priority bullets to fit is intended: the writer overfills on purpose)
    score = record.get("score")
    if score is not None and score < state["threshold"]:
        ats = record.get("ats") or {}
        fb.append(
            f"ATS score {score} is below {state['threshold']} (keywords "
            f"{ats.get('keyword_score')}, semantic {ats.get('semantic_score')}, format "
            f"{ats.get('format_score')})."
        )
        review = record.get("review")
        if review:
            fb += review_feedback(review)
        elif record.get("addable"):
            fb.append(
                "Mention these job skills; the facts support them: " + ", ".join(record["addable"])
            )
    return fb


def initial_state(
    db: Session,
    job: Job,
    *,
    template_id: int | None = None,
    page_limit: int | None = None,
    force: bool = False,
    urgent: bool = False,
) -> GenState:
    settings = get_app_settings(db)
    template = get_template(db, template_id) if template_id else active_template(db)
    if template is None:
        raise LookupError("upload a resume template first")
    if not job.jd_text:
        raise ValueError("job has no job description text")
    build_policy(template.tex_source)  # validates the template
    return {
        "job_id": job.id,
        "jd_text": job.jd_text,
        "template_id": template.id,
        "page_limit": page_limit or settings.page_limit,
        "threshold": settings.ats_threshold,
        "max_iterations": settings.max_quality_iterations,
        "force": force,
        "signature_required": settings.include_signature_project,
        "urgent": urgent,
    }


def latest_resume_for(db: Session, job_id: int) -> Resume | None:
    return db.scalar(
        select(Resume)
        .where(Resume.job_id == job_id, Resume.kind == ResumeKind.GENERATED)
        .order_by(Resume.id.desc())
    )
