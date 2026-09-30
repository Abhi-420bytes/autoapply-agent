"""Cost accounting queries: month-to-date spend (budget) and usage summaries."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.models import LLMUsage


def month_start(tz_name: str, now: datetime | None = None) -> datetime:
    """Start of the current calendar month in the user's timezone, as a UTC instant."""
    tz = ZoneInfo(tz_name)
    local = (now or datetime.now(UTC)).astimezone(tz)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def spend_since(db: Session, since: datetime) -> Decimal:
    total = db.scalar(
        select(func.coalesce(func.sum(LLMUsage.cost_usd), 0)).where(LLMUsage.created_at >= since)
    )
    return Decimal(str(total or 0))


class UsageRow(BaseModel):
    key: str
    calls: int
    failures: int
    tokens_in: int
    tokens_out: int
    cost_usd: float


class RecentCall(BaseModel):
    id: int
    created_at: datetime
    task: str | None
    provider_kind: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int | None
    success: bool
    is_fallback: bool
    error_kind: str | None
    job_id: int | None


class UsageSummary(BaseModel):
    period_start: datetime
    month_to_date_usd: float
    budget_usd: float | None
    budget_exceeded: bool
    by_task: list[UsageRow]
    by_model: list[UsageRow]
    by_job: list[UsageRow]
    recent: list[RecentCall]


def _grouped(
    db: Session, since: datetime, column: InstrumentedAttribute[Any], label: str
) -> list[UsageRow]:
    stmt = (
        select(
            column,
            func.count(LLMUsage.id),
            func.sum(case((LLMUsage.success.is_(False), 1), else_=0)),
            func.coalesce(func.sum(LLMUsage.tokens_in), 0),
            func.coalesce(func.sum(LLMUsage.tokens_out), 0),
            func.coalesce(func.sum(LLMUsage.cost_usd), 0),
        )
        .where(LLMUsage.created_at >= since)
        .group_by(column)
        .order_by(func.sum(LLMUsage.cost_usd).desc())
    )
    rows = []
    for key, calls, failures, tin, tout, cost in db.execute(stmt).all():
        rows.append(
            UsageRow(
                key=str(key) if key is not None else label,
                calls=calls,
                failures=int(failures or 0),
                tokens_in=int(tin),
                tokens_out=int(tout),
                cost_usd=float(cost),
            )
        )
    return rows


def usage_summary(
    db: Session, tz_name: str, budget_usd: float | None, recent_limit: int = 50
) -> UsageSummary:
    since = month_start(tz_name)
    mtd = spend_since(db, since)
    recent = db.scalars(select(LLMUsage).order_by(LLMUsage.id.desc()).limit(recent_limit)).all()
    return UsageSummary(
        period_start=since,
        month_to_date_usd=float(mtd),
        budget_usd=budget_usd,
        budget_exceeded=budget_usd is not None and mtd >= Decimal(str(budget_usd)),
        by_task=_grouped(db, since, LLMUsage.task, "connection_test"),
        by_model=_grouped(db, since, LLMUsage.model, "?"),
        by_job=[r for r in _grouped(db, since, LLMUsage.job_id, "none") if r.key != "none"],
        recent=[
            RecentCall(
                id=u.id,
                created_at=u.created_at,
                task=u.task.value if u.task else None,
                provider_kind=u.provider_kind.value,
                model=u.model,
                tokens_in=u.tokens_in,
                tokens_out=u.tokens_out,
                cost_usd=float(u.cost_usd),
                latency_ms=u.latency_ms,
                success=u.success,
                is_fallback=u.is_fallback,
                error_kind=u.error_kind,
                job_id=u.job_id,
            )
            for u in recent
        ],
    )
