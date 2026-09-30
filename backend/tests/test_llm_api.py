from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.llm.errors import ErrorKind, LLMProviderError
from app.models import Embedding
from app.models.enums import LLMProviderKind
from tests.fakes import FakeBackend

KEY = "sk-ant-api03-SUPERSECRETVALUE-a1b2"


def _add(client: TestClient, **kw) -> dict:  # type: ignore[no-untyped-def]
    body = {"label": "Claude", "kind": "anthropic", "api_key": KEY} | kw
    r = client.post("/api/llm/providers", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_catalog_lists_every_provider(client: TestClient) -> None:
    kinds = {c["kind"] for c in client.get("/api/llm/catalog").json()}
    assert kinds == {k.value for k in LLMProviderKind}


def test_create_masks_key_everywhere(client: TestClient, engine: Engine) -> None:
    p = _add(client)
    assert p["api_key_masked"] == "sk-...a1b2" and p["has_api_key"] is True
    for path in ("/api/llm/providers", f"/api/llm/providers/{p['id']}/models", "/api/llm/tasks"):
        assert "SUPERSECRET" not in client.get(path).text
    with engine.connect() as c:
        raw = c.execute(text("SELECT encrypted_api_key FROM llm_providers")).scalar_one()
        audits = " ".join(
            str(d) for d in c.execute(text("SELECT details FROM audit_logs")).scalars()
        )
    assert "SUPERSECRET" not in raw and "SUPERSECRET" not in audits


def test_create_validation(client: TestClient) -> None:
    r = client.post("/api/llm/providers", json={"label": "x", "kind": "anthropic"})
    assert r.status_code == 422 and "API key" in r.text
    r = client.post("/api/llm/providers", json={"label": "x", "kind": "openai_compatible"})
    assert r.status_code == 422 and "base URL" in r.text
    r = client.post(
        "/api/llm/providers", json={"label": "x", "kind": "ollama", "base_url": "ftp://x"}
    )
    assert r.status_code == 422
    ollama = client.post("/api/llm/providers", json={"label": "local", "kind": "ollama"}).json()
    assert ollama["base_url"] == "http://host.docker.internal:11434" and not ollama["has_api_key"]


def test_update_keeps_key_unless_replaced(client: TestClient) -> None:
    p = _add(client)
    r = client.patch(f"/api/llm/providers/{p['id']}", json={"label": "Renamed", "enabled": False})
    assert r.json()["api_key_masked"] == "sk-...a1b2" and r.json()["enabled"] is False
    r = client.patch(f"/api/llm/providers/{p['id']}", json={"api_key": "sk-ant-new-key-zzzz9999"})
    assert r.json()["api_key_masked"] == "sk-...9999"
    assert client.patch("/api/llm/providers/999", json={"label": "x"}).status_code == 404


def test_task_assignment_and_delete_guard(client: TestClient) -> None:
    a = _add(client)
    b = _add(client, label="OpenAI", kind="openai", api_key="sk-openai-xxxxxxxx")
    r = client.put(
        "/api/llm/tasks/resume_writer",
        json={
            "provider_id": a["id"],
            "model": "claude-opus-5-5",
            "fallback_provider_id": b["id"],
            "fallback_model": "gpt-4.1",
            "params": {"temperature": 0.5},
        },
    )
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]
    assert cfg["configured"] and cfg["params"]["temperature"] == 0.5
    assert cfg["params"]["max_tokens"] == 4096  # default kept

    tasks = {t["task"]: t for t in client.get("/api/llm/tasks").json()}
    assert tasks["resume_writer"]["fallback_model"] == "gpt-4.1"
    assert not tasks["email_classifier"]["configured"]

    r = client.delete(f"/api/llm/providers/{b['id']}")
    assert r.status_code == 409 and "resume_writer" in r.text
    assert client.get("/api/llm/providers").json()[0]["used_by_tasks"] == ["resume_writer"]


def test_task_validation(client: TestClient) -> None:
    voyage = _add(client, label="V", kind="voyage", api_key="pa-voyagekey-123456")
    chat = _add(client)
    bad = client.put(
        "/api/llm/tasks/jd_analyzer", json={"provider_id": voyage["id"], "model": "voyage-3.5"}
    )
    assert bad.status_code == 422 and "embeddings-only" in bad.text
    bad = client.put("/api/llm/tasks/embedding", json={"provider_id": chat["id"], "model": "x"})
    assert bad.status_code == 422 and "does not support embeddings" in bad.text
    bad = client.put("/api/llm/tasks/jd_analyzer", json={"provider_id": chat["id"], "model": None})
    assert bad.status_code == 422
    bad = client.put(
        "/api/llm/tasks/jd_analyzer",
        json={"provider_id": chat["id"], "model": "m", "params": {"temperature": 5}},
    )
    assert bad.status_code == 422


def test_embedding_change_requires_confirmation(client: TestClient, db: Session) -> None:
    o = _add(client, label="OpenAI", kind="openai", api_key="sk-openai-xxxxxxxx")
    first = {"provider_id": o["id"], "model": "text-embedding-3-small"}
    assert client.put("/api/llm/tasks/embedding", json=first).json()["reindex_scheduled"] is False

    db.add(
        Embedding(
            owner_type="bullet",
            owner_id=1,
            content="x",
            model_name="openai:text-embedding-3-small",
            dimension=4,
            vector=[0, 0, 0, 1],
        )
    )
    db.commit()

    change = {"provider_id": o["id"], "model": "text-embedding-3-large"}
    r = client.put("/api/llm/tasks/embedding", json=change)
    assert r.status_code == 409 and "re-embeds" in r.text
    r = client.put("/api/llm/tasks/embedding", json=change | {"confirm_reindex": True})
    assert r.status_code == 200 and r.json()["reindex_scheduled"] is True

    fb = client.put(
        "/api/llm/tasks/embedding",
        json=change | {"fallback_provider_id": o["id"], "fallback_model": "text-embedding-3-small"},
    )
    assert fb.status_code == 422 and "fallback" in fb.text


def test_models_live_then_suggested_on_error(client: TestClient, backend: FakeBackend) -> None:
    p = _add(client)
    backend.models[LLMProviderKind.ANTHROPIC] = ["claude-a", "claude-b"]
    assert client.get(f"/api/llm/providers/{p['id']}/models").json() == {
        "models": ["claude-a", "claude-b"],
        "source": "live",
        "error": None,
    }

    q = _add(client, label="G", kind="groq", api_key="gsk_abcdefghijklmnopqrst")
    backend.models[LLMProviderKind.GROQ] = LLMProviderError(ErrorKind.AUTH, "401 invalid")
    r = client.get(f"/api/llm/providers/{q['id']}/models").json()
    assert r["source"] == "suggested" and r["models"] and "401" in r["error"]


def test_connection_test_endpoint(client: TestClient, backend: FakeBackend) -> None:
    p = _add(client)
    r = client.post(
        f"/api/llm/providers/{p['id']}/test", json={"model": "claude-haiku-4-5-20251001"}
    )
    assert r.status_code == 200 and r.json()["ok"] is True and r.json()["reply"] == "OK"
    listed = client.get("/api/llm/providers").json()[0]
    assert listed["last_test_ok"] is True and listed["last_test_latency_ms"] is not None
    assert client.post("/api/llm/providers/999/test", json={}).status_code == 404


def test_usage_summary(client: TestClient, backend: FakeBackend) -> None:
    a = _add(client)
    client.put("/api/llm/tasks/jd_analyzer", json={"provider_id": a["id"], "model": "m"})
    client.patch("/api/settings", json={"monthly_budget_usd": 5})
    client.post(f"/api/llm/providers/{a['id']}/test", json={"model": "m"})
    u = client.get("/api/llm/usage").json()
    assert u["budget_usd"] == 5 and u["budget_exceeded"] is False
    assert u["month_to_date_usd"] > 0 and len(u["recent"]) == 1
    assert u["by_task"][0]["key"] == "connection_test"


def test_notifications_api(client: TestClient, db: Session) -> None:
    from app.models.enums import NotificationLevel
    from app.services.notifications import notify

    notify(db, NotificationLevel.WARNING, "x.test", "Hello", dedupe_key="k1")
    assert notify(db, NotificationLevel.WARNING, "x.test", "Hello again", dedupe_key="k1") is None
    items = client.get("/api/notifications?unread_only=true").json()
    assert [n["title"] for n in items] == ["Hello"]
    assert client.post(f"/api/notifications/{items[0]['id']}/read").status_code == 204
    assert client.get("/api/notifications?unread_only=true").json() == []
