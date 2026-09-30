from __future__ import annotations

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl, field_validator

OutreachKind = Literal["startups", "companies"]
DEFAULT_CLOSING_NOTE = (
    "This email was researched, written and sent by AutoApply Agent, an open-source agentic "
    "AI system created by Abhiram. It finds companies, reads their websites, tailors my resume "
    "and writes emails like this one."
)
JobLevelChoice = Literal["fresher", "experienced", "unknown"]
CompanySize = Literal["startup", "mid-size", "large", "unknown"]


class AppSettings(BaseModel):
    """User-editable global settings. Defaults here are the source of truth."""

    model_config = ConfigDict(extra="forbid")

    auto_apply: bool = Field(
        default=False,
        description="When off, the agent prepares everything and waits for approval.",
    )
    page_limit: int = Field(default=1, ge=1, le=3)
    ats_threshold: int = Field(default=80, ge=0, le=100)
    max_quality_iterations: int = Field(default=4, ge=1, le=4)
    global_delay_minutes: int = Field(default=60, ge=0, le=7 * 24 * 60)
    email_poll_minutes: int = Field(default=5, ge=1, le=240)
    github_username: str | None = Field(default=None, max_length=39, pattern=r"^[A-Za-z0-9-]+$")
    github_include_private: bool = False  # private repos may hold confidential work
    github_include_forks: bool = False  # forks count only if you committed to them
    github_exclude_repos: list[str] = Field(default_factory=list, max_length=200)
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    timezone: str = "Asia/Kolkata"
    # Add the one-line "AutoApply Agent" project to generated resumes. Only for its creator
    # or contributors: a resume must never claim work you didn't do.
    include_signature_project: bool = False
    # File name of your resume PDF everywhere it's sent: uploads, email attachments, downloads
    resume_file_name: str = Field(default="", max_length=80)
    # Job-board alert postings whose title contains one of these words are skipped.
    skip_title_words: list[str] = Field(
        default_factory=lambda: [
            "senior",
            "sr",
            "lead",
            "principal",
            "staff",
            "manager",
            "architect",
            "experienced",
            "sse",
            "tl",
            "head",
            "director",
        ],
        max_length=100,
    )
    # Which job levels the agent prepares resumes for (others are set aside, no LLM spent).
    # "unknown" = the posting states no experience level.
    target_levels: list[JobLevelChoice] = Field(
        default_factory=lambda: list[JobLevelChoice](["fresher", "unknown"])
    )
    # Cold-email outreach (every email waits for your Send)
    outreach_enabled: bool = False
    outreach_locations: list[str] = Field(default_factory=list, max_length=20)
    outreach_roles: list[str] = Field(default_factory=list, max_length=20)
    outreach_company_kinds: list[OutreachKind] = Field(
        default_factory=lambda: list[OutreachKind](["startups", "companies"])
    )
    outreach_daily_cap: int = Field(default=10, ge=1, le=50)
    outreach_new_per_search: int = Field(default=5, ge=1, le=20)
    outreach_search_every_hours: int = Field(default=24, ge=1, le=24 * 14)
    outreach_account_id: int | None = None  # the mailbox that sends (needs send permission)
    # Company sizes to email ("unknown" = the site doesn't say); large enterprises are skipped
    outreach_company_sizes: list[CompanySize] = Field(
        default_factory=lambda: list[CompanySize](["startup", "mid-size", "unknown"])
    )
    # Send drafts automatically after this review window (drafts with warnings still wait)
    outreach_auto_send: bool = False
    outreach_send_delay_minutes: int = Field(default=30, ge=0, le=24 * 60)
    # Closing note of every cold email (must name AutoApply Agent; {name} = your name)
    # empty = no closing note
    outreach_closing_note: str = Field(default=DEFAULT_CLOSING_NOTE, max_length=600)
    # companies without any email: re-check their careers page this often for openings
    outreach_watch_hours: int = Field(default=24, ge=1, le=24 * 14)

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v

    @field_validator("outreach_closing_note")
    @classmethod
    def _keeps_disclosure(cls, v: str) -> str:
        if v.strip() and "autoapply agent" not in v.lower():
            raise ValueError(
                "the closing note must say the email was sent by AutoApply Agent (your agent)"
            )
        return v.strip()


class AppSettingsPatch(BaseModel):
    """Partial update; only provided fields are changed."""

    model_config = ConfigDict(extra="forbid")

    auto_apply: bool | None = None
    page_limit: int | None = None
    ats_threshold: int | None = None
    max_quality_iterations: int | None = None
    global_delay_minutes: int | None = None
    email_poll_minutes: int | None = None
    github_username: str | None = None
    github_include_private: bool | None = None
    github_include_forks: bool | None = None
    github_exclude_repos: list[str] | None = None
    monthly_budget_usd: float | None = None
    timezone: str | None = None
    resume_file_name: str | None = None
    include_signature_project: bool | None = None
    skip_title_words: list[str] | None = None
    target_levels: list[JobLevelChoice] | None = None
    outreach_enabled: bool | None = None
    outreach_locations: list[str] | None = None
    outreach_roles: list[str] | None = None
    outreach_company_kinds: list[OutreachKind] | None = None
    outreach_daily_cap: int | None = None
    outreach_new_per_search: int | None = None
    outreach_search_every_hours: int | None = None
    outreach_account_id: int | None = None
    outreach_company_sizes: list[CompanySize] | None = None
    outreach_auto_send: bool | None = None
    outreach_send_delay_minutes: int | None = None
    outreach_watch_hours: int | None = None
    outreach_closing_note: str | None = None


class Education(BaseModel):
    institution: str
    degree: str | None = None
    branch: str | None = None
    cgpa: float | None = Field(default=None, ge=0, le=10)
    start_year: int | None = None
    graduation_year: int | None = None


class Profile(BaseModel):
    """Facts about the user. One of the three allowed sources of resume content, and
    the source for portal form fields."""

    model_config = ConfigDict(extra="forbid")

    full_name: str = ""
    email: EmailStr | None = None
    phone: str | None = None
    location: str | None = None
    links: dict[str, HttpUrl] = Field(default_factory=dict)  # linkedin, github, portfolio
    education: list[Education] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    # Extra answers for portal forms, e.g. {"roll_number": "...", "notice_period": "..."}.
    form_fields: dict[str, str] = Field(default_factory=dict)


SecretName = Literal["github_token"]
SECRET_NAMES: tuple[str, ...] = (
    "github_token",
    # OAuth apps for mailbox access (created by you in Google Cloud / Azure)
    "google_client_id",
    "google_client_secret",
    "microsoft_client_id",
    "microsoft_client_secret",
    "microsoft_tenant",
    # Web search (company discovery; finding a LinkedIn posting on the company's own site)
    "tavily_api_key",
)


class SecretStatus(BaseModel):
    name: str
    is_set: bool
    masked: str | None


class SecretUpdate(BaseModel):
    value: str = Field(min_length=1, max_length=4096)
