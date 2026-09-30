from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Notification
from app.models.enums import NotificationLevel

log = logging.getLogger(__name__)


def notify(
    db: Session,
    level: NotificationLevel,
    kind: str,
    title: str,
    body: str | None = None,
    *,
    job_id: int | None = None,
    dedupe_key: str | None = None,
) -> Notification | None:
    """Create a notification and commit. Returns None if `dedupe_key` was already used."""
    if dedupe_key and db.scalar(
        select(Notification.id).where(Notification.dedupe_key == dedupe_key)
    ):
        return None
    n = Notification(
        level=level, kind=kind, title=title, body=body, job_id=job_id, dedupe_key=dedupe_key
    )
    db.add(n)
    try:
        db.commit()
    except IntegrityError:  # raced with another process creating the same alert
        db.rollback()
        return None
    log.log(
        logging.WARNING if level is not NotificationLevel.INFO else logging.INFO,
        "notification [%s] %s",
        kind,
        title,
    )
    return n
