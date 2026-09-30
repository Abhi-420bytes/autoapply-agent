"""Key/value settings (with optional encrypted values) and the audit log."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.types import EncryptedText
from app.models.base import Base, str_enum, utcnow
from app.models.enums import AuditActor, NotificationLevel


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    # Exactly one of these is used, depending on whether the setting is a secret.
    value: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    secret_value: Mapped[str | None] = mapped_column("encrypted_value", EncryptedText, default=None)
    is_secret: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[AuditActor] = mapped_column(str_enum(AuditActor, "audit_actor"))
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    details: Mapped[dict[str, Any] | None]  # always passed through redact_mapping
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class Notification(Base):
    """Something the user should see: suspicious link, OTP needed, budget hit, failure."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[NotificationLevel] = mapped_column(
        str_enum(NotificationLevel, "notification_level")
    )
    kind: Mapped[str] = mapped_column(String(60), index=True)  # e.g. "budget.exceeded"
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    # Prevents repeats of the same alert (e.g. one budget alert per month).
    dedupe_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    read_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
