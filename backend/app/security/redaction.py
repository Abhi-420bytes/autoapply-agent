"""Logging filter that scrubs credentials from every log record.

Two layers:
1. Pattern-based: common key/token shapes (sk-..., Bearer ..., ghp_..., JWT-ish, etc.).
2. Registry-based: any secret value decrypted at runtime is registered here, so even
   odd-shaped secrets (e.g. a portal password) are scrubbed if they ever reach a log line.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),  # OpenAI / Anthropic style
    re.compile(r"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{16,}"),  # GitHub tokens
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"),  # Google API keys
    re.compile(r"\bgsk_[A-Za-z0-9]{16,}"),  # Groq
    re.compile(r"\bya29\.[0-9A-Za-z_\-]+"),  # Google OAuth access tokens
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWTs
    re.compile(r"\bgAAAAA[A-Za-z0-9_\-=]{20,}"),  # Fernet ciphertext — no reason to log it
    re.compile(
        r"(?i)((?:api[_-]?key|password|passwd|secret|token|refresh_token|access_token)"
        r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"
    ),
]

_known_secrets: set[str] = set()
_lock = threading.Lock()
_MIN_REGISTERED_LEN = 6  # avoid scrubbing tiny common substrings


def register_secret(value: str | None) -> None:
    if value and len(value) >= _MIN_REGISTERED_LEN:
        with _lock:
            _known_secrets.add(value)


def redact(text: str) -> str:
    with _lock:
        known = sorted(_known_secrets, key=len, reverse=True)
    for secret in known:
        if secret in text:
            text = text.replace(secret, REDACTED)
    for pat in _PATTERNS:
        if pat.groups:
            text = pat.sub(lambda m: m.group(1) + REDACTED, text)
        else:
            text = pat.sub(REDACTED, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Render the message once, redact it, and drop args so handlers can't re-render
        # the raw values.
        try:
            message = record.getMessage()
        except Exception:  # malformed format args — still log something safe
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(stream)
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())


def redact_mapping(data: Any) -> Any:
    """Recursively redact a JSON-like structure (used for audit log details)."""
    if isinstance(data, dict):
        return {k: (REDACTED if _is_secret_key(k) else redact_mapping(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [redact_mapping(v) for v in data]
    if isinstance(data, str):
        return redact(data)
    return data


_SECRET_KEY_RE = re.compile(r"(?i)(api_?key|password|passwd|secret|token|credential)")


def _is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(key))
