from __future__ import annotations

from enum import StrEnum

from app.security.redaction import redact


class ErrorKind(StrEnum):
    AUTH = "auth"  # invalid/revoked key, permission denied
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"  # plan has no quota for this model (e.g. free tier "limit: 0")
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"  # outage, 5xx, connection failure
    BAD_REQUEST = "bad_request"
    NOT_FOUND = "not_found"  # unknown model
    CONTEXT_WINDOW = "context_window"
    UNKNOWN = "unknown"


# Worth retrying the same target once; the others go straight to the fallback.
RETRYABLE = frozenset({ErrorKind.RATE_LIMIT, ErrorKind.TIMEOUT, ErrorKind.UNAVAILABLE})
# Settings problems: every call will fail the same way until the user changes the config.
CONFIG_PROBLEMS = frozenset({ErrorKind.AUTH, ErrorKind.NOT_FOUND, ErrorKind.QUOTA})


class LLMError(Exception):
    """Base class for gateway errors. Messages are always redacted."""

    def __init__(self, message: str) -> None:
        super().__init__(redact(message))


class LLMProviderError(LLMError):
    def __init__(self, kind: ErrorKind, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry_after_s = retry_after_s

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


class LLMNotConfiguredError(LLMError):
    """No (enabled) provider/model is assigned to the task."""


class LLMCallFailedError(LLMError):
    """Primary and fallback both failed."""

    def __init__(self, message: str, errors: list[LLMProviderError]) -> None:
        super().__init__(message)
        self.errors = errors

    @property
    def transient(self) -> bool:
        """True when every failure was temporary (overload, rate limit, timeout)."""
        return bool(self.errors) and all(e.kind in RETRYABLE for e in self.errors)

    @property
    def config_problem(self) -> bool:
        """True when retrying later can't help (retired model, bad key, zero quota)."""
        return bool(self.errors) and all(e.kind in CONFIG_PROBLEMS for e in self.errors)


class StructuredOutputError(LLMError):
    """The model never produced output matching the schema, even after repair attempts."""


class BudgetExceededError(LLMError):
    """Monthly budget reached; non-urgent work must pause."""
