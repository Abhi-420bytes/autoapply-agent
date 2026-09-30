from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.boards.service import allow_apply_site, search_apply_page, set_apply_link
from app.core.config import get_config
from app.db.session import get_db
from app.generation.level import label_job
from app.generation.runs import queue_apply, queue_generation
from app.learning import record_status_outcome
from app.models import (
    Application,
    Job,
    JobFile,
    JobSite,
    PipelineRun,
    Resume,
    Setting,
    SourceEmail,
)
from app.models.enums import (
    ApplicationStatus,
    AuditActor,
    JobSource,
    JobStatus,
    ResumeKind,
    RunKind,
    RunState,
    SiteMode,
)
from app.portal.agent import PROMPT_KEY
from app.schemas.templates import ResumeOut, resume_out
from app.search.web import SearchError, make_searcher
from app.services.audit import audit
from app.services.templates import active_template

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class ManualJobIn(BaseModel):
    jd_text: str = Field(min_length=50, max_length=60_000)
    company: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, max_length=255)
    page_limit: int | None = Field(default=None, ge=1, le=3)
    force: bool = True  # manual mode: generate even if the eligibility check fails


class ApplicationOut(BaseModel):
    id: int
    resume_id: int | None
    status: str
    auto_submit_at: datetime | None
    approved_at: datetime | None
    submitted_at: datetime | None
    error: str | None


class ApproveIn(BaseModel):
    resume_id: int | None = None  # default: the latest generated/edited version


class FileOut(BaseModel):
    id: int
    kind: str
    original_name: str | None
    mime_type: str | None
    created_at: datetime


class SourceEmailOut(BaseModel):
    id: int
    sender: str
    original_sender: str | None
    subject: str | None
    received_at: datetime
    classification: str
    body_html: str | None
    body_text: str | None
    extracted_link: str | None
    resolved_link: str | None
    link_safe: bool | None
    link_check_reason: str | None


class PromptOut(BaseModel):
    kind: str
    question: str
    screenshot_file_id: int | None
    asked_at: str


class PromptAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=200)


class RunOut(BaseModel):
    id: int
    kind: str
    state: str
    step: str | None
    error: str | None
    result: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobOut(BaseModel):
    id: int
    source: str
    status: str
    status_reason: str | None
    company: str | None
    role: str | None
    location: str | None
    ctc: str | None
    deadline: datetime | None
    detected_at: datetime
    scheduled_at: datetime | None
    notes: str | None
    portal_id: int | None = None
    level: str | None = None
    level_reason: str | None = None
    latest_resume_id: int | None
    ats_score: float | None
    active_run: RunOut | None


class JobDetail(JobOut):
    jd_text: str | None
    jd_structured: dict[str, Any] | None
    eligibility: dict[str, Any] | None
    apply_url: str | None
    runs: list[RunOut]
    resumes: list[ResumeOut]
    applications: list[ApplicationOut]
    apply_mode: str | None
    files: list[FileOut]
    source_emails: list[SourceEmailOut]
    prompt: PromptOut | None


class JobPatch(BaseModel):
    status: JobStatus | None = None
    notes: str | None = Field(default=None, max_length=10_000)
    company: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, max_length=255)


class RegenerateIn(BaseModel):
    page_limit: int | None = Field(default=None, ge=1, le=3)
    force: bool = True


def _run_out(r: PipelineRun) -> RunOut:
    return RunOut(
        id=r.id,
        kind=r.kind.value,
        state=r.state.value,
        step=r.step,
        error=r.error,
        result=r.result,
        created_at=r.created_at,
        started_at=r.started_at,
        finished_at=r.finished_at,
    )


def _job_out(db: Session, j: Job, cls: type[JobOut] = JobOut, **extra: Any) -> Any:
    latest = db.scalar(
        select(Resume)
        .where(Resume.job_id == j.id, Resume.kind == ResumeKind.GENERATED)
        .order_by(Resume.id.desc())
    )
    run = db.scalar(
        select(PipelineRun)
        .where(
            PipelineRun.job_id == j.id, PipelineRun.state.in_([RunState.QUEUED, RunState.RUNNING])
        )
        .order_by(PipelineRun.id.desc())
    )
    return cls(
        id=j.id,
        source=j.source.value,
        status=j.status.value,
        status_reason=j.status_reason,
        company=j.company,
        role=j.role,
        location=j.location,
        ctc=j.ctc,
        deadline=j.deadline,
        detected_at=j.detected_at,
        scheduled_at=j.scheduled_at,
        notes=j.notes,
        portal_id=j.portal_id,
        level=j.level,
        level_reason=j.level_reason,
        latest_resume_id=latest.id if latest else None,
        ats_score=latest.ats_score if latest else None,
        active_run=_run_out(run) if run else None,
        **extra,
    )


def _get(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.post("/manual", response_model=JobDetail, status_code=201)
def create_manual(body: ManualJobIn, db: Session = Depends(get_db)) -> Any:
    if active_template(db) is None:
        raise HTTPException(409, "upload a resume template first (Resume template page)")
    job = Job(
        source=JobSource.MANUAL,
        status=JobStatus.DETECTED,
        jd_text=body.jd_text.strip(),
        company=body.company,
        role=body.role,
    )
    db.add(job)
    db.flush()
    audit(db, AuditActor.USER, "job.manual_created", entity_type="job", entity_id=job.id)
    db.commit()
    label_job(job)
    db.commit()
    queue_generation(db, job, page_limit=body.page_limit, force=body.force)
    return job_detail(job.id, db)


@router.get("", response_model=list[JobOut])
def list_jobs(
    status: JobStatus | None = None,
    level: Literal["fresher", "experienced", "unknown"] | None = None,
    db: Session = Depends(get_db),
) -> list[Any]:
    stmt = select(Job).order_by(Job.id.desc()).limit(500)
    if status is not None:
        stmt = stmt.where(Job.status == status)
    if level == "unknown":
        stmt = stmt.where(Job.level.is_(None))
    elif level is not None:
        stmt = stmt.where(Job.level == level)
    return [_job_out(db, j) for j in db.scalars(stmt)]


@router.get("/{job_id}", response_model=JobDetail)
def job_detail(job_id: int, db: Session = Depends(get_db)) -> Any:
    j = _get(db, job_id)
    runs = list(
        db.scalars(
            select(PipelineRun).where(PipelineRun.job_id == j.id).order_by(PipelineRun.id.desc())
        )
    )
    resumes = list(
        db.scalars(select(Resume).where(Resume.job_id == j.id).order_by(Resume.id.desc()))
    )
    apps = list(
        db.scalars(
            select(Application).where(Application.job_id == j.id).order_by(Application.id.desc())
        )
    )
    files = list(db.scalars(select(JobFile).where(JobFile.job_id == j.id).order_by(JobFile.id)))
    emails = list(
        db.scalars(
            select(SourceEmail).where(SourceEmail.job_id == j.id).order_by(SourceEmail.received_at)
        )
    )
    return _job_out(
        db,
        j,
        JobDetail,
        apply_mode=j.apply_mode.value if j.apply_mode else None,
        files=[
            FileOut(
                id=f.id,
                kind=f.kind.value,
                original_name=f.original_name,
                mime_type=f.mime_type,
                created_at=f.created_at,
            )
            for f in files
        ],
        source_emails=[
            SourceEmailOut(
                id=e.id,
                sender=e.sender,
                original_sender=e.original_sender,
                subject=e.subject,
                received_at=e.received_at,
                classification=e.classification.value,
                body_html=e.body_html,
                body_text=e.body_text,
                extracted_link=e.extracted_link,
                resolved_link=e.resolved_link,
                link_safe=e.link_safe,
                link_check_reason=e.link_check_reason,
            )
            for e in emails
        ],
        prompt=_prompt(db, j.id),
        jd_text=j.jd_text,
        jd_structured=j.jd_structured,
        eligibility=j.eligibility,
        apply_url=j.apply_url,
        runs=[_run_out(r) for r in runs],
        resumes=[resume_out(r) for r in resumes],
        applications=[
            ApplicationOut(
                id=a.id,
                resume_id=a.resume_id,
                status=a.status.value,
                auto_submit_at=a.auto_submit_at,
                approved_at=a.approved_at,
                submitted_at=a.submitted_at,
                error=a.error,
            )
            for a in apps
        ],
    )


@router.patch("/{job_id}", response_model=JobDetail)
def patch_job(job_id: int, body: JobPatch, db: Session = Depends(get_db)) -> Any:
    j = _get(db, job_id)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(j, k, v)
    if body.status is not None:
        record_status_outcome(db, j, body.status)  # outcome learning (Phase 9)
    audit(
        db,
        AuditActor.USER,
        "job.updated",
        entity_type="job",
        entity_id=j.id,
        details={k: str(v) for k, v in changes.items()},
    )
    db.commit()
    return job_detail(j.id, db)


@router.post("/{job_id}/regenerate", response_model=JobDetail, status_code=202)
def regenerate(job_id: int, body: RegenerateIn, db: Session = Depends(get_db)) -> Any:
    j = _get(db, job_id)
    busy = db.scalar(
        select(PipelineRun.id).where(
            PipelineRun.job_id == j.id, PipelineRun.state.in_([RunState.QUEUED, RunState.RUNNING])
        )
    )
    if busy:
        raise HTTPException(409, "a generation run is already in progress for this job")
    label_job(j)
    db.commit()
    queue_generation(db, j, page_limit=body.page_limit, force=body.force)
    return job_detail(j.id, db)


@router.post("/{job_id}/retry", response_model=JobDetail, status_code=202)
def retry(job_id: int, db: Session = Depends(get_db)) -> Any:
    """Re-queue the latest failed run on the same checkpoint: it resumes at the step that
    failed instead of starting over (no repeated JD analysis/retrieval)."""
    j = _get(db, job_id)
    run = db.scalar(
        select(PipelineRun).where(PipelineRun.job_id == j.id).order_by(PipelineRun.id.desc())
    )
    if run is None or run.state is not RunState.FAILED:
        raise HTTPException(409, "the latest run hasn't failed; use Regenerate for a fresh run")
    run.state, run.not_before, run.retries, run.finished_at = RunState.QUEUED, None, 0, None
    j.status_reason = None
    if j.status is JobStatus.FAILED:
        j.status = JobStatus.SCRAPED if j.jd_structured else JobStatus.DETECTED
    audit(db, AuditActor.USER, "job.retry", entity_type="job", entity_id=j.id)
    db.commit()
    return job_detail(j.id, db)


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: int, db: Session = Depends(get_db)) -> Response:
    j = _get(db, job_id)
    db.delete(j)
    audit(db, AuditActor.USER, "job.deleted", entity_type="job", entity_id=job_id)
    db.commit()
    return Response(status_code=204)


@router.post("/{job_id}/approve", response_model=JobDetail)
def approve(job_id: int, body: ApproveIn, db: Session = Depends(get_db)) -> Any:
    """Human-in-the-loop approval (hard rule 6): the portal agent (Phase 8) only submits
    applications in APPROVED state, using exactly the approved resume version."""
    j = _get(db, job_id)
    if body.resume_id is not None:
        resume = db.get(Resume, body.resume_id)
        if resume is None or resume.job_id != j.id:
            raise HTTPException(404, "that resume doesn't belong to this job")
    else:
        resume = db.scalar(
            select(Resume)
            .where(
                Resume.job_id == j.id,
                Resume.kind.in_([ResumeKind.GENERATED, ResumeKind.MANUAL_EDIT]),
            )
            .order_by(Resume.id.desc())
        )
        if resume is None:
            raise HTTPException(409, "no resume generated for this job yet")
    report = resume.build_report or {}
    if not report.get("within_limit", True):
        raise HTTPException(409, "this resume exceeds the page limit; it can't be approved")
    pending = db.scalar(
        select(Application).where(
            Application.job_id == j.id,
            Application.status.in_([ApplicationStatus.APPROVED, ApplicationStatus.SUBMITTED]),
        )
    )
    if pending is not None:
        raise HTTPException(409, f"already {pending.status.value}")
    prepared = db.scalar(
        select(Application).where(
            Application.job_id == j.id, Application.status == ApplicationStatus.PREPARED
        )
    )
    if prepared is not None:  # "Apply now" during the review window
        application = prepared
        application.status, application.approved_at = ApplicationStatus.APPROVED, datetime.now(UTC)
        application.resume_id, application.auto_submit_at = resume.id, None
    else:
        application = Application(
            job_id=j.id,
            resume_id=resume.id,
            status=ApplicationStatus.APPROVED,
            approved_at=datetime.now(UTC),
        )
        db.add(application)
    db.flush()
    audit(
        db,
        AuditActor.USER,
        "job.approved",
        entity_type="job",
        entity_id=j.id,
        details={"resume_id": resume.id},
    )
    db.commit()
    site = db.get(JobSite, j.job_site_id) if j.job_site_id else None
    search_only = site is not None and site.mode is SiteMode.SEARCH_ONLY
    if j.apply_url and j.portal_id and not search_only:
        queue_apply(db, j, application.id)  # the portal agent submits this exact version
    return job_detail(j.id, db)


@router.post("/{job_id}/allow-apply-site", response_model=JobDetail)
def allow_site(job_id: int, db: Session = Depends(get_db)) -> Any:
    """Your one-time OK to open a company careers site (not a known hiring system) found
    for this job. If you already approved the application, the agent applies now."""
    j = _get(db, job_id)
    try:
        portal = allow_apply_site(db, j)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    audit(
        db,
        AuditActor.USER,
        "job.apply_site_allowed",
        entity_type="job",
        entity_id=j.id,
        details={"portal_id": portal.id, "url": j.apply_url},
    )
    db.commit()
    _queue_if_approved(db, j)
    return job_detail(j.id, db)


def _queue_if_approved(db: Session, j: Job) -> None:
    approved = db.scalar(
        select(Application).where(
            Application.job_id == j.id, Application.status == ApplicationStatus.APPROVED
        )
    )
    if approved is not None and j.portal_id and j.apply_url:
        queue_apply(db, j, approved.id)


class ApplyLinkIn(BaseModel):
    url: str = Field(min_length=10, max_length=2000)


@router.post("/{job_id}/apply-link", response_model=JobDetail)
def set_link(job_id: int, body: ApplyLinkIn, db: Session = Depends(get_db)) -> Any:
    """You know the company's own application page: the agent applies there (after your
    approval). Job boards like LinkedIn are refused (the agent never applies on them)."""
    j = _get(db, job_id)
    try:
        portal = set_apply_link(db, j, body.url)
    except ValueError:
        raise HTTPException(
            422,
            "paste the company's own application page (https://...); job boards like "
            "LinkedIn or Naukri can't be used by the agent",
        ) from None
    audit(
        db,
        AuditActor.USER,
        "job.apply_link_set",
        entity_type="job",
        entity_id=j.id,
        details={"portal_id": portal.id, "url": j.apply_url},
    )
    db.commit()
    _queue_if_approved(db, j)
    return job_detail(j.id, db)


@router.post("/{job_id}/find-apply-page", response_model=JobDetail)
async def find_apply_page(job_id: int, db: Session = Depends(get_db)) -> Any:
    j = _get(db, job_id)
    db.commit()

    maker = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    def run() -> None:
        from app.llm import get_gateway

        with maker() as sdb:
            search_apply_page(sdb, job_id, make_searcher(sdb, get_gateway(), job_id=job_id))

    try:
        await run_in_threadpool(run)
    except SearchError as exc:
        raise HTTPException(409, str(exc)) from None
    db.expire_all()
    j = _get(db, job_id)
    _queue_if_approved(db, j)
    return job_detail(j.id, db)


@router.delete("/{job_id}/approval", response_model=JobDetail)
def withdraw_approval(job_id: int, db: Session = Depends(get_db)) -> Any:
    j = _get(db, job_id)
    app_ = db.scalar(
        select(Application).where(
            Application.job_id == j.id,
            Application.status.in_([ApplicationStatus.APPROVED, ApplicationStatus.PREPARED]),
        )
    )
    if app_ is None:
        raise HTTPException(409, "nothing to withdraw (not approved, or already submitted)")
    # also cancel a queued (not yet started) apply run for it
    for run in db.scalars(
        select(PipelineRun).where(
            PipelineRun.job_id == j.id,
            PipelineRun.kind == RunKind.APPLY,
            PipelineRun.state == RunState.QUEUED,
        )
    ):
        db.delete(run)
    db.delete(app_)
    j.status = JobStatus.RESUME_READY
    j.status_reason = "You chose not to apply."
    audit(db, AuditActor.USER, "job.approval_withdrawn", entity_type="job", entity_id=j.id)
    db.commit()
    return job_detail(j.id, db)


def _prompt(db: Session, job_id: int) -> PromptOut | None:
    import json

    row = db.get(Setting, PROMPT_KEY.format(job_id=job_id))
    if row is None or not row.secret_value:
        return None
    data = json.loads(row.secret_value)
    if data.get("answer"):
        return None
    return PromptOut(
        kind=data["kind"],
        question=data["question"],
        screenshot_file_id=data.get("screenshot_file_id"),
        asked_at=data["asked_at"],
    )


@router.post("/{job_id}/prompt", status_code=204)
def answer_prompt(job_id: int, body: PromptAnswer, db: Session = Depends(get_db)) -> Response:
    """The user's own OTP/CAPTCHA answer, relayed to the waiting portal agent."""
    import json

    row = db.get(Setting, PROMPT_KEY.format(job_id=job_id))
    if row is None or not row.secret_value:
        raise HTTPException(404, "the agent isn't waiting for anything on this job")
    data = json.loads(row.secret_value)
    data["answer"] = body.answer.strip()
    row.secret_value = json.dumps(data)
    audit(
        db,
        AuditActor.USER,
        "job.prompt_answered",
        entity_type="job",
        entity_id=job_id,
        details={"kind": data.get("kind")},
    )
    db.commit()
    return Response(status_code=204)


@router.get("/{job_id}/files/{file_id}")
def job_file(job_id: int, file_id: int, db: Session = Depends(get_db)) -> FileResponse:
    f = db.get(JobFile, file_id)
    if f is None or f.job_id != job_id:
        raise HTTPException(404, "file not found")
    root = Path(get_config().data_dir).resolve()
    path = (root / f.path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(
        path,
        media_type=f.mime_type or "application/octet-stream",
        filename=f.original_name or path.name,
    )
