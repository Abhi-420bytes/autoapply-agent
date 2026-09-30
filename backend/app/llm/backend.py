"""Provider access. This is the ONLY module that imports LiteLLM or talks to provider
APIs directly (enforced by tests/test_architecture.py). The gateway depends on the
`LLMBackend` protocol, so tests substitute a fake.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

import httpx

from app.llm.catalog import litellm_model, spec_for
from app.llm.errors import ErrorKind, LLMProviderError
from app.models.enums import LLMProviderKind as K

# Use LiteLLM's bundled price map instead of fetching it from GitHub at import time.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

Message = dict[str, str]

# Model ids that can't serve text chat tasks (image/speech/audio/agents/embeddings...).
_NON_CHAT_MARKERS = (
    "embed",
    "tts",
    "whisper",
    "transcribe",
    "dall-e",
    "image",
    "audio",
    "realtime",
    "moderation",
    "lyria",
    "robotics",
    "computer-use",
    "nano-banana",
    "veo",
    "imagen",
    "guard",
    "deep-research",
    "antigravity",
    "customtools",
    "sora",
    "babbage",
    "davinci",
)


_ZERO_QUOTA_RE = re.compile(
    r"limit:\s*0\b|insufficient_quota|exceeded your current quota.*limit: 0",
    re.IGNORECASE | re.DOTALL,
)


def _is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(marker in lowered for marker in _NON_CHAT_MARKERS)


@dataclass(frozen=True)
class Target:
    """A concrete provider + model to call, with decrypted credentials (memory only)."""

    provider_id: int | None
    kind: K
    model: str
    api_key: str | None = None
    base_url: str | None = None

    def __repr__(self) -> str:  # never print the key
        return f"Target(provider_id={self.provider_id}, kind={self.kind}, model={self.model!r})"


@dataclass(frozen=True)
class RawCompletion:
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal


@dataclass(frozen=True)
class RawEmbedding:
    vectors: list[list[float]]
    tokens_in: int
    cost_usd: Decimal


@dataclass(frozen=True)
class WebResult:
    url: str
    title: str
    snippet: str


@dataclass(frozen=True)
class RawSearch:
    results: list[WebResult]
    tokens_in: int
    cost_usd: Decimal
    tokens_out: int = 0


GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
SEARCH_PROMPT = (
    "Use Google Search for the query below. For each relevant web page you find, describe in "
    "one or two sentences what the page is and which company it belongs to, and copy any "
    "email address the page itself publishes exactly as written. Don't guess addresses.\n\n"
    "Query: {query}"
)


def _http_error(status: int, text: str) -> LLMProviderError:
    kind = (
        ErrorKind.AUTH
        if status in (401, 403)
        else ErrorKind.NOT_FOUND
        if status == 404
        else ErrorKind.QUOTA
        if status == 429 and "limit: 0" in text
        else ErrorKind.RATE_LIMIT
        if status == 429
        else ErrorKind.BAD_REQUEST
        if status == 400
        else ErrorKind.UNAVAILABLE
    )
    return LLMProviderError(kind, f"Gemini web search {status}: {text[:300]}")


def gemini_web_search(
    target: Target,
    query: str,
    *,
    timeout_s: float,
    transport: httpx.BaseTransport | None = None,
) -> RawSearch:
    """Gemini with Google Search grounding: the sources Gemini actually used, with the
    text it wrote about each (grounding supports), and redirect links resolved."""
    model = target.model.split("/", 1)[-1]
    body = {
        "contents": [{"role": "user", "parts": [{"text": SEARCH_PROMPT.format(query=query)}]}],
        "tools": [{"google_search": {}}],
    }
    try:
        with httpx.Client(timeout=timeout_s, transport=transport) as http:
            r = http.post(
                f"{GEMINI_API}/models/{model}:generateContent",
                headers={"x-goog-api-key": target.api_key or ""},
                json=body,
            )
            if r.status_code >= 400:
                raise _http_error(r.status_code, r.text)
            data = r.json()
            cand = (data.get("candidates") or [{}])[0]
            meta = cand.get("groundingMetadata") or {}
            chunks = meta.get("groundingChunks") or []
            snippets: dict[int, list[str]] = {}
            for sup in meta.get("groundingSupports") or []:
                text = (sup.get("segment") or {}).get("text", "")
                for i in sup.get("groundingChunkIndices") or []:
                    snippets.setdefault(int(i), []).append(text)
            results: list[WebResult] = []
            for i, ch in enumerate(chunks):
                web = ch.get("web") or {}
                uri = str(web.get("uri", ""))
                if not uri:
                    continue
                results.append(
                    WebResult(
                        url=_resolve_redirect(http, uri),
                        title=str(web.get("title", "")),
                        snippet=" ".join(snippets.get(i, []))[:600],
                    )
                )
    except httpx.TimeoutException as exc:
        raise LLMProviderError(ErrorKind.TIMEOUT, f"Gemini web search timed out: {exc}") from None
    except httpx.HTTPError as exc:
        raise LLMProviderError(ErrorKind.UNAVAILABLE, f"Gemini web search failed: {exc}") from None
    usage = data.get("usageMetadata") or {}
    return RawSearch(
        results=results,
        tokens_in=int(usage.get("promptTokenCount", 0)),
        tokens_out=int(usage.get("candidatesTokenCount", 0)),
        cost_usd=Decimal(0),
    )


def _resolve_redirect(http: httpx.Client, uri: str) -> str:
    """Grounding links are Google redirect URLs; read the Location header (no page load)."""
    if "grounding-api-redirect" not in uri:
        return uri
    for method in ("HEAD", "GET"):
        try:
            r = http.request(method, uri, follow_redirects=False)
        except httpx.HTTPError:
            continue
        loc = r.headers.get("location")
        if loc:
            return str(loc)
    return uri


class LLMBackend(Protocol):
    def complete(
        self,
        target: Target,
        messages: list[Message],
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> RawCompletion: ...

    def embed(self, target: Target, texts: list[str], *, timeout_s: float) -> RawEmbedding: ...

    def web_search(self, target: Target, query: str, *, timeout_s: float) -> RawSearch: ...

    def list_models(
        self, kind: K, api_key: str | None, base_url: str | None, *, embedding: bool = False
    ) -> list[str]: ...


class LiteLLMBackend:
    def __init__(self, mock_response: str | None = None) -> None:
        import litellm

        litellm.telemetry = False
        litellm.suppress_debug_info = True
        litellm.drop_params = True  # ignore params a provider doesn't support
        self._litellm = litellm
        self._mock_response = mock_response  # tests only: exercise LiteLLM offline
        self._st_models: dict[str, Any] = {}
        self._st_lock = threading.Lock()

    # -- web search (Gemini + Google Search grounding) ------------------------------------

    def web_search(self, target: Target, query: str, *, timeout_s: float) -> RawSearch:
        return gemini_web_search(target, query, timeout_s=timeout_s)

    # -- chat ---------------------------------------------------------------------------

    def complete(
        self,
        target: Target,
        messages: list[Message],
        *,
        temperature: float,
        max_tokens: int,
        timeout_s: float,
    ) -> RawCompletion:
        kwargs: dict[str, Any] = {
            "model": litellm_model(target.kind, target.model),
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "timeout": timeout_s,
            "num_retries": 0,  # the gateway owns retry/fallback policy
        }
        kwargs.update(self._auth(target))
        if self._mock_response is not None:
            kwargs["mock_response"] = self._mock_response
        try:
            resp = self._litellm.completion(**kwargs)
        except Exception as exc:
            raise self._map_error(exc) from None

        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        return RawCompletion(
            text=text,
            tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
            cost_usd=self._cost(resp),
        )

    # -- embeddings ---------------------------------------------------------------------

    def embed(self, target: Target, texts: list[str], *, timeout_s: float) -> RawEmbedding:
        if target.kind is K.LOCAL_SENTENCE_TRANSFORMERS:
            return self._embed_local(target.model, texts)
        kwargs: dict[str, Any] = {
            "model": litellm_model(target.kind, target.model, embedding=True),
            "input": texts,
            "timeout": timeout_s,
        }
        kwargs.update(self._auth(target))
        try:
            resp = self._litellm.embedding(**kwargs)
        except Exception as exc:
            raise self._map_error(exc) from None
        vectors = [list(map(float, _get(d, "embedding"))) for d in resp.data]
        usage = getattr(resp, "usage", None)
        return RawEmbedding(
            vectors=vectors,
            tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
            cost_usd=self._cost(resp),
        )

    def _embed_local(self, model: str, texts: list[str]) -> RawEmbedding:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise LLMProviderError(
                ErrorKind.BAD_REQUEST,
                "sentence-transformers is not installed; rebuild the image with "
                "INSTALL_LOCAL_EMBEDDINGS=true or pick another embedding provider",
            ) from None
        with self._st_lock:
            if model not in self._st_models:
                self._st_models[model] = SentenceTransformer(model)
            st = self._st_models[model]
        vecs = st.encode(texts, normalize_embeddings=True)
        return RawEmbedding(
            vectors=[list(map(float, v)) for v in vecs], tokens_in=0, cost_usd=Decimal(0)
        )

    # -- model listing ------------------------------------------------------------------

    def list_models(
        self, kind: K, api_key: str | None, base_url: str | None, *, embedding: bool = False
    ) -> list[str]:
        spec = spec_for(kind)
        base = (base_url or spec.default_base_url or "").rstrip("/")
        bearer = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            if kind is K.ANTHROPIC:
                data = _get_json(
                    "https://api.anthropic.com/v1/models?limit=1000",
                    {"x-api-key": api_key or "", "anthropic-version": "2023-06-01"},
                )
                ids = [m["id"] for m in data.get("data", [])]
            elif kind is K.GEMINI:
                data = _get_json(
                    "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000",
                    {"x-goog-api-key": api_key or ""},
                )
                method = "embedContent" if embedding else "generateContent"
                ids = [
                    m["name"].removeprefix("models/")
                    for m in data.get("models", [])
                    if method in m.get("supportedGenerationMethods", [])
                ]
            elif kind is K.OLLAMA:
                data = _get_json(f"{base}/api/tags", {})
                return sorted(m["name"] for m in data.get("models", []))
            elif kind in (K.OPENAI, K.GROQ, K.OPENROUTER, K.OPENAI_COMPATIBLE):
                url = {
                    K.OPENAI: "https://api.openai.com/v1/models",
                    K.GROQ: "https://api.groq.com/openai/v1/models",
                }.get(kind, f"{base}/models")
                data = _get_json(url, bearer)
                ids = [m["id"] for m in data.get("data", [])]
            else:  # Voyage / local: no listing endpoint
                return list(spec.suggested_embedding_models if embedding else spec.suggested_models)
        except LLMProviderError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMProviderError(
                ErrorKind.UNKNOWN, f"unexpected model list format: {exc}"
            ) from None

        if embedding:
            if kind in (K.OPENAI, K.OPENAI_COMPATIBLE):
                ids = [i for i in ids if "embed" in i.lower()]
        else:
            ids = [i for i in ids if _is_chat_model(i)]
        # "-latest" aliases first: providers keep them current, unlike versioned ids.
        return sorted(ids, key=lambda i: (not i.endswith("-latest"), i))

    # -- helpers ------------------------------------------------------------------------

    @staticmethod
    def _auth(target: Target) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if target.api_key:
            out["api_key"] = target.api_key
        base = target.base_url or spec_for(target.kind).default_base_url
        if base:
            out["api_base"] = base
        return out

    def _cost(self, resp: Any) -> Decimal:
        try:
            return Decimal(str(self._litellm.completion_cost(completion_response=resp) or 0))
        except Exception:  # unknown/local model — no price data
            return Decimal(0)

    def _map_error(self, exc: Exception) -> LLMProviderError:
        ex = self._litellm.exceptions
        table: list[tuple[type[BaseException], ErrorKind]] = [
            (ex.ContextWindowExceededError, ErrorKind.CONTEXT_WINDOW),
            (ex.AuthenticationError, ErrorKind.AUTH),
            (ex.PermissionDeniedError, ErrorKind.AUTH),
            (ex.RateLimitError, ErrorKind.RATE_LIMIT),
            (ex.Timeout, ErrorKind.TIMEOUT),
            (ex.NotFoundError, ErrorKind.NOT_FOUND),
            (ex.ServiceUnavailableError, ErrorKind.UNAVAILABLE),
            (ex.InternalServerError, ErrorKind.UNAVAILABLE),
            (ex.BadGatewayError, ErrorKind.UNAVAILABLE),
            (ex.APIConnectionError, ErrorKind.UNAVAILABLE),
            (ex.BadRequestError, ErrorKind.BAD_REQUEST),
        ]
        kind = next((k for cls, k in table if isinstance(exc, cls)), ErrorKind.UNKNOWN)
        if kind is ErrorKind.RATE_LIMIT and _ZERO_QUOTA_RE.search(str(exc)):
            kind = ErrorKind.QUOTA  # retrying can't help; go straight to the fallback
        retry_after = _retry_after(exc)
        return LLMProviderError(kind, f"{type(exc).__name__}: {exc}", retry_after)


def _get(obj: Any, key: str) -> Any:
    return obj[key] if isinstance(obj, dict) else getattr(obj, key)


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("retry-after")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _get_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    try:
        r = httpx.get(url, headers=headers, timeout=15)
    except httpx.TimeoutException:
        raise LLMProviderError(ErrorKind.TIMEOUT, "timed out listing models") from None
    except httpx.HTTPError as exc:
        raise LLMProviderError(ErrorKind.UNAVAILABLE, f"could not reach provider: {exc}") from None
    if r.status_code in (401, 403):
        raise LLMProviderError(
            ErrorKind.AUTH, f"provider rejected the credentials ({r.status_code})"
        )
    if r.status_code == 429:
        raise LLMProviderError(ErrorKind.RATE_LIMIT, "rate limited while listing models")
    if r.status_code >= 400:
        raise LLMProviderError(ErrorKind.UNAVAILABLE, f"model listing failed ({r.status_code})")
    data: dict[str, Any] = r.json()
    return data
