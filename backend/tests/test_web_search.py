"""Web search through Gemini's Google Search grounding (replaces a paid search API)."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy.orm import Session

from app.llm.backend import Target, gemini_web_search
from app.llm.errors import ErrorKind, LLMProviderError
from app.llm.gateway import LLMGateway
from app.models import LLMProvider, LLMTaskConfig, LLMUsage
from app.models.enums import LLMProviderKind, LLMTask
from app.outreach.discover import search_published_email
from app.search.web import GeminiSearch, SearchError, SearchResult
from tests.fakes import FakeBackend

REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"
GROUNDED = {
    "candidates": [
        {
            "content": {"parts": [{"text": "Acme builds payment APIs; careers@acme.io"}]},
            "groundingMetadata": {
                "groundingChunks": [
                    {"web": {"uri": REDIRECT, "title": "acme.io"}},
                    {"web": {"uri": "https://zeta.tech/careers", "title": "zeta.tech"}},
                ],
                "groundingSupports": [
                    {
                        "segment": {"text": "Acme builds payment APIs."},
                        "groundingChunkIndices": [0],
                    },
                    {
                        "segment": {"text": "Write to careers@acme.io."},
                        "groundingChunkIndices": [0],
                    },
                    {"segment": {"text": "Zeta is hiring."}, "groundingChunkIndices": [1]},
                ],
            },
        }
    ],
    "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 40},
}


def test_grounded_results_are_parsed_and_redirects_resolved() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(REDIRECT):
            return httpx.Response(302, headers={"location": "https://acme.io/careers"})
        seen["url"] = str(request.url)
        seen["key"] = request.headers["x-goog-api-key"]
        seen["tools"] = json.dumps(json.loads(request.content)["tools"])
        return httpx.Response(200, json=GROUNDED)

    t = Target(
        provider_id=1, kind=LLMProviderKind.GEMINI, model="gemini/gemini-flash-latest", api_key="k"
    )
    raw = gemini_web_search(
        t, "python startup bengaluru", timeout_s=5, transport=httpx.MockTransport(handler)
    )
    assert (
        seen["url"].endswith("/models/gemini-flash-latest:generateContent") and seen["key"] == "k"
    )
    assert "google_search" in seen["tools"]
    assert [(r.url, r.title) for r in raw.results] == [
        ("https://acme.io/careers", "acme.io"),
        ("https://zeta.tech/careers", "zeta.tech"),
    ]
    assert "careers@acme.io" in raw.results[0].snippet and raw.tokens_in == 12


def test_quota_errors_are_classified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text='{"error": "Quota exceeded ... limit: 0"}')

    t = Target(provider_id=1, kind=LLMProviderKind.GEMINI, model="gemini-pro-latest", api_key="k")
    with pytest.raises(LLMProviderError) as exc:
        gemini_web_search(t, "q", timeout_s=5, transport=httpx.MockTransport(handler))
    assert exc.value.kind is ErrorKind.QUOTA


def test_gateway_uses_the_configured_gemini_provider_and_logs_usage(
    db: Session, gateway: LLMGateway, backend: FakeBackend
) -> None:
    with pytest.raises(SearchError, match="add a Gemini provider"):
        GeminiSearch(gateway).search("x")
    p = LLMProvider(label="G", kind=LLMProviderKind.GEMINI, api_key="AIza-test-key-0000")
    db.add(p)
    db.flush()
    db.add(
        LLMTaskConfig(
            task=LLMTask.JD_ANALYZER, provider_id=p.id, model="gemini-flash-latest", params={}
        )
    )
    db.commit()
    backend.script(
        "gemini-flash-latest",
        json.dumps([{"url": "https://acme.io", "title": "acme.io", "snippet": "Acme"}]),
    )
    assert GeminiSearch(gateway).search("acme") == [
        SearchResult("https://acme.io", "acme.io", "Acme", extracted=False)
    ]
    usage = db.query(LLMUsage).one()
    assert usage.prompt_ref == "web_search" and usage.success


class _Search:
    def __init__(self, extracted: bool) -> None:
        self.extracted = extracted

    def search(self, query: str, *, count: int = 10) -> list[SearchResult]:
        return [
            SearchResult(
                "https://acme.io/careers",
                "acme.io",
                "Write to careers@acme.io or ceo@acme.io",
                extracted=self.extracted,
            ),
        ]


def test_model_written_search_text_needs_the_page_to_confirm() -> None:
    model = _Search(extracted=False)
    assert search_published_email(model, "acme.io", lambda url, addr: False) is None
    assert search_published_email(model, "acme.io", lambda url, addr: True) == (
        "careers@acme.io",
        "https://acme.io/careers",
    )


def test_page_text_from_search_is_evidence_and_personal_needs_hiring_context() -> None:
    from app.outreach.discover import search_emails

    found = search_emails(_Search(extracted=True), "Acme", "acme.io", lambda u, a: False)
    # careers@ is taken from real page text; ceo@ is personal and not about hiring there
    assert [(f.address, f.source) for f in found] == [("careers@acme.io", "web")]


def test_tavily_search_and_errors() -> None:
    from app.search.web import TavilySearch

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        body = json.loads(request.content)
        if body["query"] == "used up":
            return httpx.Response(432, json={"detail": "limit"})
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": "https://acme.io/careers", "title": "Careers", "content": "Join Acme"}
                ]
            },
        )

    t = TavilySearch("tvly-test", transport=httpx.MockTransport(handler), min_interval_s=0)
    assert t.search("python startups") == [
        SearchResult("https://acme.io/careers", "Careers", "Join Acme")
    ]
    assert seen["auth"] == "Bearer tvly-test"
    with pytest.raises(SearchError, match="used up"):
        t.search("used up")


def test_tavily_is_preferred_when_its_key_is_saved(db: Session, gateway: LLMGateway) -> None:
    from app.search.web import TavilySearch, make_searcher
    from app.services.settings_service import set_secret

    assert isinstance(make_searcher(db, gateway), GeminiSearch)
    set_secret(db, "tavily_api_key", "tvly-abc")
    assert isinstance(make_searcher(db, gateway), TavilySearch)


def test_gemini_billing_refusal_points_to_tavily(
    db: Session, gateway: LLMGateway, backend: FakeBackend
) -> None:
    p = LLMProvider(label="G", kind=LLMProviderKind.GEMINI, api_key="AIza-test-key-0000")
    db.add(p)
    db.flush()
    db.add(
        LLMTaskConfig(
            task=LLMTask.PORTAL_HELPER, provider_id=p.id, model="gemini-flash-latest", params={}
        )
    )
    db.commit()
    backend.script(
        "gemini-flash-latest",
        LLMProviderError(ErrorKind.QUOTA, "429 check your plan and billing details"),
    )
    with pytest.raises(SearchError, match="Tavily"):
        GeminiSearch(gateway).search("x")
