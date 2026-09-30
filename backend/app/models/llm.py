"""LLM provider credentials, per-task model routing, and usage/cost accounting."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import EncryptedText
from app.models.base import Base, TimestampMixin, str_enum, utcnow
from app.models.enums import LLMProviderKind, LLMTask


class LLMProvider(TimestampMixin, Base):
    __tablename__ = "llm_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(120))  # e.g. "Anthropic (personal)"
    kind: Mapped[LLMProviderKind] = mapped_column(str_enum(LLMProviderKind, "llm_provider_kind"))
    # Plaintext only in memory; the column holds Fernet ciphertext. Nullable for Ollama.
    api_key: Mapped[str | None] = mapped_column("encrypted_api_key", EncryptedText, default=None)
    base_url: Mapped[str | None] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(default=True)
    last_test_ok: Mapped[bool | None]
    last_test_at: Mapped[datetime | None]
    last_test_latency_ms: Mapped[int | None]
    last_test_error: Mapped[str | None] = mapped_column(Text)


class LLMTaskConfig(TimestampMixin, Base):
    __tablename__ = "llm_task_config"

    task: Mapped[LLMTask] = mapped_column(str_enum(LLMTask, "llm_task"), primary_key=True)
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_providers.id", ondelete="SET NULL")
    )
    model: Mapped[str | None] = mapped_column(String(200))
    fallback_provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_providers.id", ondelete="SET NULL")
    )
    fallback_model: Mapped[str | None] = mapped_column(String(200))
    # temperature, max_tokens, timeout_s, ... — validated by the gateway (Phase 2).
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)

    provider: Mapped[LLMProvider | None] = relationship(foreign_keys=[provider_id])
    fallback_provider: Mapped[LLMProvider | None] = relationship(
        foreign_keys=[fallback_provider_id]
    )


class LLMUsage(Base):
    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    # NULL for calls outside a task (connection tests).
    task: Mapped[LLMTask | None] = mapped_column(str_enum(LLMTask, "llm_task"))
    provider_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_providers.id", ondelete="SET NULL")
    )
    prompt_ref: Mapped[str | None] = mapped_column(String(120))  # e.g. "jd_analyzer.v2"
    provider_kind: Mapped[LLMProviderKind] = mapped_column(
        str_enum(LLMProviderKind, "llm_provider_kind")
    )
    model: Mapped[str] = mapped_column(String(200))
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    latency_ms: Mapped[int | None]
    success: Mapped[bool] = mapped_column(default=True)
    is_fallback: Mapped[bool] = mapped_column(default=False)
    attempt: Mapped[int] = mapped_column(default=1)
    error_kind: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)  # redacted
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
