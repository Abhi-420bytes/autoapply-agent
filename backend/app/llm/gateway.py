"""The single entry point for every LLM and embedding call (hard rule 9).

Responsibilities:
- route each call by task to the provider + model configured in the dashboard
- retry once with backoff on transient errors, then switch to the task's fallback
- enforce Pydantic output schemas with JSON validation + repair retries
- record tokens, cost, latency and errors for every attempt (llm_usage)
- enforce the monthly budget: non-urgent calls stop and the user is alerted
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.backend import (
    LLMBackend,
    Message,
    RawCompletion,
    RawEmbedding,
    RawSearch,
    Target,
    WebResult,
)
from app.llm.catalog import TASK_DEFAULTS, TaskParams, embedding_model_key, spec_for
from app.llm.errors import (
    BudgetExceededError,
    ErrorKind,
    LLMCallFailedError,
    LLMNotConfiguredError,
    LLMProviderError,
    StructuredOutputError,
)
from app.llm.prompts import PromptLibrary
from app.llm.usage import month_start, spend_since
from app.models import LLMProvider, LLMTaskConfig, LLMUsage
from app.models.enums import LLMProviderKind, LLMTask, NotificationLevel
from app.security.redaction import redact
from app.services.notifications import notify
from app.services.settings_service import get_app_settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R", RawCompletion, RawEmbedding, RawSearch)

EMBED_BATCH = 64
MAX_BACKOFF_S = 30.0


@dataclass(frozen=True)
class Completion:
    text: str
    task: LLMTask | None
    provider_kind: str
    model: str
    is_fallback: bool
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    latency_ms: int
    prompt_ref: str | None = None


@dataclass(frozen=True)
class StructuredResult(Generic[T]):
    value: T
    completions: list[Completion] = field(default_factory=list)

    @property
    def cost_usd(self) -> Decimal:
        return sum((c.cost_usd for c in self.completions), Decimal(0))

    @property
    def models_used(self) -> list[str]:
        return sorted({f"{c.provider_kind}:{c.model}" for c in self.completions})


@dataclass(frozen=True)
class EmbeddingResult:
    model_key: str  # stored with every vector; never mix keys
    dimension: int
    vectors: list[list[float]]
    cost_usd: Decimal


@dataclass(frozen=True)
class TestResult:
    ok: bool
    latency_ms: int | None
    model: str | None
    error: str | None = None
    error_kind: str | None = None
    reply: str | None = None


@dataclass(frozen=True)
class _Route:
    params: TaskParams
    targets: list[tuple[Target, bool]]  # (target, is_fallback)


class LLMGateway:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        backend: LLMBackend,
        prompts: PromptLibrary | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sessions = session_factory
        self.backend = backend
        self.prompts = prompts or PromptLibrary()
        self._sleep = sleep
        self._clock = clock

    # -- public API ---------------------------------------------------------------------

    @property
    def session_factory(self) -> Callable[[], Session]:
        """The session factory the gateway records usage with (shared by background jobs)."""
        return self._sessions

    def complete(
        self,
        task: LLMTask,
        messages: list[Message],
        *,
        job_id: int | None = None,
        urgent: bool = False,
        prompt_ref: str | None = None,
    ) -> Completion:
        if task is LLMTask.EMBEDDING:
            raise ValueError("use embed() for the embedding task")
        route = self._resolve(task)
        self._check_budget(urgent)
        p = route.params

        def call(t: Target) -> RawCompletion:
            return self.backend.complete(
                t,
                messages,
                temperature=p.temperature,
                max_tokens=p.max_tokens,
                timeout_s=p.timeout_s,
            )

        raw, target, is_fb, latency = self._run(task, route, call, job_id, prompt_ref)
        return Completion(
            text=raw.text,
            task=task,
            provider_kind=target.kind.value,
            model=target.model,
            is_fallback=is_fb,
            tokens_in=raw.tokens_in,
            tokens_out=raw.tokens_out,
            cost_usd=raw.cost_usd,
            latency_ms=latency,
            prompt_ref=prompt_ref,
        )

    def run_prompt(
        self,
        task: LLMTask,
        prompt_name: str,
        variables: dict[str, Any] | None = None,
        *,
        job_id: int | None = None,
        urgent: bool = False,
    ) -> Completion:
        version = self._resolve(task).params.prompt_version
        rendered = self.prompts.render(prompt_name, variables, version)
        return self.complete(
            task, rendered.messages, job_id=job_id, urgent=urgent, prompt_ref=rendered.ref
        )

    def structured(
        self,
        task: LLMTask,
        prompt_name: str,
        variables: dict[str, Any] | None,
        schema: type[T],
        *,
        job_id: int | None = None,
        urgent: bool = False,
        max_repairs: int = 2,
    ) -> StructuredResult[T]:
        """Render a prompt, call the task's model, and return a validated `schema` instance.

        Uses prompt-level JSON instructions (portable across every provider) and, when the
        output doesn't parse or validate, feeds the errors back for up to `max_repairs`
        repair attempts.
        """
        version = self._resolve(task).params.prompt_version
        rendered = self.prompts.render(prompt_name, variables, version)
        instructions = self.prompts.render(
            "structured_output",
            {"schema_json": json.dumps(schema.model_json_schema(), indent=2)},
        )
        messages = _merge_system(instructions.messages, rendered.messages)

        completions: list[Completion] = []
        last_error = ""
        for _ in range(max_repairs + 1):
            c = self.complete(task, messages, job_id=job_id, urgent=urgent, prompt_ref=rendered.ref)
            completions.append(c)
            try:
                value = schema.model_validate(extract_json(c.text, want_object=True))
                return StructuredResult(value=value, completions=completions)
            except (ValueError, ValidationError) as exc:
                last_error = _format_validation_error(exc)
                log.info("task %s: invalid structured output, asking for repair", task)
                repair = self.prompts.render("structured_repair", {"errors": last_error})
                messages = [*messages, {"role": "assistant", "content": c.text}, *repair.messages]
        raise StructuredOutputError(
            f"task {task}: no valid {schema.__name__} after {max_repairs + 1} attempts: "
            f"{last_error}"
        )

    def web_search(self, query: str, *, job_id: int | None = None) -> list[WebResult]:
        """Web search via Gemini's Google Search grounding (no separate search API key).

        Uses the Gemini provider already assigned to a task (preferring the portal helper's),
        recorded as a portal_helper call with prompt_ref "web_search".
        """
        route = self._search_route()
        self._check_budget(urgent=False)

        def call(t: Target) -> RawSearch:
            return self.backend.web_search(t, query, timeout_s=route.params.timeout_s)

        raw, _, _, _ = self._run(LLMTask.PORTAL_HELPER, route, call, job_id, "web_search")
        return raw.results

    def _search_route(self) -> _Route:
        with self._sessions() as db:
            configs = {c.task: c for c in db.scalars(select(LLMTaskConfig))}
            order = [LLMTask.PORTAL_HELPER, LLMTask.JD_ANALYZER, *LLMTask]
            for task in order:
                cfg = configs.get(task)
                if task is LLMTask.EMBEDDING or cfg is None or not cfg.model:
                    continue
                for prov, model in (
                    (cfg.provider, cfg.model),
                    (cfg.fallback_provider, cfg.fallback_model),
                ):
                    if (
                        prov is not None
                        and prov.enabled
                        and model
                        and prov.kind is LLMProviderKind.GEMINI
                    ):
                        return _Route(
                            params=TASK_DEFAULTS[LLMTask.PORTAL_HELPER],
                            targets=[(_target(prov, model), False)],
                        )
            prov = db.scalar(
                select(LLMProvider).where(
                    LLMProvider.kind == LLMProviderKind.GEMINI, LLMProvider.enabled.is_(True)
                )
            )
            if prov is not None:
                return _Route(
                    params=TASK_DEFAULTS[LLMTask.PORTAL_HELPER],
                    targets=[(_target(prov, "gemini-flash-latest"), False)],
                )
        raise LLMNotConfiguredError(
            "web search uses Gemini's Google Search: add a Gemini provider in Settings → LLM"
        )

    def embed(self, texts: list[str], *, job_id: int | None = None) -> EmbeddingResult:
        """Embed texts with the configured embedding model.

        No fallback here on purpose: a different model would produce incompatible vectors.
        """
        route = self._resolve(LLMTask.EMBEDDING)
        self._check_budget(urgent=False)
        target = route.targets[0][0]
        vectors: list[list[float]] = []
        cost = Decimal(0)
        for i in range(0, len(texts), EMBED_BATCH):
            batch = texts[i : i + EMBED_BATCH]

            def call(t: Target, batch: list[str] = batch) -> RawEmbedding:
                return self.backend.embed(t, batch, timeout_s=route.params.timeout_s)

            raw, _, _, _ = self._run(LLMTask.EMBEDDING, route, call, job_id, None)
            if len(raw.vectors) != len(batch):
                raise LLMCallFailedError("embedding count mismatch", [])
            vectors.extend(raw.vectors)
            cost += raw.cost_usd
        dims = {len(v) for v in vectors}
        if len(dims) > 1:
            raise LLMCallFailedError(f"inconsistent embedding dimensions {dims}", [])
        return EmbeddingResult(
            model_key=embedding_model_key(target.kind, target.model),
            dimension=dims.pop() if dims else 0,
            vectors=vectors,
            cost_usd=cost,
        )

    def active_embedding_key(self) -> str | None:
        with self._sessions() as db:
            cfg = db.get(LLMTaskConfig, LLMTask.EMBEDDING)
            if cfg is None or cfg.provider is None or not cfg.model:
                return None
            return embedding_model_key(cfg.provider.kind, cfg.model)

    def test_provider(self, provider_id: int, model: str | None = None) -> TestResult:
        """Make a tiny call and record the outcome on the provider row."""
        with self._sessions() as db:
            provider = db.get(LLMProvider, provider_id)
            if provider is None:
                raise LookupError(f"provider {provider_id} not found")
            spec = spec_for(provider.kind)
            chosen = model or next(
                iter(spec.suggested_models or spec.suggested_embedding_models), None
            )
            if not chosen:
                return TestResult(
                    ok=False, latency_ms=None, model=None, error="choose a model to test with"
                )
            target = _target(provider, chosen)

        rendered = self.prompts.render("connection_test")
        start = self._clock()
        try:
            if spec.supports_chat:
                raw_c = self.backend.complete(
                    target, rendered.messages, temperature=0, max_tokens=5, timeout_s=20
                )
                reply, tin, tout, cost = (
                    raw_c.text.strip()[:50],
                    raw_c.tokens_in,
                    raw_c.tokens_out,
                    raw_c.cost_usd,
                )
            else:
                raw_e = self.backend.embed(target, ["connection test"], timeout_s=20)
                reply = f"embedding dimension {len(raw_e.vectors[0])}"
                tin, tout, cost = raw_e.tokens_in, 0, raw_e.cost_usd
            result = TestResult(
                ok=True, latency_ms=self._elapsed_ms(start), model=chosen, reply=reply
            )
            self._record(
                None, target, None, rendered.ref, tin, tout, cost, result.latency_ms, None, False, 1
            )
        except LLMProviderError as exc:
            hint = {
                ErrorKind.NOT_FOUND: f"Model '{chosen}' isn't available to this key; "
                "pick another model from the list. ",
                ErrorKind.AUTH: "The provider rejected this API key. ",
                ErrorKind.QUOTA: f"Your plan has no quota for '{chosen}' (e.g. a free tier that "
                "excludes this model). Pick a cheaper model or enable billing. ",
            }.get(exc.kind, "")
            result = TestResult(
                ok=False,
                latency_ms=self._elapsed_ms(start),
                model=chosen,
                error=hint + str(exc),
                error_kind=exc.kind.value,
            )
            self._record(
                None, target, None, rendered.ref, 0, 0, Decimal(0), result.latency_ms, exc, False, 1
            )

        with self._sessions() as db:
            provider = db.get(LLMProvider, provider_id)
            if provider is not None:
                provider.last_test_ok = result.ok
                provider.last_test_at = datetime.now(UTC)
                provider.last_test_latency_ms = result.latency_ms
                provider.last_test_error = result.error
                db.commit()
        return result

    # -- routing ------------------------------------------------------------------------

    def _resolve(self, task: LLMTask) -> _Route:
        with self._sessions() as db:
            cfg = db.get(LLMTaskConfig, task)
            if cfg is None or cfg.provider_id is None or not cfg.model:
                raise LLMNotConfiguredError(
                    f"no model assigned to task '{task}'. Set it in Settings → LLM."
                )
            params = TaskParams.model_validate(
                {**TASK_DEFAULTS[task].model_dump(), **(cfg.params or {})}
            )
            targets: list[tuple[Target, bool]] = []
            if cfg.provider is not None and cfg.provider.enabled:
                targets.append((_target(cfg.provider, cfg.model), False))
            if (
                task is not LLMTask.EMBEDDING
                and cfg.fallback_provider is not None
                and cfg.fallback_provider.enabled
                and cfg.fallback_model
            ):
                targets.append((_target(cfg.fallback_provider, cfg.fallback_model), True))
            if not targets:
                raise LLMNotConfiguredError(
                    f"the provider(s) for task '{task}' are disabled. Enable one in Settings → LLM."
                )
            return _Route(params=params, targets=targets)

    def _run(
        self,
        task: LLMTask,
        route: _Route,
        call: Callable[[Target], R],
        job_id: int | None,
        prompt_ref: str | None,
    ) -> tuple[R, Target, bool, int]:
        """Primary → (retry once if transient) → fallback → (retry once if transient)."""
        errors: list[LLMProviderError] = []
        for idx, (target, is_fb) in enumerate(route.targets):
            if idx > 0:
                log.warning(
                    "task %s: primary %s:%s failed (%s); switching to fallback %s:%s",
                    task,
                    route.targets[0][0].kind,
                    route.targets[0][0].model,
                    errors[-1].kind if errors else "?",
                    target.kind,
                    target.model,
                )
            for attempt in (1, 2):
                start = self._clock()
                try:
                    raw = call(target)
                except LLMProviderError as exc:
                    errors.append(exc)
                    self._record(
                        task,
                        target,
                        job_id,
                        prompt_ref,
                        0,
                        0,
                        Decimal(0),
                        self._elapsed_ms(start),
                        exc,
                        is_fb,
                        attempt,
                    )
                    if attempt == 1 and exc.retryable:
                        self._sleep(_backoff(exc))
                        continue
                    break
                latency = self._elapsed_ms(start)
                tokens_out = raw.tokens_out if isinstance(raw, RawCompletion) else 0
                self._record(
                    task,
                    target,
                    job_id,
                    prompt_ref,
                    raw.tokens_in,
                    tokens_out,
                    raw.cost_usd,
                    latency,
                    None,
                    is_fb,
                    attempt,
                )
                return raw, target, is_fb, latency

        summary = "; ".join(f"{e.kind}: {_short(str(e))}" for e in errors[-2:])
        failure = LLMCallFailedError(
            f"task '{task}' failed on all configured models. {summary}", errors
        )
        if failure.config_problem:
            self._alert_config_problem(task, route, errors)
        raise failure

    def _alert_config_problem(
        self, task: LLMTask, route: _Route, errors: list[LLMProviderError]
    ) -> None:
        models = ", ".join(f"{t.kind}:{t.model}" for t, _ in route.targets)
        reasons = {
            ErrorKind.NOT_FOUND: "the model isn't available to your key (retired or renamed)",
            ErrorKind.AUTH: "the provider rejected the API key",
            ErrorKind.QUOTA: "your plan has no quota for this model",
        }
        why = "; ".join(sorted({reasons[e.kind] for e in errors if e.kind in reasons}))
        try:
            with self._sessions() as db:
                notify(
                    db,
                    NotificationLevel.ERROR,
                    "llm.config",
                    f"Task '{task}' can't run: {why}",
                    f"Configured: {models}. Pick another model for this task in Settings → LLM "
                    "(models ending in -latest are kept current by the provider).",
                    dedupe_key=f"llm.config:{task}:{models}",
                )
        except Exception:
            log.exception("failed to record config-problem notification")

    # -- budget -------------------------------------------------------------------------

    def _check_budget(self, urgent: bool) -> None:
        with self._sessions() as db:
            settings = get_app_settings(db)
            if settings.monthly_budget_usd is None:
                return
            since = month_start(settings.timezone)
            spent = spend_since(db, since)
            budget = Decimal(str(settings.monthly_budget_usd))
            if spent < budget:
                return
            notify(
                db,
                NotificationLevel.WARNING,
                "budget.exceeded",
                f"Monthly LLM budget reached (${spent:.2f} of ${budget:.2f})",
                "Non-urgent jobs are paused until next month or until you raise the budget "
                "in Settings → LLM.",
                dedupe_key=f"budget.exceeded:{since:%Y-%m}:{budget}",
            )
        if not urgent:
            raise BudgetExceededError(
                f"monthly LLM budget of ${budget:.2f} reached (spent ${spent:.2f})"
            )
        log.warning("budget exceeded but call is urgent; proceeding")

    # -- accounting ---------------------------------------------------------------------

    def _record(
        self,
        task: LLMTask | None,
        target: Target,
        job_id: int | None,
        prompt_ref: str | None,
        tokens_in: int,
        tokens_out: int,
        cost: Decimal,
        latency_ms: int | None,
        error: LLMProviderError | None,
        is_fallback: bool,
        attempt: int,
    ) -> None:
        # Own session + commit: usage must be recorded even if the caller rolls back.
        try:
            with self._sessions() as db:
                db.add(
                    LLMUsage(
                        job_id=job_id,
                        task=task,
                        provider_id=target.provider_id,
                        prompt_ref=prompt_ref,
                        provider_kind=target.kind,
                        model=target.model,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        cost_usd=cost,
                        latency_ms=latency_ms,
                        success=error is None,
                        is_fallback=is_fallback,
                        attempt=attempt,
                        error_kind=error.kind.value if error else None,
                        error=redact(str(error))[:2000] if error else None,
                    )
                )
                db.commit()
        except Exception:
            log.exception("failed to record LLM usage")

    def _elapsed_ms(self, start: float) -> int:
        return int((self._clock() - start) * 1000)


# -- helpers ----------------------------------------------------------------------------


def _target(provider: LLMProvider, model: str) -> Target:
    return Target(
        provider_id=provider.id,
        kind=provider.kind,
        model=model,
        api_key=provider.api_key,
        base_url=provider.base_url,
    )


_JSON_MESSAGE_RE = re.compile(r'"message"\s*:\s*"([^"]{1,300})"')


def _short(message: str) -> str:
    """One readable line from a provider error (which often embeds a JSON blob)."""
    m = _JSON_MESSAGE_RE.search(message)
    head = message.split("{", 1)[0].strip().rstrip("-: ")
    text = f"{head} - {m.group(1)}" if m else " ".join(message.split())
    return text[:300]


def _backoff(exc: LLMProviderError) -> float:
    if exc.retry_after_s is not None:
        return min(max(exc.retry_after_s, 0.5), MAX_BACKOFF_S)
    base = 4.0 if exc.kind is ErrorKind.RATE_LIMIT else 1.5
    return min(base + random.uniform(0, 1.0), MAX_BACKOFF_S)  # noqa: S311 — jitter, not crypto


def _merge_system(first: list[Message], second: list[Message]) -> list[Message]:
    """Combine system messages into one leading system message (portable across providers)."""
    system = [m["content"] for m in (*second, *first) if m["role"] == "system"]
    rest = [m for m in (*first, *second) if m["role"] != "system"]
    merged: list[Message] = [{"role": "system", "content": "\n\n".join(system)}] if system else []
    return merged + rest


def extract_json(text: str, *, want_object: bool = False) -> Any:
    """Parse JSON from a model response, tolerating code fences and surrounding prose.

    With `want_object` (every structured schema is an object), the largest top-level JSON
    object wins, and a one-element list wrapping an object is unwrapped. That stops stray
    bracketed fragments in prose or LaTeX (e.g. "[0.9]") being mistaken for the answer.
    """
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else ""
        s = s.rsplit("```", 1)[0]
    if want_object:
        s = fix_latex_escapes(s)
    decoder = json.JSONDecoder(strict=False)  # models sometimes put raw newlines in strings
    found: list[Any] = []
    i = 0
    while i < len(s):
        if s[i] in "{[":
            try:
                value, end = decoder.raw_decode(s, i)
            except json.JSONDecodeError:
                i += 1
                continue
            if not want_object:
                return value
            if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
                value = value[0]
            if isinstance(value, dict):
                found.append((end - i, value))
            i = end
            continue
        i += 1
    if found:
        return max(found, key=lambda t: t[0])[1]
    raise ValueError("response did not contain a JSON object")


_ESCAPE_RE = re.compile(r"\\\\|\\(.)", re.DOTALL)


def fix_latex_escapes(s: str) -> str:
    """Double the single backslashes of LaTeX commands inside model-written JSON.

    Models often write "\\%" or "\\textbf" in JSON strings with ONE backslash. The first
    is an invalid escape (the whole reply fails to parse); the second parses silently as a
    TAB + "extbf". Properly escaped pairs and real escapes (\\n, \\", \\uXXXX) are kept;
    \\t \\r \\b \\f followed by a letter are treated as LaTeX (tabs/form feeds are never
    intended in structured output).
    """

    def fix(m: re.Match[str]) -> str:
        c = m.group(1)
        if c is None:  # an escaped backslash pair
            return m.group(0)
        nxt = s[m.end() : m.end() + 1]
        if c in '"\\/n':
            return m.group(0)
        if c == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", s[m.end() : m.end() + 4]):
            return m.group(0)
        if c in "bfrt" and not nxt.isalpha():
            return m.group(0)
        return "\\\\" + c

    return _ESCAPE_RE.sub(fix, s)


def _format_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        lines = []
        for err in exc.errors()[:20]:
            loc = ".".join(str(p) for p in err["loc"]) or "(root)"
            lines.append(f"- {loc}: {err['msg']}")
        return "\n".join(lines)
    return f"- {exc}"
