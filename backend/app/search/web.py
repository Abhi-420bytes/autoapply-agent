"""Web search for the agent.

- Tavily (free plan, no card): used when a `tavily_api_key` is saved. The key is sent only
  to api.tavily.com and never logged.
- Gemini with Google Search grounding (via the gateway): used otherwise; Google only
  enables it on billed Gemini keys.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import httpx
from sqlalchemy.orm import Session

from app.llm.errors import LLMError, LLMNotConfiguredError
from app.llm.gateway import LLMGateway


class SearchError(Exception):
    """User-facing search problem (no Gemini provider, quota, outage)."""


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    description: str
    # True: `description` is text extracted from the page itself (usable as evidence);
    # False: it was written by a model about the page (must be checked on the page).
    extracted: bool = True


class Searcher(Protocol):
    def search(self, query: str, *, count: int = 10) -> list[SearchResult]: ...


class GeminiSearch:
    def __init__(self, gateway: LLMGateway, job_id: int | None = None) -> None:
        self._gateway = gateway
        self._job_id = job_id

    def search(self, query: str, *, count: int = 10) -> list[SearchResult]:
        try:
            found = self._gateway.web_search(query, job_id=self._job_id)
        except LLMNotConfiguredError as exc:
            raise SearchError(str(exc)) from None
        except LLMError as exc:
            text = str(exc)
            if "quota" in text.lower() or "billing" in text.lower():
                raise SearchError(
                    "Gemini's Google Search isn't available on your free Gemini key (Google "
                    "asks for billing). Add a free Tavily key in Settings → Outreach."
                ) from None
            raise SearchError(f"Gemini web search failed: {text[:300]}") from None
        return [SearchResult(r.url, r.title, r.snippet, extracted=False) for r in found[:count]]


TAVILY_ENDPOINT = "https://api.tavily.com/search"


class TavilySearch:
    def __init__(
        self,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        min_interval_s: float = 1.0,
    ) -> None:
        if not api_key:
            raise SearchError("add a Tavily API key in Settings → Outreach")
        self._http = httpx.Client(
            timeout=30, transport=transport, headers={"Authorization": f"Bearer {api_key}"}
        )
        self._interval = min_interval_s
        self._last = 0.0

    def search(self, query: str, *, count: int = 10) -> list[SearchResult]:
        wait = self._interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            r = self._http.post(
                TAVILY_ENDPOINT,
                json={"query": query[:400], "max_results": min(count, 20), "search_depth": "basic"},
            )
        except httpx.HTTPError as exc:
            raise SearchError(f"Tavily unreachable: {exc}") from None
        finally:
            self._last = time.monotonic()
        if r.status_code in (401, 403):
            raise SearchError("Tavily rejected the API key; check it in Settings → Outreach")
        if r.status_code in (429, 432, 433):
            raise SearchError("Tavily's monthly free searches are used up (or rate-limited)")
        if r.status_code >= 400:
            raise SearchError(f"Tavily error {r.status_code}: {r.text[:200]}")
        return [
            SearchResult(
                str(i.get("url", "")), str(i.get("title", "")), str(i.get("content", ""))[:600]
            )
            for i in (r.json().get("results") or [])
            if i.get("url")
        ]

    def close(self) -> None:
        self._http.close()


def make_searcher(db: Session, gateway: LLMGateway, job_id: int | None = None) -> Searcher:
    """Tavily when its key is saved, else Gemini search through the gateway."""
    from app.services.settings_service import get_secret

    key = get_secret(db, "tavily_api_key")
    return TavilySearch(key) if key else GeminiSearch(gateway, job_id=job_id)
