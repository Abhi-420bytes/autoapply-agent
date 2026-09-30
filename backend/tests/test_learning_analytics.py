from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.orm import Session

from app.generation.runs import run_next
from app.models import Application, Bullet, Outcome, ResumeBullet
from app.models.enums import ApplicationStatus, OutcomeKind
from tests.fakes import FakeBackend
from tests.test_generation import JD, gen_env, script_happy  # noqa: F401 — fixture reuse


@pytest.fixture
def generated(client: TestClient, gen_env: dict[str, Any], backend: FakeBackend) -> dict[str, Any]:  # noqa: F811
    script_happy(backend)
    job = client.post(
        "/api/jobs/manual", json={"jd_text": JD, "company": "Acme", "role": "SDE"}
    ).json()
    run_next(gen_env["deps"], InMemorySaver())
    return client.get(f"/api/jobs/{job['id']}").json()


def test_generated_resume_links_cited_bullets(generated: dict[str, Any], db: Session) -> None:
    links = db.query(ResumeBullet).filter_by(resume_id=generated["latest_resume_id"]).all()
    assert [rb.bullet_id for rb in links] == [1]  # the writer cited "b1"


def test_outcomes_update_bullet_scores_and_ranking(
    client: TestClient, generated: dict[str, Any], db: Session
) -> None:
    jid = generated["id"]
    client.patch(f"/api/jobs/{jid}", json={"status": "applied"})
    app_ = db.query(Application).one()
    assert (
        app_.status is ApplicationStatus.SUBMITTED
        and app_.resume_id == generated["latest_resume_id"]
    )

    client.patch(f"/api/jobs/{jid}", json={"status": "shortlisted"})
    db.expire_all()
    assert db.get(Bullet, 1).outcome_score == pytest.approx(0.6 / 3)  # type: ignore[union-attr]
    assert db.get(Bullet, 2).outcome_score == 0  # type: ignore[union-attr]  # not used in that resume

    client.patch(f"/api/jobs/{jid}", json={"status": "rejected"})  # later outcome supersedes
    db.expire_all()
    assert db.get(Bullet, 1).outcome_score == pytest.approx(-0.3 / 3)  # type: ignore[union-attr]
    assert [o.kind for o in db.query(Outcome).order_by(Outcome.id)] == [
        OutcomeKind.SHORTLISTED,
        OutcomeKind.REJECTED,
    ]

    client.patch(f"/api/jobs/{jid}", json={"status": "offer"})
    hits = client.post(
        "/api/knowledge/search", json={"query": "Built a REST API FastAPI caching Docker", "k": 2}
    ).json()
    assert hits[0]["bullet"]["id"] == 1  # boosted by the offer


def test_status_change_to_non_outcome_does_not_create_outcomes(
    client: TestClient, generated: dict[str, Any], db: Session
) -> None:
    client.patch(f"/api/jobs/{generated['id']}", json={"status": "resume_ready", "notes": "x"})
    assert db.query(Outcome).count() == 0 and db.query(Application).count() == 0


def test_analytics(client: TestClient, generated: dict[str, Any]) -> None:
    client.patch(f"/api/jobs/{generated['id']}", json={"status": "applied"})
    client.patch(f"/api/jobs/{generated['id']}", json={"status": "shortlisted"})
    a = client.get("/api/analytics").json()
    assert (
        len(a["weekly"]) == 12
        and a["weekly"][-1]["applications"] == 1
        and a["weekly"][-1]["generated"] == 1
    )
    assert a["applications_submitted"] == 1 and a["shortlist_rate"] == 1.0
    assert a["avg_ats_score"] == generated["ats_score"] and a["funnel"]["shortlisted"] == 1
    assert {"skill": "Kubernetes", "jobs": 1} in a["top_gaps"]
    assert a["monthly_cost"][-1]["cost_usd"] > 0 and a["cost_per_job"][0]["label"] == "Acme – SDE"
    assert a["top_bullets"][0]["id"] == 1 and a["avg_cost_per_resume"] > 0


def test_monthly_buckets_end_with_current_month(client: TestClient) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    months = [m["month"] for m in client.get("/api/analytics").json()["monthly_cost"]]
    now = datetime.now(ZoneInfo("Asia/Kolkata"))
    assert len(months) == 6 and months[-1] == f"{now.year:04d}-{now.month:02d}"
    assert months == sorted(months) and len(set(months)) == 6
