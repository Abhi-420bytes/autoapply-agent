from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.knowledge.bank import chunk_text, embed_missing
from app.llm.gateway import LLMGateway
from app.models import Bullet, Embedding
from app.models.enums import BulletSource
from tests.test_knowledge_parsing import JAKE

pytestmark = pytest.mark.usefixtures("llm_ready")


def upload(client: TestClient, name: str, data: bytes) -> dict:  # type: ignore[type-arg]
    r = client.post(
        "/api/knowledge/resumes",
        files=[("files", (name, data))],
        data={"source_date": "2025-06-01"},
    )
    assert r.status_code == 200, r.text
    return r.json()[0]  # type: ignore[no-any-return]


def test_upload_tex_resume_builds_bullet_bank(client: TestClient, engine: Engine) -> None:
    rep = upload(client, "old.tex", JAKE.encode())
    assert (rep["found"], rep["added"], rep["exact_duplicates"]) == (3, 3, 0)
    bullets = client.get("/api/knowledge/bullets").json()
    fastapi_bullet = next(b for b in bullets if "FastAPI" in b["text"])
    assert fastapi_bullet["skills"] == ["FastAPI"] and fastapi_bullet["section"] == "Experience"
    assert fastapi_bullet["source_date"] == "2025-06-01"
    assert next(b for b in bullets if "Rust" in b["text"])["skills"] == ["Rust"]
    with engine.connect() as c:
        assert (
            c.execute(text("SELECT count(*) FROM embeddings WHERE owner_type='bullet'")).scalar()
            == 3
        )

    status = client.get("/api/knowledge/status").json()
    assert status["bullets_by_source"] == {"past_resume": 3}
    assert status["embedding_model"] == "openai:emb" and status["unembedded_items"] == 0


def test_duplicates_exact_and_near(client: TestClient) -> None:
    upload(client, "a.tex", JAKE.encode())
    again = upload(client, "b.tex", JAKE.encode())
    assert again["exact_duplicates"] == 3 and again["added"] == 0
    reworded = JAKE.replace(
        "Migrated CI to GitHub Actions, cutting build time by 35\\%",
        "Migrated the CI to GitHub Actions, cutting build time by 35\\%",
    )
    near = upload(client, "c.tex", reworded.encode())
    assert near["near_duplicates"] == 1 and near["added"] == 0


def test_upload_rejects_bad_files(client: TestClient) -> None:
    r = client.post("/api/knowledge/resumes", files=[("files", ("x.docx", b"x"))])
    assert r.status_code == 422
    r = client.post("/api/knowledge/resumes", files=[("files", ("x.pdf", b"not a pdf"))])
    assert r.status_code == 422


def test_delete_resume_removes_its_bullets(client: TestClient, engine: Engine) -> None:
    rid = upload(client, "old.tex", JAKE.encode())["resume_id"]
    assert client.get("/api/knowledge/resumes").json()[0]["bullet_count"] == 3
    assert client.delete(f"/api/knowledge/resumes/{rid}").status_code == 204
    assert client.get("/api/knowledge/bullets").json() == []
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM embeddings")).scalar() == 0


def test_toggle_and_delete_bullet(client: TestClient) -> None:
    upload(client, "old.tex", JAKE.encode())
    b = client.get("/api/knowledge/bullets").json()[0]
    assert (
        client.patch(f"/api/knowledge/bullets/{b['id']}", json={"is_active": False}).json()[
            "is_active"
        ]
        is False
    )
    assert client.delete(f"/api/knowledge/bullets/{b['id']}").status_code == 204
    assert len(client.get("/api/knowledge/bullets").json()) == 2


def test_search_ranks_relevant_bullets(client: TestClient) -> None:
    upload(client, "old.tex", JAKE.encode())
    hits = client.post(
        "/api/knowledge/search",
        json={"query": "Backend role: FastAPI service, uptime, req/s", "k": 3},
    ).json()
    assert "FastAPI" in hits[0]["content"] and hits[0]["matched_keywords"] == ["fastapi"]
    assert hits[0]["score"] > hits[-1]["score"]

    # an inactive bullet is never returned
    client.patch(f"/api/knowledge/bullets/{hits[0]['bullet']['id']}", json={"is_active": False})
    hits = client.post("/api/knowledge/search", json={"query": "FastAPI service", "k": 5}).json()
    assert all("FastAPI" not in h["content"] for h in hits)


def test_outcome_score_boosts_ranking(client: TestClient, db: Session) -> None:
    upload(client, "old.tex", JAKE.encode())
    q = {"query": "cutting build time and uptime of services", "k": 3}
    before = [h["bullet"]["id"] for h in client.post("/api/knowledge/search", json=q).json()]
    loser = db.get(Bullet, before[-1])
    assert loser is not None
    loser.outcome_score = 10.0
    db.commit()
    after = [h["bullet"]["id"] for h in client.post("/api/knowledge/search", json=q).json()]
    assert after[0] == before[-1]


def test_embed_missing_updates_and_cleans(gateway: LLMGateway, db: Session, engine: Engine) -> None:
    from sqlalchemy.orm import sessionmaker

    maker = sessionmaker(bind=engine, expire_on_commit=False)
    b = Bullet(text="Built X with Python", source_type=BulletSource.PROFILE, content_hash="h1")
    db.add(b)
    db.commit()
    assert embed_missing(gateway, maker) == 1
    assert embed_missing(gateway, maker) == 0  # idempotent
    b.text = "Built Y with Go"
    db.commit()
    assert embed_missing(gateway, maker) == 1  # changed content re-embedded
    db.expire_all()
    assert db.query(Embedding).one().content.startswith("Built Y")
    db.delete(b)
    db.commit()
    embed_missing(gateway, maker)
    assert db.query(Embedding).count() == 0  # orphan removed


def test_search_requires_embedding_model(client: TestClient, db: Session) -> None:
    from app.models import LLMTaskConfig
    from app.models.enums import LLMTask

    db.delete(db.get(LLMTaskConfig, LLMTask.EMBEDDING))
    db.commit()
    assert client.post("/api/knowledge/search", json={"query": "python"}).status_code == 409


def test_chunk_text() -> None:
    text = ("Sentence one is here. " * 200).strip()
    chunks = chunk_text(text, size=500, overlap=50)
    assert all(len(c) <= 500 for c in chunks) and len(chunks) > 5
    assert chunk_text("short") == ["short"] and chunk_text("  ") == []


def test_pgvector_distance_is_typed_as_float() -> None:
    """Regression: the <=> result must not inherit the vector column's type (Postgres-only
    crash: "'float' object is not subscriptable")."""
    from sqlalchemy import Float
    from sqlalchemy.dialects import postgresql

    from app.knowledge.retrieval import cosine_distance

    expr = cosine_distance([0.1, 0.2])
    assert isinstance(expr.type, Float)
    assert "<=>" in str(expr.compile(dialect=postgresql.dialect()))
