"""Exercises the real LiteLLM adapter offline via LiteLLM's mock_response."""

from __future__ import annotations

import pytest

from app.llm.backend import LiteLLMBackend, Target
from app.llm.catalog import litellm_model
from app.llm.errors import ErrorKind, LLMProviderError
from app.models.enums import LLMProviderKind as K

T = Target(provider_id=1, kind=K.OPENAI, model="gpt-4o-mini", api_key="sk-test-000000000000")
MSG = [{"role": "user", "content": "hi"}]


def test_completion_through_litellm() -> None:
    raw = LiteLLMBackend(mock_response="pong").complete(
        T, MSG, temperature=0, max_tokens=5, timeout_s=5
    )
    assert raw.text == "pong" and raw.tokens_in > 0 and raw.cost_usd >= 0


@pytest.mark.parametrize(
    ("mock", "kind"),
    [
        ("litellm.RateLimitError", ErrorKind.RATE_LIMIT),
        ("litellm.InternalServerError", ErrorKind.UNAVAILABLE),
        ("litellm.ContextWindowExceededError", ErrorKind.CONTEXT_WINDOW),
    ],
)
def test_error_mapping(mock: str, kind: ErrorKind) -> None:
    with pytest.raises(LLMProviderError) as exc:
        LiteLLMBackend(mock_response=mock).complete(
            T, MSG, temperature=0, max_tokens=5, timeout_s=5
        )
    assert exc.value.kind is kind


def test_model_prefixes() -> None:
    assert litellm_model(K.ANTHROPIC, "claude-sonnet-5") == "anthropic/claude-sonnet-5"
    assert litellm_model(K.OLLAMA, "llama3.1") == "ollama_chat/llama3.1"
    assert litellm_model(K.OLLAMA, "nomic-embed-text", embedding=True) == "ollama/nomic-embed-text"
    assert (
        litellm_model(K.OPENROUTER, "anthropic/claude-sonnet-5")
        == "openrouter/anthropic/claude-sonnet-5"
    )
    assert litellm_model(K.OPENAI_COMPATIBLE, "my-model") == "openai/my-model"


def test_chat_model_filter_drops_non_text_models() -> None:
    from app.llm.backend import _is_chat_model

    keep = ["gemini-pro-latest", "gemini-3.1-pro-preview", "gpt-4.1-mini", "gemma-4-31b-it"]
    drop = [
        "gemini-3.1-flash-image",
        "gemini-3.8-flash-tts",
        "whisper-large-v3",
        "text-embedding-3-small",
        "lyria-3.5",
        "llama-guard-4",
        "nano-banana-pro-preview",
    ]
    assert all(_is_chat_model(m) for m in keep)
    assert not any(_is_chat_model(m) for m in drop)


def test_zero_quota_rate_limit_is_classified_as_quota() -> None:
    import litellm

    err = litellm.RateLimitError(
        message="Quota exceeded for metric: generate_content_free_tier_requests, limit: 0, "
        "model: gemini-3.1-pro",
        llm_provider="gemini",
        model="gemini-pro-latest",
    )
    with pytest.raises(LLMProviderError) as exc:
        LiteLLMBackend(mock_response=err).complete(T, MSG, temperature=0, max_tokens=5, timeout_s=5)  # type: ignore[arg-type]
    assert exc.value.kind is ErrorKind.QUOTA and not exc.value.retryable


def test_latest_aliases_listed_first() -> None:
    from app.llm import backend as b

    def fake_get(url: str, headers: dict[str, str]) -> dict[str, object]:
        return {
            "models": [
                {"name": f"models/{n}", "supportedGenerationMethods": ["generateContent"]}
                for n in ("gemini-2.5-flash-lite", "gemini-flash-latest", "gemini-3.5-flash")
            ]
        }

    original = b._get_json
    b._get_json = fake_get  # type: ignore[assignment]
    try:
        ids = LiteLLMBackend().list_models(K.GEMINI, "k", None)
    finally:
        b._get_json = original
    assert ids[0] == "gemini-flash-latest"
