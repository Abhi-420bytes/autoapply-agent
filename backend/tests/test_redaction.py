from __future__ import annotations

import logging

import pytest

from app.security.redaction import (
    REDACTED,
    RedactingFilter,
    redact,
    redact_mapping,
    register_secret,
)


@pytest.mark.parametrize(
    "leaky",
    [
        "calling with key sk-ant-api03-abcdefghijklmnop",
        "Authorization: Bearer eyJhbGciOi.abcdefghijk.lmnopqrstu",
        "token ghp_abcdefghijklmnopqrstuvwxyz0123",
        "gemini AIzaSyAbcdefghijklmnopqrstuvwx",
        "groq gsk_abcdefghijklmnopqrstuv",
        'payload {"password": "hunter2hunter2"}',
        "api_key=abcd1234efgh",
    ],
)
def test_patterns_are_redacted(leaky: str) -> None:
    out = redact(leaky)
    assert REDACTED in out
    for secret in ("abcdefghijklmnop", "hunter2hunter2", "abcd1234efgh", "lmnopqrstu"):
        assert secret not in out


def test_registered_secret_is_redacted_even_without_pattern() -> None:
    register_secret("correct-horse-battery")
    assert redact("login with correct-horse-battery ok") == f"login with {REDACTED} ok"


def test_logging_filter_redacts_args(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test.redaction")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="test.redaction"):
        logger.info("using key %s for %s", "sk-live-0123456789abcdef", "openai")
    assert "0123456789abcdef" not in caplog.text
    assert "openai" in caplog.text


def test_redact_mapping_masks_secret_keys_recursively() -> None:
    data = {"provider": "openai", "api_key": "whatever", "nested": [{"refresh_token": "r"}]}
    assert redact_mapping(data) == {
        "provider": "openai",
        "api_key": REDACTED,
        "nested": [{"refresh_token": REDACTED}],
    }
