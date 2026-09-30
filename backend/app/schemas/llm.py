from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.llm.catalog import TaskParams
from app.models.enums import LLMProviderKind, LLMTask


class ProviderSpecOut(BaseModel):
    kind: LLMProviderKind
    display_name: str
    requires_api_key: bool
    requires_base_url: bool
    default_base_url: str | None
    supports_chat: bool
    supports_embeddings: bool
    suggested_models: list[str]
    suggested_embedding_models: list[str]
    key_hint: str
    notes: str


def _clean_url(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip().rstrip("/")
    if not v:
        return None
    if not v.startswith(("http://", "https://")):
        raise ValueError("base URL must start with http:// or https://")
    return v


class ProviderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    kind: LLMProviderKind
    api_key: str | None = Field(default=None, max_length=4096)
    base_url: str | None = Field(default=None, max_length=500)
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return _clean_url(v)

    @field_validator("api_key")
    @classmethod
    def _strip_key(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None


class ProviderUpdate(BaseModel):
    """Omitted fields are unchanged. `api_key` replaces the stored key when provided;
    it is never sent back, so the UI leaves it blank to keep the current key."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(default=None, min_length=1, max_length=120)
    api_key: str | None = Field(default=None, max_length=4096)
    base_url: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None

    @field_validator("api_key")
    @classmethod
    def _strip_key(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return _clean_url(v)


class ProviderOut(BaseModel):
    id: int
    label: str
    kind: LLMProviderKind
    display_name: str
    has_api_key: bool
    api_key_masked: str | None
    base_url: str | None
    enabled: bool
    supports_chat: bool
    supports_embeddings: bool
    last_test_ok: bool | None
    last_test_at: datetime | None
    last_test_latency_ms: int | None
    last_test_error: str | None
    used_by_tasks: list[LLMTask]


class ProviderTestRequest(BaseModel):
    model: str | None = Field(default=None, max_length=200)


class ProviderTestOut(BaseModel):
    ok: bool
    latency_ms: int | None
    model: str | None
    error: str | None
    error_kind: str | None
    reply: str | None


class ModelListOut(BaseModel):
    models: list[str]
    source: Literal["live", "suggested"]
    error: str | None = None


class TaskParamsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float | None = None
    max_tokens: int | None = None
    timeout_s: float | None = None
    prompt_version: int | None = None


class TaskConfigOut(BaseModel):
    task: LLMTask
    description: str
    is_embedding: bool
    provider_id: int | None
    model: str | None
    fallback_provider_id: int | None
    fallback_model: str | None
    params: TaskParams
    configured: bool


class TaskConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: int | None
    model: str | None = Field(default=None, max_length=200)
    fallback_provider_id: int | None = None
    fallback_model: str | None = Field(default=None, max_length=200)
    params: TaskParamsPatch | None = None
    # Required when changing the embedding model while vectors exist.
    confirm_reindex: bool = False

    @field_validator("model", "fallback_model")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return (v.strip() or None) if v is not None else None


class TaskConfigUpdateOut(BaseModel):
    config: TaskConfigOut
    reindex_scheduled: bool = False
