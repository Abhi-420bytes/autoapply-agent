"""Resumes (versioned), bullet bank, GitHub repos and embeddings for RAG."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import VectorType
from app.models.base import Base, TimestampMixin, str_enum, utcnow
from app.models.enums import BulletSource, ResumeKind


class Resume(Base):
    """Immutable resume versions. Editing creates a new row with `parent_id` set."""

    __tablename__ = "resumes"
    __table_args__ = (UniqueConstraint("lineage_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[ResumeKind] = mapped_column(str_enum(ResumeKind, "resume_kind"))
    # All versions of one resume share a lineage id (= id of the first version).
    lineage_id: Mapped[int | None] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(default=1)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    label: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=False)  # the active base template

    tex_source: Mapped[str] = mapped_column(Text)
    tex_path: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(Text)
    page_limit: Mapped[int | None]
    page_count: Mapped[int | None]
    ats_score: Mapped[float | None]
    score_report: Mapped[dict[str, Any] | None]
    models_used: Mapped[list[Any] | None]
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    # Templates: main file name, asset files, regions, bullet macro, Overleaf fidelity check.
    meta: Mapped[dict[str, Any] | None]
    # Last compile: page count vs limit, overfull boxes, removed bullets, errors, timing.
    build_report: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    bullets: Mapped[list[ResumeBullet]] = relationship(
        back_populates="resume", cascade="all, delete-orphan"
    )


class GitHubRepo(TimestampMixin, Base):
    __tablename__ = "github_repos"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), unique=True)
    url: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    readme: Mapped[str | None] = mapped_column(Text)
    languages: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"Python": bytes}
    topics: Mapped[list[str]] = mapped_column(default=list)
    stars: Mapped[int] = mapped_column(default=0)
    forks: Mapped[int] = mapped_column(default=0)
    is_fork: Mapped[bool] = mapped_column(default=False)
    is_private: Mapped[bool] = mapped_column(default=False)
    archived: Mapped[bool] = mapped_column(default=False)
    # Hash of the inputs to the summary; the LLM is only re-run when it changes.
    source_hash: Mapped[str | None] = mapped_column(String(64))
    sync_error: Mapped[str | None] = mapped_column(Text)
    commit_count: Mapped[int | None]  # commits authored by the user
    pushed_at: Mapped[datetime | None]
    # LLM summary: {problem, tech_stack, role, outcomes} — outcomes only if in the data.
    summary: Mapped[dict[str, Any] | None]
    synced_at: Mapped[datetime | None]


class Bullet(Base):
    """One entry in the bullet bank. Every bullet traces back to a real source."""

    __tablename__ = "bullets"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(String(120))  # Experience, Projects, ...
    heading: Mapped[str | None] = mapped_column(String(255))  # project / company name
    skills: Mapped[list[str]] = mapped_column(default=list)
    source_type: Mapped[BulletSource] = mapped_column(str_enum(BulletSource, "bullet_source"))
    source_resume_id: Mapped[int | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL")
    )
    github_repo_id: Mapped[int | None] = mapped_column(
        ForeignKey("github_repos.id", ondelete="CASCADE")
    )
    source_date: Mapped[date | None]
    # Normalized-text hash for exact dedupe; near-duplicates are merged via embeddings.
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Learned from outcomes (Phase 9): boosts bullets used in successful resumes.
    outcome_score: Mapped[float] = mapped_column(default=0.0)
    # The user can hide a bullet from generation without deleting it.
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ResumeBullet(Base):
    """Which bullets went into which resume — the link used for outcome learning."""

    __tablename__ = "resume_bullets"

    resume_id: Mapped[int] = mapped_column(
        ForeignKey("resumes.id", ondelete="CASCADE"), primary_key=True
    )
    bullet_id: Mapped[int] = mapped_column(
        ForeignKey("bullets.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(default=0)
    rendered_text: Mapped[str | None] = mapped_column(Text)  # the rewritten form actually used

    resume: Mapped[Resume] = relationship(back_populates="bullets")
    bullet: Mapped[Bullet] = relationship()


class Embedding(Base):
    """A vector for one chunk of a knowledge item.

    `model_name` + `dimension` are stored per row and every similarity query filters on
    the active model, so vectors from different models are never compared.
    """

    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("owner_type", "owner_id", "chunk_index", "model_name"),
        Index("ix_embeddings_model_owner", "model_name", "owner_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_type: Mapped[str] = mapped_column(String(40))  # "bullet" | "github_repo" | ...
    owner_id: Mapped[int]
    chunk_index: Mapped[int] = mapped_column(default=0)
    content: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(200))
    dimension: Mapped[int]
    vector: Mapped[list[float]] = mapped_column(VectorType)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
