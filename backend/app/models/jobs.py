"""Portals, email ingestion, jobs, applications and outcomes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import EncryptedJSON, EncryptedText
from app.models.base import Base, TimestampMixin, str_enum, utcnow
from app.models.enums import (
    ApplicationStatus,
    ApplyMode,
    ConnectionStatus,
    EmailClassification,
    EmailProvider,
    JobFileKind,
    JobSource,
    JobStatus,
    OutcomeKind,
    PortalLoginMethod,
    RunKind,
    RunState,
    SiteMode,
)


class Portal(TimestampMixin, Base):
    __tablename__ = "portals"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    base_url: Mapped[str] = mapped_column(String(500))
    # Hostnames the agent may open for this portal (exact or "*.example.com").
    allowed_domains: Mapped[list[str]] = mapped_column(default=list)
    login_method: Mapped[PortalLoginMethod] = mapped_column(
        str_enum(PortalLoginMethod, "portal_login_method"), default=PortalLoginMethod.MANUAL
    )
    # {"username": ..., "password": ...} — encrypted at rest, never returned to the UI.
    credentials: Mapped[dict[str, Any] | None] = mapped_column(
        "encrypted_credentials", EncryptedJSON, default=None
    )
    # Name of the per-portal selector config file (backend/portal_configs/<name>.yaml).
    selectors_config: Mapped[str | None] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(default=True)


class PortalSkill(TimestampMixin, Base):
    """One learned way to do one step on a portal (reinforcement learning memory).

    `role` is the step ("apply.open", "apply.resume_input", "apply.submit",
    "apply.confirmation", "field", "checkbox"); `selector` is how to find the element;
    successes/failures are the bandit's Beta(successes+1, failures+1) evidence. Fields also
    remember the form label (`key`) and the profile path that fills it (`value_path`).
    """

    __tablename__ = "portal_skills"
    __table_args__ = (UniqueConstraint("portal_id", "role", "selector"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    portal_id: Mapped[int] = mapped_column(ForeignKey("portals.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(40))
    selector: Mapped[str] = mapped_column(String(500))
    key: Mapped[str | None] = mapped_column(String(200))
    value_path: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(
        String(20), default="heuristic"
    )  # config|heuristic|model|user
    successes: Mapped[float] = mapped_column(default=0.0)
    failures: Mapped[float] = mapped_column(default=0.0)
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)


class OutreachCompany(TimestampMixin, Base):
    """A company or startup found for cold outreach (by web search or added by you)."""

    __tablename__ = "outreach_companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(200), unique=True)
    website: Mapped[str] = mapped_column(String(500))
    location: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(20), default="search")  # search | manual
    source_query: Mapped[str | None] = mapped_column(String(500))
    # new | researched | no_email | queued | skipped | failed
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    summary: Mapped[dict[str, Any] | None] = mapped_column(default=None)
    # [{"address": ..., "source_url": ..., "kind": "careers|general|person"}]
    emails: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    researched_at: Mapped[datetime | None] = mapped_column(default=None)
    size: Mapped[str | None] = mapped_column(String(20))  # startup | mid-size | large
    # no email anywhere: the agent watches this careers page for matching openings
    careers_url: Mapped[str | None] = mapped_column(String(1000))
    watch_checked_at: Mapped[datetime | None] = mapped_column(default=None)
    seen_openings: Mapped[list[str]] = mapped_column(JSON, default=list)
    # a specific job you're emailing about (optional): resume + email are tailored to it
    jd_text: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(String(200))


class OutreachSource(TimestampMixin, Base):
    """A startup directory you added (e.g. bangalorestartupmap.com) to find companies in."""

    __tablename__ = "outreach_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    label: Mapped[str | None] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(default=True)
    last_read_at: Mapped[datetime | None] = mapped_column(default=None)
    entries_found: Mapped[int] = mapped_column(default=0)
    added_total: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)


class OutreachEmail(TimestampMixin, Base):
    """One cold email per company: drafted by the agent, sent only after your approval."""

    __tablename__ = "outreach_emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("outreach_companies.id", ondelete="CASCADE"), unique=True
    )
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    to_address: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    # resume_pending | drafting | ready | approved | sent | failed | skipped
    status: Mapped[str] = mapped_column(String(20), default="resume_pending", index=True)
    warnings: Mapped[list[str]] = mapped_column(default=list)
    approved_at: Mapped[datetime | None] = mapped_column(default=None)
    auto_send_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(default=None)
    provider_message_id: Mapped[str | None] = mapped_column(String(300))
    error: Mapped[str | None] = mapped_column(Text)


class EmailAccount(TimestampMixin, Base):
    __tablename__ = "email_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[EmailProvider] = mapped_column(str_enum(EmailProvider, "email_provider"))
    address: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    oauth_tokens: Mapped[dict[str, Any] | None] = mapped_column(
        "encrypted_oauth_tokens", EncryptedJSON, default=None
    )
    imap_host: Mapped[str | None] = mapped_column(String(255))
    imap_port: Mapped[int | None]
    imap_password: Mapped[str | None] = mapped_column(
        "encrypted_imap_password", EncryptedText, default=None
    )
    status: Mapped[ConnectionStatus] = mapped_column(
        str_enum(ConnectionStatus, "connection_status"), default=ConnectionStatus.PENDING
    )
    # Provider-specific incremental sync cursor (Graph deltaLink, Gmail historyId, IMAP UID).
    sync_cursor: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)

    sender_rules: Mapped[list[SenderRule]] = relationship(
        back_populates="email_account", cascade="all, delete-orphan"
    )


class SenderRule(TimestampMixin, Base):
    __tablename__ = "sender_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_account_id: Mapped[int] = mapped_column(
        ForeignKey("email_accounts.id", ondelete="CASCADE"), index=True
    )
    # Full address ("jobs@havlock.in") or domain ("@havlock.in" / "havlock.in").
    sender_match: Mapped[str] = mapped_column(String(320))
    portal_id: Mapped[int | None] = mapped_column(ForeignKey("portals.id", ondelete="SET NULL"))
    apply_mode: Mapped[ApplyMode] = mapped_column(
        str_enum(ApplyMode, "apply_mode"), default=ApplyMode.DIRECT_LINK
    )
    delay_minutes: Mapped[int | None]  # None → global default
    # Also match mail auto-forwarded from another inbox, using the recovered original sender.
    match_forwarded: Mapped[bool] = mapped_column(default=True)
    enabled: Mapped[bool] = mapped_column(default=True)

    email_account: Mapped[EmailAccount] = relationship(back_populates="sender_rules")
    portal: Mapped[Portal | None] = relationship()


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[JobSource] = mapped_column(str_enum(JobSource, "job_source"))
    status: Mapped[JobStatus] = mapped_column(
        str_enum(JobStatus, "job_status"), default=JobStatus.DETECTED, index=True
    )
    # Why the job is suspicious / ineligible / failed / paused — shown in the dashboard.
    status_reason: Mapped[str | None] = mapped_column(Text)

    company: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[str | None] = mapped_column(String(255))
    portal_id: Mapped[int | None] = mapped_column(ForeignKey("portals.id", ondelete="SET NULL"))
    job_site_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_sites.id", ondelete="SET NULL"), index=True
    )
    apply_mode: Mapped[ApplyMode | None] = mapped_column(str_enum(ApplyMode, "apply_mode"))
    apply_url: Mapped[str | None] = mapped_column(Text)
    portal_job_ref: Mapped[str | None] = mapped_column(String(255))
    # Stable identity used to merge duplicates (both inboxes, reminders, extensions).
    dedupe_key: Mapped[str | None] = mapped_column(String(255), unique=True)

    jd_text: Mapped[str | None] = mapped_column(Text)
    jd_structured: Mapped[dict[str, Any] | None]
    eligibility: Mapped[dict[str, Any] | None]
    ctc: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    deadline: Mapped[datetime | None]
    required_documents: Mapped[list[str] | None]

    detected_at: Mapped[datetime] = mapped_column(default=utcnow)
    delay_minutes: Mapped[int | None]  # per-job override
    scheduled_at: Mapped[datetime | None] = mapped_column(index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    # fresher | experienced | None (not stated), from the title + JD (app.generation.level)
    level: Mapped[str | None] = mapped_column(String(20), index=True)
    level_reason: Mapped[str | None] = mapped_column(String(200))

    portal: Mapped[Portal | None] = relationship()
    source_emails: Mapped[list[SourceEmail]] = relationship(back_populates="job")
    files: Mapped[list[JobFile]] = relationship(back_populates="job", cascade="all, delete-orphan")
    applications: Mapped[list[Application]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class SourceEmail(Base):
    __tablename__ = "source_emails"
    __table_args__ = (UniqueConstraint("account_id", "message_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("email_accounts.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[str] = mapped_column(String(512))  # provider-specific id
    # RFC 5322 Message-ID: identical across inboxes, used for cross-account dedupe.
    internet_message_id: Mapped[str | None] = mapped_column(String(998), index=True)
    sender: Mapped[str] = mapped_column(String(320))
    original_sender: Mapped[str | None] = mapped_column(String(320))  # for forwarded mail
    subject: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime]
    body_html: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)
    headers: Mapped[dict[str, Any] | None]
    classification: Mapped[EmailClassification] = mapped_column(
        str_enum(EmailClassification, "email_classification"),
        default=EmailClassification.UNCLASSIFIED,
    )
    extracted_link: Mapped[str | None] = mapped_column(Text)
    resolved_link: Mapped[str | None] = mapped_column(Text)
    link_safe: Mapped[bool | None]
    link_check_reason: Mapped[str | None] = mapped_column(Text)
    sender_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("sender_rules.id", ondelete="SET NULL")
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    job: Mapped[Job | None] = relationship(back_populates="source_emails")


class JobFile(Base):
    __tablename__ = "job_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[JobFileKind] = mapped_column(str_enum(JobFileKind, "job_file_kind"))
    path: Mapped[str] = mapped_column(Text)  # relative to DATA_DIR
    original_name: Mapped[str | None] = mapped_column(String(500))
    mime_type: Mapped[str | None] = mapped_column(String(120))
    sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    job: Mapped[Job] = relationship(back_populates="files")


class Application(TimestampMixin, Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    resume_id: Mapped[int | None] = mapped_column(ForeignKey("resumes.id", ondelete="SET NULL"))
    status: Mapped[ApplicationStatus] = mapped_column(
        str_enum(ApplicationStatus, "application_status"), default=ApplicationStatus.PREPARED
    )
    approved_at: Mapped[datetime | None]
    # Review window: a PREPARED application is approved automatically at this time unless
    # the user clicks "Don't apply" first ("Apply now" approves it immediately).
    auto_submit_at: Mapped[datetime | None] = mapped_column(index=True)
    submitted_at: Mapped[datetime | None]
    confirmation_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_files.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="applications")
    outcomes: Mapped[list[Outcome]] = relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class Outcome(Base):
    __tablename__ = "outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[OutcomeKind] = mapped_column(str_enum(OutcomeKind, "outcome_kind"))
    recorded_at: Mapped[datetime] = mapped_column(default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text)

    application: Mapped[Application] = relationship(back_populates="outcomes")


Index("ix_jobs_status_scheduled", Job.status, Job.scheduled_at)


class PipelineRun(Base):
    """One execution of a pipeline for a job (queued by the API, run by the worker).
    LangGraph checkpoints are keyed by `thread_id`, so a crashed run resumes."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[RunKind] = mapped_column(str_enum(RunKind, "run_kind"))
    state: Mapped[RunState] = mapped_column(
        str_enum(RunState, "run_state"), default=RunState.QUEUED, index=True
    )
    thread_id: Mapped[str] = mapped_column(String(80))
    options: Mapped[dict[str, Any]] = mapped_column(default=dict)
    step: Mapped[str | None] = mapped_column(String(60))  # current graph node, for the UI
    # Transient failures (provider overloaded, rate limits) re-queue the run: it resumes
    # from its checkpoint at `not_before`. `retries` counts those automatic retries.
    not_before: Mapped[datetime | None] = mapped_column(index=True)
    retries: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class JobSite(TimestampMixin, Base):
    """A job website the agent searches (Havlock, LinkedIn, a company careers page...).

    Stage 1 (searching): open each search URL, collect postings, keep those matching the
    categories. Stage 2 (applying): each match becomes a Job that goes through
    scrape → resume → approval → apply, like jobs from email.
    """

    __tablename__ = "job_sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    portal_id: Mapped[int] = mapped_column(ForeignKey("portals.id", ondelete="CASCADE"))
    search_urls: Mapped[list[str]] = mapped_column(default=list)
    categories: Mapped[list[str]] = mapped_column(default=list)  # roles the user wants
    exclude_keywords: Mapped[list[str]] = mapped_column(default=list)
    mode: Mapped[SiteMode] = mapped_column(
        str_enum(SiteMode, "site_mode"), default=SiteMode.SEARCH_ONLY
    )
    check_every_minutes: Mapped[int] = mapped_column(default=120)
    # Review window after the resume is ready: the agent applies when it ends unless the
    # user clicks Don't apply (Apply now skips the wait). 0 = apply as soon as it's ready.
    apply_delay_minutes: Mapped[int] = mapped_column(default=0)
    max_new_per_check: Mapped[int] = mapped_column(default=5)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_checked_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)

    portal: Mapped[Portal] = relationship()


class DiscoveredPosting(Base):
    """Every posting seen while searching, including skipped ones (with the reason)."""

    __tablename__ = "discovered_postings"
    __table_args__ = (UniqueConstraint("job_site_id", "url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job_site_id: Mapped[int] = mapped_column(
        ForeignKey("job_sites.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(2000))
    title: Mapped[str | None] = mapped_column(String(500))
    company: Mapped[str | None] = mapped_column(String(255))
    snippet: Mapped[str | None] = mapped_column(Text)
    matched: Mapped[bool] = mapped_column(default=False)
    matched_category: Mapped[str | None] = mapped_column(String(200))
    skip_reason: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    discovered_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
