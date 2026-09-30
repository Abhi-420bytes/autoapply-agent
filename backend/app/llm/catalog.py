"""Static knowledge about each provider kind and per-task defaults.

This is the only place that knows provider-specific details (LiteLLM prefixes, default
endpoints, how to list models). Everything else refers to providers by `LLMProviderKind`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from app.models.enums import LLMProviderKind as K
from app.models.enums import LLMTask


@dataclass(frozen=True)
class ProviderSpec:
    kind: K
    display_name: str
    litellm_prefix: str  # LiteLLM model prefix for chat, e.g. "anthropic/"
    requires_api_key: bool = True
    requires_base_url: bool = False
    default_base_url: str | None = None
    supports_chat: bool = True
    supports_embeddings: bool = False
    embedding_prefix: str | None = None  # when it differs from the chat prefix
    # Shown when live model listing is unavailable; users can always type a custom id.
    suggested_models: tuple[str, ...] = ()
    suggested_embedding_models: tuple[str, ...] = ()
    key_hint: str = ""
    notes: str = ""
    extra: dict[str, str] = field(default_factory=dict)


PROVIDERS: dict[K, ProviderSpec] = {
    K.ANTHROPIC: ProviderSpec(
        kind=K.ANTHROPIC,
        display_name="Anthropic (Claude)",
        litellm_prefix="anthropic/",
        suggested_models=("claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"),
        key_hint="sk-ant-...",
    ),
    K.OPENAI: ProviderSpec(
        kind=K.OPENAI,
        display_name="OpenAI",
        litellm_prefix="openai/",
        supports_embeddings=True,
        suggested_models=("gpt-4.1", "gpt-4.1-mini", "gpt-4o-mini"),
        suggested_embedding_models=("text-embedding-3-small", "text-embedding-3-large"),
        key_hint="sk-...",
    ),
    K.GEMINI: ProviderSpec(
        kind=K.GEMINI,
        display_name="Google Gemini",
        litellm_prefix="gemini/",
        supports_embeddings=True,
        # "-latest" aliases are kept current by Google, so they don't go stale when
        # versioned models are retired.
        # Flash first: it works on the free tier, where Pro models have zero quota.
        suggested_models=("gemini-flash-latest", "gemini-flash-lite-latest", "gemini-pro-latest"),
        suggested_embedding_models=("gemini-embedding-001", "gemini-embedding-2"),
        key_hint="AIza...",
    ),
    K.GROQ: ProviderSpec(
        kind=K.GROQ,
        display_name="Groq",
        litellm_prefix="groq/",
        suggested_models=("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
        key_hint="gsk_...",
    ),
    K.OPENROUTER: ProviderSpec(
        kind=K.OPENROUTER,
        display_name="OpenRouter",
        litellm_prefix="openrouter/",
        default_base_url="https://openrouter.ai/api/v1",
        suggested_models=("anthropic/claude-sonnet-5", "openai/gpt-4.1-mini"),
        key_hint="sk-or-...",
    ),
    K.OLLAMA: ProviderSpec(
        kind=K.OLLAMA,
        display_name="Ollama (local)",
        litellm_prefix="ollama_chat/",
        embedding_prefix="ollama/",
        requires_api_key=False,
        requires_base_url=True,
        default_base_url="http://host.docker.internal:11434",
        supports_embeddings=True,
        suggested_models=("llama3.1", "qwen2.5"),
        suggested_embedding_models=("nomic-embed-text", "bge-m3"),
        notes="From Docker, use http://host.docker.internal:11434 to reach Ollama on your Mac.",
    ),
    K.OPENAI_COMPATIBLE: ProviderSpec(
        kind=K.OPENAI_COMPATIBLE,
        display_name="Custom OpenAI-compatible",
        litellm_prefix="openai/",
        requires_api_key=False,
        requires_base_url=True,
        supports_embeddings=True,
        notes="Any server exposing /v1/chat/completions (vLLM, LM Studio, LiteLLM proxy...).",
    ),
    K.VOYAGE: ProviderSpec(
        kind=K.VOYAGE,
        display_name="Voyage AI (embeddings)",
        litellm_prefix="voyage/",
        supports_chat=False,
        supports_embeddings=True,
        suggested_embedding_models=("voyage-3.5", "voyage-3.5-lite", "voyage-3-large"),
        key_hint="pa-...",
    ),
    K.LOCAL_SENTENCE_TRANSFORMERS: ProviderSpec(
        kind=K.LOCAL_SENTENCE_TRANSFORMERS,
        display_name="Local sentence-transformers (embeddings)",
        litellm_prefix="",
        requires_api_key=False,
        supports_chat=False,
        supports_embeddings=True,
        suggested_embedding_models=("BAAI/bge-small-en-v1.5",),
        notes="Runs in the worker. Requires building the image with INSTALL_LOCAL_EMBEDDINGS=true.",
    ),
}


class TaskParams(BaseModel):
    """Per-task call parameters. Stored in llm_task_config.params, merged over defaults."""

    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=1, le=64_000)
    timeout_s: float = Field(default=60, gt=0, le=600)
    prompt_version: int | None = None  # pin a prompt version; None → latest


TASK_DEFAULTS: dict[LLMTask, TaskParams] = {
    LLMTask.EMAIL_CLASSIFIER: TaskParams(temperature=0, max_tokens=512, timeout_s=30),
    LLMTask.JD_ANALYZER: TaskParams(temperature=0, max_tokens=2048, timeout_s=90),
    LLMTask.RESUME_WRITER: TaskParams(temperature=0.3, max_tokens=4096, timeout_s=180),
    LLMTask.ATS_SCORER: TaskParams(temperature=0, max_tokens=2048, timeout_s=90),
    LLMTask.FINAL_POLISH: TaskParams(temperature=0.2, max_tokens=4096, timeout_s=120),
    LLMTask.PORTAL_HELPER: TaskParams(temperature=0, max_tokens=1024, timeout_s=60),
    LLMTask.REPO_SUMMARIZER: TaskParams(temperature=0.1, max_tokens=1024, timeout_s=60),
    LLMTask.EMBEDDING: TaskParams(timeout_s=60),
}

TASK_DESCRIPTIONS: dict[LLMTask, str] = {
    LLMTask.EMAIL_CLASSIFIER: "Classifies incoming emails. A cheap, fast model is enough.",
    LLMTask.JD_ANALYZER: "Extracts skills, keywords and eligibility from job descriptions.",
    LLMTask.RESUME_WRITER: "Selects and rewrites resume bullets. Use your strongest model.",
    LLMTask.ATS_SCORER: "Scores resume/JD fit and reviews each draft for the writer.",
    LLMTask.FINAL_POLISH: "Tightens wording and length before the final compile.",
    LLMTask.PORTAL_HELPER: "Reads portal pages and picks the right link or button.",
    LLMTask.REPO_SUMMARIZER: "Summarizes GitHub repositories for the knowledge base.",
    LLMTask.EMBEDDING: "Embedding model for retrieval. Changing it re-indexes everything.",
}

CHAT_TASKS: tuple[LLMTask, ...] = tuple(t for t in LLMTask if t is not LLMTask.EMBEDDING)


def spec_for(kind: K) -> ProviderSpec:
    return PROVIDERS[kind]


def litellm_model(kind: K, model: str, *, embedding: bool = False) -> str:
    spec = spec_for(kind)
    prefix = (spec.embedding_prefix or spec.litellm_prefix) if embedding else spec.litellm_prefix
    return model if model.startswith(prefix) else f"{prefix}{model}"


def embedding_model_key(kind: K, model: str) -> str:
    """Identity stored with every vector: provider kind + model id."""
    return f"{kind.value}:{model}"
