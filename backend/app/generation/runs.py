"""Queue and execute pipeline runs (the worker calls `run_next`)."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.generation.level import gate_and_queue
from app.generation.pipeline import Deps, build_graph, initial_state
from app.llm.errors import (
    BudgetExceededError,
    LLMCallFailedError,
    LLMError,
    LLMNotConfiguredError,
)
from app.models import Job, PipelineRun
from app.models.enums import JobStatus, NotificationLevel, RunKind, RunState
from app.services.notifications import notify

log = logging.getLogger(__name__)


@contextmanager
def checkpointer_for(database_url: str) -> Iterator[Any]:
    """Postgres checkpointer in production (resumable across restarts), memory otherwise."""
    if database_url.startswith("postgresql"):
        from langgraph.checkpoint.postgres import PostgresSaver

        conn = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        with PostgresSaver.from_conn_string(conn) as saver:
            saver.setup()
            yield saver
    else:
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()


def queue_generation(db: Session, job: Job, **options: Any) -> PipelineRun:
    n = db.query(PipelineRun).filter_by(job_id=job.id).count() + 1
    run = PipelineRun(
        job_id=job.id, kind=RunKind.GENERATE, thread_id=f"job-{job.id}-gen-{n}", options=options
    )
    db.add(run)
    db.commit()
    return run


def recover_interrupted(db: Session) -> int:
    """Runs left RUNNING by a crash are re-queued; they resume from their checkpoint."""
    n = db.execute(
        update(PipelineRun)
        .where(PipelineRun.state == RunState.RUNNING)
        .values(state=RunState.QUEUED)
    ).rowcount  # type: ignore[attr-defined]
    db.commit()
    return int(n or 0)


RETRY_DELAYS_MIN = (2, 5, 15, 30, 60)


def _retry_later(
    sessions: Callable[[], Session], run_id: int, job_id: int, error: str
) -> PipelineRun | None:
    """Re-queue after a temporary provider failure; the run resumes from its checkpoint."""
    with sessions() as db:
        r = db.get(PipelineRun, run_id)
        if r is None:
            return None
        if r.retries >= len(RETRY_DELAYS_MIN):
            r.state, r.error = RunState.FAILED, f"gave up after {r.retries} retries: {error}"[:2000]
            r.finished_at = datetime.now(UTC)
            job = db.get(Job, job_id)
            if job is not None:
                job.status, job.status_reason = JobStatus.FAILED, r.error[:500]
            db.commit()
            notify(
                db,
                NotificationLevel.ERROR,
                "pipeline.failed",
                f"Resume generation failed for job #{job_id}",
                r.error[:500],
                job_id=job_id,
            )
            return r
        delay = RETRY_DELAYS_MIN[r.retries]
        r.retries += 1
        r.state = RunState.QUEUED
        r.not_before = datetime.now(UTC) + timedelta(minutes=delay)
        r.error = f"temporary: {error}"[:2000]
        job = db.get(Job, job_id)
        if job is not None:
            local = r.not_before.strftime("%H:%M UTC")
            job.status_reason = (
                f"Waiting: the model provider is temporarily unavailable. "
                f"Retry {r.retries}/{len(RETRY_DELAYS_MIN)} at {local}."
            )
        db.commit()
        db.refresh(r)
        log.info("run %s: transient failure, retrying in %d min", run_id, delay)
        return r


def run_next(deps: Deps, checkpointer: Any) -> PipelineRun | None:
    sessions: Callable[[], Session] = deps.sessions
    with sessions() as db:
        now = datetime.now(UTC)
        run = db.scalar(
            select(PipelineRun)
            .where(PipelineRun.state == RunState.QUEUED, PipelineRun.kind == RunKind.GENERATE)
            .where(or_(PipelineRun.not_before.is_(None), PipelineRun.not_before <= now))
            .order_by(PipelineRun.id)
            .limit(1)
        )
        if run is None:
            return None
        run.state = RunState.RUNNING
        run.started_at = run.started_at or datetime.now(UTC)
        db.commit()
        run_id, thread_id, job_id, options = run.id, run.thread_id, run.job_id, dict(run.options)

    graph = build_graph(deps, checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    error: str | None = None
    job_status: JobStatus | None = None
    try:
        snapshot = graph.get_state(config)
        if snapshot and snapshot.next:  # resume an interrupted run
            stream_input = None
        else:
            with sessions() as db:
                job = db.get(Job, job_id)
                if job is None:
                    raise LookupError(f"job {job_id} was deleted")
                stream_input = initial_state(db, job, **options)
        for update_ in graph.stream(stream_input, config, stream_mode="updates"):
            step = next(iter(update_), None)
            with sessions() as db:
                r = db.get(PipelineRun, run_id)
                if r is not None:
                    r.step = step
                    db.commit()
        final = graph.get_state(config).values
        result = {"resume_id": final.get("resume_id"), "stopped": final.get("stopped")}
    except BudgetExceededError as exc:
        error, job_status = f"paused: {exc}", JobStatus.PAUSED
    except (LLMNotConfiguredError, LookupError, ValueError) as exc:
        error, job_status = str(exc), JobStatus.FAILED
    except LLMCallFailedError as exc:
        if exc.transient:
            return _retry_later(sessions, run_id, job_id, str(exc))
        error, job_status = f"LLM error: {exc}", JobStatus.FAILED
    except LLMError as exc:
        error, job_status = f"LLM error: {exc}", JobStatus.FAILED
    except Exception as exc:  # keep the worker alive; surface the failure
        log.exception("pipeline run %s crashed", run_id)
        error, job_status = f"unexpected error: {type(exc).__name__}: {exc}", JobStatus.FAILED

    with sessions() as db:
        r = db.get(PipelineRun, run_id)
        assert r is not None
        r.finished_at = datetime.now(UTC)
        if error is None:
            r.state, r.result, r.error = RunState.DONE, result, None
            if result.get("resume_id"):
                after_generation(db, job_id)
        else:
            r.state, r.error = RunState.FAILED, error[:2000]
            job = db.get(Job, job_id)
            if job is not None and job_status is not None:
                job.status = job_status
                job.status_reason = error[:500]
        db.commit()
        if error is not None:
            notify(
                db,
                NotificationLevel.ERROR,
                "pipeline.failed",
                f"Resume generation failed for job #{job_id}",
                error[:500],
                job_id=job_id,
            )
        db.refresh(r)
        return r


# -- portal runs + the email-job lifecycle -------------------------------------------------


def queue_portal(db: Session, job: Job, *, then_generate: bool, merge_email: bool) -> PipelineRun:
    n = db.query(PipelineRun).filter_by(job_id=job.id).count() + 1
    run = PipelineRun(
        job_id=job.id,
        kind=RunKind.SCRAPE,
        thread_id=f"job-{job.id}-scrape-{n}",
        options={"then_generate": then_generate, "merge_email": merge_email},
    )
    db.add(run)
    db.commit()
    return run


def queue_apply(db: Session, job: Job, application_id: int) -> PipelineRun:
    n = db.query(PipelineRun).filter_by(job_id=job.id).count() + 1
    run = PipelineRun(
        job_id=job.id,
        kind=RunKind.APPLY,
        thread_id=f"job-{job.id}-apply-{n}",
        options={"application_id": application_id},
    )
    db.add(run)
    db.commit()
    return run


def after_generation(db: Session, job_id: int) -> None:
    """Email jobs: wait for approval (default) or auto-apply when allowed (hard rule 6)."""
    from app.models import Application, JobSite, Resume
    from app.models.enums import ApplicationStatus, JobSource, ResumeKind, SiteMode
    from app.services.settings_service import get_app_settings

    job = db.get(Job, job_id)
    if job is None or job.source not in (JobSource.EMAIL, JobSource.SITE):
        return
    if job.status is JobStatus.INELIGIBLE:
        return
    site = db.get(JobSite, job.job_site_id) if job.job_site_id else None
    resume = db.scalar(
        select(Resume)
        .where(Resume.job_id == job_id, Resume.kind == ResumeKind.GENERATED)
        .order_by(Resume.id.desc())
    )
    passed = bool(resume and (resume.score_report or {}).get("passed"))
    if site is not None and site.mode is SiteMode.SEARCH_ONLY:
        job.status = JobStatus.RESUME_READY
        db.commit()
        notify(
            db,
            NotificationLevel.INFO,
            "job.ready_manual",
            f"Resume ready: {job.company or 'job'} – {job.role or ''}",
            f"{site.name} is set to search only, so apply yourself: {job.apply_url}",
            job_id=job_id,
        )
        return
    if (
        site is not None
        and site.mode is SiteMode.SEARCH_AND_APPLY
        and passed
        and job.apply_url
        and resume is not None
    ):
        # Review window: the resume is ready now; the agent applies when the window ends
        # unless the user clicks "Don't apply" ("Apply now" sends it straight away).
        window = site.apply_delay_minutes
        when = datetime.now(UTC) + timedelta(minutes=window)
        app_ = Application(
            job_id=job_id,
            resume_id=resume.id,
            status=ApplicationStatus.PREPARED,
            auto_submit_at=when,
        )
        db.add(app_)
        job.status = JobStatus.AWAITING_APPROVAL
        db.commit()
        if window <= 0:
            release_due_auto_submits(db)
            return
        notify(
            db,
            NotificationLevel.INFO,
            "job.ready_auto",
            f"Resume ready: {job.company or 'job'} – {job.role or ''}",
            f"Applies automatically at {when:%H:%M} UTC ({window} min) unless you click "
            "Don't apply. Click Apply now to send it straight away.",
            job_id=job_id,
        )
        return
    if job.source is JobSource.EMAIL and not job.portal_id:
        # No site the agent may apply on (LinkedIn Easy Apply, or a company site you
        # haven't allowed yet): the resume is ready and you apply yourself.
        job.status = JobStatus.RESUME_READY
        db.commit()
        notify(
            db,
            NotificationLevel.INFO,
            "job.ready_manual",
            f"Resume ready: {job.company or 'job'} – {job.role or ''}",
            " ".join(x for x in (job.notes, job.apply_url) if x) or "Apply yourself.",
            job_id=job_id,
        )
        return
    if get_app_settings(db).auto_apply and passed and job.apply_url and resume is not None:
        app_ = Application(
            job_id=job_id,
            resume_id=resume.id,
            status=ApplicationStatus.APPROVED,
            approved_at=datetime.now(UTC),
        )
        db.add(app_)
        db.flush()
        queue_apply(db, job, app_.id)
        return
    job.status = JobStatus.AWAITING_APPROVAL
    db.commit()
    notify(
        db,
        NotificationLevel.INFO,
        "job.ready",
        f"Resume ready for review: {job.company or 'job'} – {job.role or ''}",
        "Approve it on the job page to let the portal agent apply"
        + (
            ""
            if passed
            else " (it didn't pass every quality check, so it won't be sent "
            "automatically; review carefully)"
        )
        + ".",
        job_id=job_id,
    )


def release_due_auto_submits(db: Session) -> int:
    """Approve PREPARED applications whose review window has ended and queue the apply."""
    from app.models import Application
    from app.models.enums import ApplicationStatus

    now = datetime.now(UTC)
    due = list(
        db.scalars(
            select(Application).where(
                Application.status == ApplicationStatus.PREPARED,
                Application.auto_submit_at.is_not(None),
                Application.auto_submit_at <= now,
            )
        )
    )
    for app_ in due:
        app_.status, app_.approved_at = ApplicationStatus.APPROVED, now
        db.commit()
        job = db.get(Job, app_.job_id)
        if job is not None:
            queue_apply(db, job, app_.id)
    return len(due)


def run_next_portal(agent: Any, sessions: Callable[[], Session]) -> PipelineRun | None:
    from app.models import Application
    from app.models.enums import ApplicationStatus
    from app.portal.agent import HumanNeeded, PortalError

    with sessions() as db:
        run = db.scalar(
            select(PipelineRun)
            .where(
                PipelineRun.state == RunState.QUEUED,
                PipelineRun.kind.in_([RunKind.SCRAPE, RunKind.APPLY]),
            )
            .order_by(PipelineRun.id)
            .limit(1)
        )
        if run is None:
            return None
        run.state, run.started_at = RunState.RUNNING, datetime.now(UTC)
        run.step = "opening the portal"
        db.commit()
        run_id, job_id, kind, options = run.id, run.job_id, run.kind, dict(run.options)

    error: str | None = None
    job_status: JobStatus | None = None
    try:
        if kind is RunKind.SCRAPE:
            agent.scrape(job_id, merge_email=bool(options.get("merge_email")))
        else:
            agent.apply(job_id, int(options["application_id"]))
    except HumanNeeded as exc:
        error, job_status = str(exc), JobStatus.PAUSED
    except PortalError as exc:
        error, job_status = str(exc), JobStatus.FAILED
    except LLMError as exc:
        error, job_status = f"LLM error: {exc}", JobStatus.FAILED
    except Exception as exc:  # noqa: BLE001 — surface anything (browser crashes, timeouts)
        log.exception("portal run %s crashed", run_id)
        error, job_status = (
            f"portal agent error: {type(exc).__name__}: {str(exc)[:300]}",
            JobStatus.FAILED,
        )

    with sessions() as db:
        r = db.get(PipelineRun, run_id)
        job = db.get(Job, job_id)
        assert r is not None
        r.finished_at = datetime.now(UTC)
        if error is None:
            r.state, r.error = RunState.DONE, None
            db.commit()
            if kind is RunKind.SCRAPE and options.get("then_generate") and job is not None:
                gate_and_queue(db, job, queue_generation, getattr(agent, "_gateway", None))
        elif (
            kind is RunKind.SCRAPE
            and options.get("merge_email")
            and job is not None
            and job.jd_text
        ):
            # Read-email mode: the portal page was only extra context. Carry on with the JD
            # from the email rather than stopping the job.
            r.state, r.error = RunState.FAILED, error[:2000]
            job.status_reason = (
                f"Couldn't read the portal page ({error[:200]}); the resume is based on the "
                "email only."
            )
            db.commit()
            if options.get("then_generate"):
                gate_and_queue(db, job, queue_generation, getattr(agent, "_gateway", None))
        else:
            r.state, r.error = RunState.FAILED, error[:2000]
            outcome = "paused" if job_status is JobStatus.PAUSED else "failed"
            if job is not None and job_status is not None:
                if kind is RunKind.APPLY:
                    a = db.get(Application, int(options["application_id"]))
                    if a is not None and a.status is not ApplicationStatus.SUBMITTED:
                        a.status, a.error = ApplicationStatus.FAILED, error[:1000]
                job.status, job.status_reason = job_status, error[:500]
            db.commit()
            notify(
                db,
                NotificationLevel.ERROR
                if job_status is JobStatus.FAILED
                else NotificationLevel.WARNING,
                f"portal.{kind.value}",
                f"Portal {kind.value} {outcome} for job #{job_id}",
                error[:500],
                job_id=job_id,
            )
        db.refresh(r)
        return r
