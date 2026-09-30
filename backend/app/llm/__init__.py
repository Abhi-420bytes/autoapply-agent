"""LLM gateway package. Other modules import `get_gateway()` — never a provider SDK."""

from __future__ import annotations

from functools import lru_cache

from app.llm.gateway import LLMGateway


@lru_cache
def get_gateway() -> LLMGateway:
    from app.db.session import get_sessionmaker
    from app.llm.backend import LiteLLMBackend

    return LLMGateway(get_sessionmaker(), LiteLLMBackend())


__all__ = ["LLMGateway", "get_gateway"]
