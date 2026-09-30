from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Notification
from app.models.enums import NotificationLevel

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: int
    level: NotificationLevel
    kind: str
    title: str
    body: str | None
    job_id: int | None
    read_at: datetime | None
    created_at: datetime


def _out(n: Notification) -> NotificationOut:
    return NotificationOut(
        id=n.id,
        level=n.level,
        kind=n.kind,
        title=n.title,
        body=n.body,
        job_id=n.job_id,
        read_at=n.read_at,
        created_at=n.created_at,
    )


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    unread_only: bool = False, limit: int = 50, db: Session = Depends(get_db)
) -> list[NotificationOut]:
    stmt = select(Notification).order_by(Notification.id.desc()).limit(min(max(limit, 1), 200))
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return [_out(n) for n in db.scalars(stmt)]


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(notification_id: int, db: Session = Depends(get_db)) -> Response:
    n = db.get(Notification, notification_id)
    if n is None:
        raise HTTPException(404, "notification not found")
    n.read_at = n.read_at or datetime.now(UTC)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(db: Session = Depends(get_db)) -> Response:
    db.execute(
        update(Notification).where(Notification.read_at.is_(None)).values(read_at=datetime.now(UTC))
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
