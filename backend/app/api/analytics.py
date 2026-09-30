from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Application, Bullet, Job, LLMUsage, Outcome, Resume
from app.models.enums import ApplicationStatus, OutcomeKind, ResumeKind
from app.services.settings_service import get_app_settings

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
WEEKS = 12
MONTHS = 6


class WeekPoint(BaseModel):
    week_start: date
    applications: int
    generated: int


class MonthCost(BaseModel):
    month: str
    cost_usd: float
    calls: int


class JobCost(BaseModel):
    job_id: int
    label: str
    cost_usd: float


class GapCount(BaseModel):
    skill: str
    jobs: int


class LearnedBullet(BaseModel):
    id: int
    text: str
    outcome_score: float


class Analytics(BaseModel):
    weekly: list[WeekPoint]
    funnel: dict[str, int]
    applications_submitted: int
    with_outcome: int
    shortlisted_or_offer: int
    shortlist_rate: float | None
    avg_ats_score: float | None
    ats_scores: list[float]
    top_gaps: list[GapCount]
    monthly_cost: list[MonthCost]
    cost_per_job: list[JobCost]
    avg_cost_per_resume: float | None
    top_bullets: list[LearnedBullet]


def _as_utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


@router.get("", response_model=Analytics)
def analytics(db: Session = Depends(get_db)) -> Analytics:
    tz = ZoneInfo(get_app_settings(db).timezone)
    today = datetime.now(tz).date()
    first_week = today - timedelta(days=today.weekday()) - timedelta(weeks=WEEKS - 1)
    weeks = {first_week + timedelta(weeks=i): [0, 0] for i in range(WEEKS)}

    def week_of(d: datetime) -> date:
        local = _as_utc(d).astimezone(tz).date()
        return local - timedelta(days=local.weekday())

    submitted = list(
        db.scalars(select(Application).where(Application.status == ApplicationStatus.SUBMITTED))
    )
    for a in submitted:
        w = week_of(a.submitted_at or a.created_at)
        if w in weeks:
            weeks[w][0] += 1

    latest_per_job: dict[int, Resume] = {}
    for r in db.scalars(
        select(Resume).where(Resume.kind == ResumeKind.GENERATED).order_by(Resume.id)
    ):
        w = week_of(r.created_at)
        if w in weeks:
            weeks[w][1] += 1
        if r.job_id is not None:
            latest_per_job[r.job_id] = r

    latest_outcome: dict[int, OutcomeKind] = {}
    for o in db.scalars(select(Outcome).order_by(Outcome.id)):
        latest_outcome[o.application_id] = o.kind
    positive = sum(
        1 for k in latest_outcome.values() if k in (OutcomeKind.SHORTLISTED, OutcomeKind.OFFER)
    )

    scores = [r.ats_score for r in latest_per_job.values() if r.ats_score is not None]
    gaps: Counter[str] = Counter()
    for r in latest_per_job.values():
        for g in set((r.score_report or {}).get("gaps", [])):
            gaps[g] += 1

    months: dict[str, list[float]] = {}
    for back in range(MONTHS - 1, -1, -1):  # calendar months, oldest first, ending this month
        y, m = divmod(today.year * 12 + today.month - 1 - back, 12)
        months[f"{y:04d}-{m + 1:02d}"] = [0.0, 0]
    for created, cost in db.execute(select(LLMUsage.created_at, LLMUsage.cost_usd)):
        key = _as_utc(created).astimezone(tz).strftime("%Y-%m")
        if key in months:
            months[key][0] += float(cost)
            months[key][1] += 1

    per_job = db.execute(
        select(LLMUsage.job_id, func.sum(LLMUsage.cost_usd))
        .where(LLMUsage.job_id.is_not(None))
        .group_by(LLMUsage.job_id)
        .order_by(func.sum(LLMUsage.cost_usd).desc())
        .limit(10)
    ).all()
    job_costs = []
    for job_id, cost in per_job:
        j = db.get(Job, job_id)
        label = f"{j.company or 'Unknown'} – {j.role or ''}".strip(" –") if j else f"job #{job_id}"
        job_costs.append(JobCost(job_id=job_id, label=label, cost_usd=round(float(cost), 6)))
    resume_costs = [float(r.cost_usd) for r in latest_per_job.values() if r.cost_usd is not None]

    funnel: dict[str, Any] = {
        k.value: v
        for k, v in db.execute(select(Job.status, func.count()).group_by(Job.status)).tuples()
    }
    top = db.scalars(
        select(Bullet)
        .where(Bullet.outcome_score != 0)
        .order_by(Bullet.outcome_score.desc())
        .limit(5)
    )
    return Analytics(
        weekly=[
            WeekPoint(week_start=w, applications=v[0], generated=v[1])
            for w, v in sorted(weeks.items())
        ],
        funnel=funnel,
        applications_submitted=len(submitted),
        with_outcome=len(latest_outcome),
        shortlisted_or_offer=positive,
        shortlist_rate=round(positive / len(submitted), 3) if submitted else None,
        avg_ats_score=round(sum(scores) / len(scores), 1) if scores else None,
        ats_scores=[round(s, 1) for s in scores],
        top_gaps=[GapCount(skill=k, jobs=v) for k, v in gaps.most_common(10)],
        monthly_cost=[
            MonthCost(month=k, cost_usd=round(v[0], 6), calls=int(v[1])) for k, v in months.items()
        ],
        cost_per_job=job_costs,
        avg_cost_per_resume=round(sum(resume_costs) / len(resume_costs), 6)
        if resume_costs
        else None,
        top_bullets=[
            LearnedBullet(id=b.id, text=b.text, outcome_score=b.outcome_score) for b in top
        ],
    )
