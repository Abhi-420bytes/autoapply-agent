from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.schemas.settings import AppSettings


def test_health_is_public(client: TestClient) -> None:
    del client.headers["Authorization"]
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}


def test_api_requires_token(client: TestClient) -> None:
    assert client.get("/api/settings", headers={"Authorization": "Bearer wrong"}).status_code == 401
    del client.headers["Authorization"]
    assert client.get("/api/settings").status_code == 401


def test_defaults(client: TestClient) -> None:
    body = client.get("/api/settings").json()
    assert body == AppSettings().model_dump()
    assert body["auto_apply"] is False  # human-in-the-loop by default
    assert body["page_limit"] == 1
    assert body["ats_threshold"] == 80
    assert body["global_delay_minutes"] == 60


def test_patch_persists_and_is_audited(client: TestClient, engine: Engine) -> None:
    r = client.patch("/api/settings", json={"page_limit": 2, "github_username": "abhi-ram"})
    assert r.status_code == 200
    assert r.json()["page_limit"] == 2
    assert client.get("/api/settings").json()["github_username"] == "abhi-ram"
    # untouched fields keep defaults
    assert client.get("/api/settings").json()["ats_threshold"] == 80

    with engine.connect() as conn:
        action = conn.execute(text("SELECT action FROM audit_logs")).scalar_one()
    assert action == "settings.updated"


def test_patch_validation(client: TestClient) -> None:
    assert client.patch("/api/settings", json={"page_limit": 0}).status_code == 422
    assert client.patch("/api/settings", json={"ats_threshold": 101}).status_code == 422
    assert client.patch("/api/settings", json={"timezone": "Mars/Base"}).status_code == 422
    assert client.patch("/api/settings", json={"unknown": 1}).status_code == 422
    assert client.patch("/api/settings", json={"max_quality_iterations": 5}).status_code == 422


def test_secret_is_masked_and_encrypted(client: TestClient, engine: Engine) -> None:
    token = "ghp_abcdefghijklmnopqrstuvwxyzQ9Z1"
    r = client.put("/api/settings/secrets/github_token", json={"value": token})
    assert r.status_code == 200
    assert r.json() == {"name": "github_token", "is_set": True, "masked": "ghp_...Q9Z1"}
    assert token not in r.text

    listed = client.get("/api/settings/secrets")
    assert token not in listed.text

    with engine.connect() as conn:
        raw = conn.execute(text("SELECT encrypted_value, value FROM settings")).one()
        audit = conn.execute(text("SELECT details FROM audit_logs")).scalars().all()
    assert token not in raw[0]
    assert raw[1] is None
    assert all(d is None or token not in d for d in audit)


def test_secret_delete_and_unknown(client: TestClient) -> None:
    assert client.put("/api/settings/secrets/nope", json={"value": "x"}).status_code == 404
    assert client.delete("/api/settings/secrets/github_token").status_code == 404
    client.put("/api/settings/secrets/github_token", json={"value": "ghp_1234567890abcdef"})
    assert client.delete("/api/settings/secrets/github_token").status_code == 204
    status = client.get("/api/settings/secrets").json()
    gh = next(x for x in status if x["name"] == "github_token")
    assert gh == {"name": "github_token", "is_set": False, "masked": None}


def test_profile_roundtrip(client: TestClient) -> None:
    assert client.get("/api/settings/profile").json()["full_name"] == ""
    profile = {
        "full_name": "Abhi Ram",
        "email": "abhi@example.com",
        "links": {"github": "https://github.com/example"},
        "education": [{"institution": "Example University", "branch": "CSE", "cgpa": 8.5}],
        "skills": ["Python", "FastAPI"],
    }
    r = client.put("/api/settings/profile", json=profile)
    assert r.status_code == 200
    got = client.get("/api/settings/profile").json()
    assert got["full_name"] == "Abhi Ram"
    assert got["education"][0]["cgpa"] == 8.5
    assert (
        client.put(
            "/api/settings/profile", json={"education": [{"institution": "X", "cgpa": 11}]}
        ).status_code
        == 422
    )


def test_add_one_skill_keeps_the_rest_of_the_profile(client: TestClient) -> None:
    client.put("/api/settings/profile", json={"full_name": "Abhi Ram", "skills": ["Python"]})
    r = client.post("/api/settings/profile/skills", json={"skill": "  Azure  "})
    assert r.status_code == 200 and r.json()["skills"] == ["Python", "Azure"]
    assert r.json()["full_name"] == "Abhi Ram"
    again = client.post("/api/settings/profile/skills", json={"skill": "azure"})
    assert again.json()["skills"] == ["Python", "Azure"]  # no duplicates
