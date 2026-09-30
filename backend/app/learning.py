"""Outcome learning: bullets used in resumes that got shortlisted/offers rank higher.

outcome_score(bullet) = Σ weight(latest outcome of each application using it) / (n + PRIOR)

The PRIOR shrinks scores toward 0 while evidence is thin, so a single lucky shortlist
doesn't dominate retrieval. Retrieval adds 0.10 × outcome_score to similarity.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Application, Bullet, Job, Outcome, Resume, ResumeBullet
from app.models.enums import ApplicationStatus, JobStatus, OutcomeKind, ResumeKind

WEIGHTS = {
    OutcomeKind.OFFER: 1.0,
    OutcomeKind.SHORTLISTED: 0.6,
    OutcomeKind.REJECTED: -0.3,
    OutcomeKind.NO_RESPONSE: -0.1,
}
PRIOR = 2.0
_STATUS_TO_OUTCOME = {
    JobStatus.SHORTLISTED: OutcomeKind.SHORTLISTED,
    JobStatus.REJECTED: OutcomeKind.REJECTED,
    JobStatus.OFFER: OutcomeKind.OFFER,
}


def link_resume_bullets(db: Session, resume: Resume, used_facts: list[str]) -> int:
    """Record which bullet-bank bullets a generated resume drew on (writer-cited ids)."""
    ids = {int(f[1:]) for f in used_facts if f.startswith("b") and f[1:].isdigit()}
    existing = set(db.scalars(select(Bullet.id).where(Bullet.id.in_(ids)))) if ids else set()
    for pos, bid in enumerate(sorted(existing)):
        db.merge(ResumeBullet(resume_id=resume.id, bullet_id=bid, position=pos))
    return len(existing)


def _application_for(db: Session, job: Job) -> Application:
    app_ = db.scalar(
        select(Application).where(Application.job_id == job.id).order_by(Application.id.desc())
    )
    if app_ is None:
        resume = db.scalar(
            select(Resume)
            .where(
                Resume.job_id == job.id,
                Resume.kind.in_([ResumeKind.GENERATED, ResumeKind.MANUAL_EDIT]),
            )
            .order_by(Resume.id.desc())
        )
        app_ = Application(
            job_id=job.id,
            resume_id=resume.id if resume else None,
            status=ApplicationStatus.SUBMITTED,
            submitted_at=datetime.now(UTC),
        )
        db.add(app_)
        db.flush()
    elif app_.status is not ApplicationStatus.SUBMITTED:
        # the user applied themselves (e.g. no portal link) and reports it
        app_.status, app_.submitted_at = (
            ApplicationStatus.SUBMITTED,
            app_.submitted_at or datetime.now(UTC),
        )
    return app_


def record_status_outcome(db: Session, job: Job, new_status: JobStatus) -> bool:
    """Called when the user changes a job's status. Returns True if learning changed."""
    if new_status is JobStatus.APPLIED:
        _application_for(db, job)
        return False
    kind = _STATUS_TO_OUTCOME.get(new_status)
    if kind is None:
        return False
    app_ = _application_for(db, job)
    latest = db.scalar(
        select(Outcome).where(Outcome.application_id == app_.id).order_by(Outcome.id.desc())
    )
    if latest is None or latest.kind is not kind:
        db.add(Outcome(application_id=app_.id, kind=kind))
        db.flush()
    recompute_outcome_scores(db)
    return True


def recompute_outcome_scores(db: Session) -> int:
    latest: dict[int, OutcomeKind] = {}
    for o in db.scalars(select(Outcome).order_by(Outcome.id)):
        latest[o.application_id] = o.kind  # later outcomes supersede earlier ones
    resume_weight: dict[int, list[float]] = defaultdict(list)
    for app_id, kind in latest.items():
        a = db.get(Application, app_id)
        if a is not None and a.resume_id is not None:
            resume_weight[a.resume_id].append(WEIGHTS[kind])
    totals: dict[int, list[float]] = defaultdict(list)
    for rb in db.scalars(select(ResumeBullet)):
        totals[rb.bullet_id] += resume_weight.get(rb.resume_id, [])
    changed = 0
    for b in db.scalars(select(Bullet)):
        ws = totals.get(b.id, [])
        score = round(sum(ws) / (len(ws) + PRIOR), 4) if ws else 0.0
        if b.outcome_score != score:
            b.outcome_score = score
            changed += 1
    return changed
