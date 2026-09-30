from enum import StrEnum


class JobStatus(StrEnum):
    DETECTED = "detected"
    SCRAPED = "scraped"
    RESUME_READY = "resume_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLIED = "applied"
    SHORTLISTED = "shortlisted"
    REJECTED = "rejected"
    OFFER = "offer"
    SUSPICIOUS = "suspicious"
    INELIGIBLE = "ineligible"
    FAILED = "failed"  # agent error — needs the user's attention (screenshot attached)
    PAUSED = "paused"  # waiting on OTP/CAPTCHA or budget limit


class JobSource(StrEnum):
    EMAIL = "email"
    MANUAL = "manual"
    SITE = "site"  # found by searching a job website


class ApplyMode(StrEnum):
    DIRECT_LINK = "direct_link"
    READ_EMAIL_THEN_APPLY = "read_email_then_apply"


class EmailProvider(StrEnum):
    OUTLOOK_GRAPH = "outlook_graph"
    GMAIL_API = "gmail_api"
    IMAP = "imap"


class ConnectionStatus(StrEnum):
    PENDING = "pending"
    CONNECTED = "connected"
    ERROR = "error"
    DISABLED = "disabled"


class EmailClassification(StrEnum):
    UNCLASSIFIED = "unclassified"
    JOB_NOTICE = "job_notice"
    DEADLINE_EXTENSION = "deadline_extension"
    REMINDER = "reminder"
    RESULT = "result"
    GENERAL_NOTICE = "general_notice"
    SUSPICIOUS = "suspicious"


class PortalLoginMethod(StrEnum):
    PASSWORD = "password"  # noqa: S105 — enum label, not a credential
    SSO = "sso"
    OTP = "otp"
    MANUAL = "manual"  # user logs in once in a headed browser; session is persisted


class LLMProviderKind(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"
    VOYAGE = "voyage"  # embeddings only
    LOCAL_SENTENCE_TRANSFORMERS = "local_sentence_transformers"  # embeddings only


class LLMTask(StrEnum):
    EMAIL_CLASSIFIER = "email_classifier"
    JD_ANALYZER = "jd_analyzer"
    RESUME_WRITER = "resume_writer"
    ATS_SCORER = "ats_scorer"
    FINAL_POLISH = "final_polish"
    PORTAL_HELPER = "portal_helper"
    REPO_SUMMARIZER = "repo_summarizer"
    EMBEDDING = "embedding"


class ResumeKind(StrEnum):
    BASE_TEMPLATE = "base_template"
    PAST = "past"  # uploaded historical resume, source for the bullet bank
    GENERATED = "generated"
    MANUAL_EDIT = "manual_edit"


class BulletSource(StrEnum):
    PAST_RESUME = "past_resume"
    GITHUB = "github"
    PROFILE = "profile"


class JobFileKind(StrEnum):
    JD_ATTACHMENT = "jd_attachment"
    EMAIL_ATTACHMENT = "email_attachment"
    SCREENSHOT = "screenshot"
    CONFIRMATION_SCREENSHOT = "confirmation_screenshot"


class ApplicationStatus(StrEnum):
    PREPARED = "prepared"
    APPROVED = "approved"
    SUBMITTED = "submitted"
    FAILED = "failed"


class OutcomeKind(StrEnum):
    SHORTLISTED = "shortlisted"
    REJECTED = "rejected"
    OFFER = "offer"
    NO_RESPONSE = "no_response"


class AuditActor(StrEnum):
    USER = "user"
    SYSTEM = "system"
    AGENT = "agent"


class NotificationLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class RunState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class RunKind(StrEnum):
    GENERATE = "generate"
    SCRAPE = "scrape"  # portal agent: open the job link, read the JD, download attachments
    APPLY = "apply"  # portal agent: submit an approved application
    DISCOVER = "discover"  # job-site search: find postings matching the user's categories


class SiteMode(StrEnum):
    SEARCH_ONLY = "search_only"  # find + prepare resumes; the user applies
    SEARCH_AND_APPLY = "search_and_apply"  # also apply (approval gate still applies)
