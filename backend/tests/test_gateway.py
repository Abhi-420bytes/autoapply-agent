from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.llm.errors import (
    BudgetExceededError,
    ErrorKind,
    LLMCallFailedError,
    LLMNotConfiguredError,
    LLMProviderError,
    StructuredOutputError,
)
from app.llm.gateway import LLMGateway, extract_json
from app.models import LLMProvider, LLMTaskConfig, LLMUsage, Notification
from app.models.enums import LLMProviderKind, LLMTask
from app.schemas.settings import AppSettingsPatch
from app.services.settings_service import update_app_settings
from tests.fakes import FakeBackend

MSG = [{"role": "user", "content": "hi"}]


def _setup(db: Session, *, fallback: bool = True, primary_enabled: bool = True) -> None:
    a = LLMProvider(
        label="A",
        kind=LLMProviderKind.ANTHROPIC,
        api_key="sk-ant-primary-key-0001",
        enabled=primary_enabled,
    )
    b = LLMProvider(label="B", kind=LLMProviderKind.OPENAI, api_key="sk-openai-fallback-0002")
    db.add_all([a, b])
    db.flush()
    db.add(
        LLMTaskConfig(
            task=LLMTask.JD_ANALYZER,
            provider_id=a.id,
            model="primary-model",
            fallback_provider_id=b.id if fallback else None,
            fallback_model="fallback-model" if fallback else None,
            params={},
        )
    )
    db.commit()


def _usage(db: Session) -> list[LLMUsage]:
    db.expire_all()
    return db.query(LLMUsage).order_by(LLMUsage.id).all()


def test_not_configured(gateway: LLMGateway) -> None:
    with pytest.raises(LLMNotConfiguredError):
        gateway.complete(LLMTask.RESUME_WRITER, MSG)


def test_routes_to_configured_model_and_records_usage(
    gateway: LLMGateway, backend: FakeBackend, db: Session
) -> None:
    _setup(db)
    backend.script("primary-model", "hello")
    c = gateway.complete(LLMTask.JD_ANALYZER, MSG, job_id=None)
    assert c.text == "hello" and c.model == "primary-model" and not c.is_fallback
    target = backend.calls[0][0]
    assert target.api_key == "sk-ant-primary-key-0001"
    assert "sk-ant" not in repr(target)  # repr never leaks the key
    [u] = _usage(db)
    assert (u.task, u.tokens_in, u.tokens_out, u.success) == (LLMTask.JD_ANALYZER, 100, 20, True)
    assert u.cost_usd == Decimal("0.001")


def test_transient_error_retries_once_then_succeeds(
    gateway: LLMGateway, backend: FakeBackend, db: Session, sleeps: list[float]
) -> None:
    _setup(db)
    backend.script("primary-model", LLMProviderError(ErrorKind.RATE_LIMIT, "429"), "ok")
    c = gateway.complete(LLMTask.JD_ANALYZER, MSG)
    assert c.text == "ok" and not c.is_fallback
    assert len(sleeps) == 1 and sleeps[0] > 0
    assert [(u.success, u.attempt) for u in _usage(db)] == [(False, 1), (True, 2)]


def test_retry_after_header_is_respected(
    gateway: LLMGateway, backend: FakeBackend, db: Session, sleeps: list[float]
) -> None:
    _setup(db)
    backend.script("primary-model", LLMProviderError(ErrorKind.RATE_LIMIT, "429", 7.0), "ok")
    gateway.complete(LLMTask.JD_ANALYZER, MSG)
    assert sleeps == [7.0]


def test_persistent_outage_switches_to_fallback(
    gateway: LLMGateway, backend: FakeBackend, db: Session
) -> None:
    _setup(db)
    err = LLMProviderError(ErrorKind.UNAVAILABLE, "503")
    backend.script("primary-model", err, err)
    backend.script("fallback-model", "from fallback")
    c = gateway.complete(LLMTask.JD_ANALYZER, MSG)
    assert c.text == "from fallback" and c.is_fallback and c.provider_kind == "openai"
    assert [(u.model, u.success, u.is_fallback) for u in _usage(db)] == [
        ("primary-model", False, False),
        ("primary-model", False, False),
        ("fallback-model", True, True),
    ]


def test_auth_error_skips_retry_and_goes_to_fallback(
    gateway: LLMGateway, backend: FakeBackend, db: Session, sleeps: list[float]
) -> None:
    _setup(db)
    backend.script("primary-model", LLMProviderError(ErrorKind.AUTH, "invalid key"))
    backend.script("fallback-model", "fb")
    assert gateway.complete(LLMTask.JD_ANALYZER, MSG).is_fallback
    assert sleeps == []
    assert len(backend.calls) == 2


def test_all_fail_raises_with_redacted_message(
    gateway: LLMGateway, backend: FakeBackend, db: Session
) -> None:
    _setup(db, fallback=False)
    leak = LLMProviderError(ErrorKind.AUTH, "Incorrect API key sk-ant-primary-key-0001")
    backend.script("primary-model", leak)
    with pytest.raises(LLMCallFailedError) as exc:
        gateway.complete(LLMTask.JD_ANALYZER, MSG)
    assert "primary-key-0001" not in str(exc.value)
    assert all("primary-key-0001" not in (u.error or "") for u in _usage(db))


def test_disabled_primary_uses_fallback(
    gateway: LLMGateway, backend: FakeBackend, db: Session
) -> None:
    _setup(db, primary_enabled=False)
    c = gateway.complete(LLMTask.JD_ANALYZER, MSG)
    assert c.is_fallback and backend.calls[0][0].model == "fallback-model"


class JD(BaseModel):
    role: str
    required_skills: list[str]


def test_structured_valid_first_try(gateway: LLMGateway, backend: FakeBackend, db: Session) -> None:
    _setup(db)
    backend.script("primary-model", '```json\n{"role": "SDE", "required_skills": ["Python"]}\n```')
    r = gateway.structured(LLMTask.JD_ANALYZER, "connection_test", {}, JD)
    assert r.value == JD(role="SDE", required_skills=["Python"])
    system = backend.calls[0][1][0]
    assert system["role"] == "system" and '"required_skills"' in system["content"]
    assert r.models_used == ["anthropic:primary-model"]


def test_structured_repairs_invalid_output(
    gateway: LLMGateway, backend: FakeBackend, db: Session
) -> None:
    _setup(db)
    backend.script(
        "primary-model",
        "Sure! here you go",
        '{"role": "SDE"}',
        'Result: {"role": "SDE", "required_skills": []} hope that helps',
    )
    r = gateway.structured(LLMTask.JD_ANALYZER, "connection_test", {}, JD)
    assert r.value.role == "SDE"
    assert len(r.completions) == 3 and r.cost_usd == Decimal("0.003")
    last_msgs = backend.calls[2][1]
    assert last_msgs[-1]["role"] == "user" and "required_skills" in last_msgs[-1]["content"]
    assert last_msgs[-2] == {"role": "assistant", "content": '{"role": "SDE"}'}


def test_structured_gives_up(gateway: LLMGateway, backend: FakeBackend, db: Session) -> None:
    _setup(db)
    backend.script("primary-model", "no", "still no", "nope")
    with pytest.raises(StructuredOutputError):
        gateway.structured(LLMTask.JD_ANALYZER, "connection_test", {}, JD, max_repairs=2)


def test_extract_json_variants() -> None:
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('text {"a": {"b": [1]}} trailing') == {"a": {"b": [1]}}
    assert extract_json("```\n[1, 2]\n```") == [1, 2]
    with pytest.raises(ValueError):
        extract_json("no json here {")


def test_budget_blocks_non_urgent_and_alerts_once(
    gateway: LLMGateway, backend: FakeBackend, db: Session
) -> None:
    _setup(db)
    update_app_settings(db, AppSettingsPatch(monthly_budget_usd=0.0015))
    gateway.complete(LLMTask.JD_ANALYZER, MSG)  # spends 0.001
    gateway.complete(LLMTask.JD_ANALYZER, MSG)  # spends 0.002 total
    with pytest.raises(BudgetExceededError):
        gateway.complete(LLMTask.JD_ANALYZER, MSG)
    with pytest.raises(BudgetExceededError):
        gateway.complete(LLMTask.JD_ANALYZER, MSG)
    assert gateway.complete(LLMTask.JD_ANALYZER, MSG, urgent=True).text == "OK"
    db.expire_all()
    assert db.query(Notification).filter_by(kind="budget.exceeded").count() == 1


def _embed_setup(db: Session) -> None:
    p = LLMProvider(label="O", kind=LLMProviderKind.OPENAI, api_key="sk-openai-emb-000000")
    db.add(p)
    db.flush()
    db.add(
        LLMTaskConfig(
            task=LLMTask.EMBEDDING, provider_id=p.id, model="text-embedding-3-small", params={}
        )
    )
    db.commit()


def test_embed_batches_and_reports_model_key(
    gateway: LLMGateway, backend: FakeBackend, db: Session
) -> None:
    _embed_setup(db)
    texts = [f"t{i}" for i in range(150)]
    r = gateway.embed(texts)
    assert r.model_key == "openai:text-embedding-3-small"
    assert r.dimension == backend.dim and len(r.vectors) == 150
    assert [len(b) for _, b in backend.embed_calls] == [64, 64, 22]


def test_test_provider_success_and_failure(
    gateway: LLMGateway, backend: FakeBackend, db: Session
) -> None:
    _setup(db)
    pid = db.query(LLMProvider).filter_by(label="A").one().id
    ok = gateway.test_provider(pid, "primary-model")
    assert ok.ok and ok.reply == "OK" and ok.latency_ms is not None
    backend.script("primary-model", LLMProviderError(ErrorKind.AUTH, "bad key"))
    bad = gateway.test_provider(pid, "primary-model")
    assert not bad.ok and bad.error_kind == "auth" and "rejected this API key" in (bad.error or "")
    backend.script("primary-model", LLMProviderError(ErrorKind.NOT_FOUND, "404 retired"))
    gone = gateway.test_provider(pid, "primary-model")
    assert "isn't available to this key" in (gone.error or "")
    db.expire_all()
    p = db.get(LLMProvider, pid)
    assert p is not None and p.last_test_ok is False and "404 retired" in (p.last_test_error or "")
    assert {u.task for u in _usage(db)} == {None}  # connection tests aren't task usage


def test_quota_error_skips_retry_and_uses_fallback(
    gateway: LLMGateway, backend: FakeBackend, db: Session, sleeps: list[float]
) -> None:
    _setup(db)
    backend.script("primary-model", LLMProviderError(ErrorKind.QUOTA, "limit: 0"))
    assert gateway.complete(LLMTask.JD_ANALYZER, MSG).is_fallback
    assert sleeps == [] and len(backend.calls) == 2


def test_extract_json_prefers_the_object() -> None:
    assert extract_json('Priority [0.9] then {"a": 1}', want_object=True) == {"a": 1}
    assert extract_json('[{"regions": {}}]', want_object=True) == {"regions": {}}
    assert extract_json(
        '{"x": 1} and the real one {"regions": {"A": "b"}, "notes": "longer"}', want_object=True
    ) == {"regions": {"A": "b"}, "notes": "longer"}
    with pytest.raises(ValueError):
        extract_json("[1, 2]", want_object=True)
