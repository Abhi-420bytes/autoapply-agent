"""Importing this package registers every table on Base.metadata (Alembic relies on it)."""

from app.models.base import Base
from app.models.jobs import (
    Application,
    DiscoveredPosting,
    EmailAccount,
    Job,
    JobFile,
    JobSite,
    Outcome,
    OutreachCompany,
    OutreachEmail,
    OutreachSource,
    PipelineRun,
    Portal,
    PortalSkill,
    SenderRule,
    SourceEmail,
)
from app.models.knowledge import Bullet, Embedding, GitHubRepo, Resume, ResumeBullet
from app.models.llm import LLMProvider, LLMTaskConfig, LLMUsage
from app.models.system import AuditLog, Notification, Setting

__all__ = [
    "Application",
    "AuditLog",
    "Base",
    "Bullet",
    "DiscoveredPosting",
    "EmailAccount",
    "Embedding",
    "GitHubRepo",
    "Job",
    "JobFile",
    "JobSite",
    "LLMProvider",
    "LLMTaskConfig",
    "LLMUsage",
    "Notification",
    "Outcome",
    "OutreachCompany",
    "OutreachEmail",
    "OutreachSource",
    "PipelineRun",
    "Portal",
    "PortalSkill",
    "Resume",
    "ResumeBullet",
    "SenderRule",
    "Setting",
    "SourceEmail",
]
