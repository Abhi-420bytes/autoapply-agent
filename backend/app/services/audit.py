from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog
from app.models.enums import AuditActor
from app.security.redaction import redact_mapping


def audit(
    db: Session,
    actor: AuditActor,
    action: str,
    *,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Record an audit event in the caller's transaction. Details are always redacted."""
    entry = AuditLog(
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        details=redact_mapping(details) if details else None,
    )
    db.add(entry)
    return entry
