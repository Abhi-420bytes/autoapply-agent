from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.knowledge.github import (
    GitHubClient,
    GitHubRateLimitedError,
    RepoSummaryLLM,
    guard_summary,
    role_from_contributors,
    sync_github,
)
from app.knowledge.jobs import get_status, request_github_sync, run_pending_github_sync
from app.knowledge.skills import SkillTagger
from app.llm.gateway import LLMGateway
from app.models import Bullet, Embedding, GitHubRepo, Notification
from app.schemas.settings import AppSettingsPatch
from app.services.settings_service import update_app_settings
from tests.fakes import FakeBackend

pytestmark = pytest.mark.usefixtures("llm_ready")

README = """# chess-engine
A UCI chess engine written in Rust. Uses alpha-beta search with transposition tables.
Benchmarks: searches 2.1M nodes/sec on a laptop. 120 stars on GitHub."""


def repo(name: str, **kw: object) -> dict[str, object]:
    return {
        "name": name,
        "full_name": f"abhi/{name}",
        "html_url": f"https://github.com/abhi/{name}",
        "description": kw.get("description", f"{name} project"),
        "topics": kw.get("topics", []),
        "stargazers_count": kw.get("stars", 0),
        "forks_count": 0,
        "fork": kw.get("fork", False),
        "private": kw.get("private", False),
        "archived": False,
        "pushed_at": "2025-08-01T10:00:00Z",
    }


def github_api(
    repos: list[dict[str, object]], calls: list[str]
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        calls.append(path)
        if path == "/users/abhi/repos":
            return httpx.Response(200, json=repos if req.url.params.get("page") == "1" else [])
        name = path.split("/")[3] if path.startswith("/repos/") else ""
        if path.endswith("/readme"):
            return (
                httpx.Response(200, text=README) if name == "chess-engine" else httpx.Response(404)
            )
        if path.endswith("/languages"):
            return httpx.Response(200, json={"Rust": 50000, "Shell": 200})
        if path.endswith("/commits"):
            return httpx.Response(
                200,
                json=[{}],
                headers={
                    "link": '<https://api.github.com/x?author=abhi&per_page=1&page=42>; rel="last"'
                },
            )
        if path.endswith("/contributors"):
            return httpx.Response(200, json=[{"login": "abhi", "contributions": 42}])
        return httpx.Response(404)

    return handler


def summary_json(**over: object) -> str:
    base = {
        "problem": "A UCI chess engine written in Rust.",
        "tech_stack": ["Rust", "Kubernetes"],  # Kubernetes is NOT in the data
        "outcomes": ["Searches 2.1M nodes/sec", "Serves 10k users"],  # 10k is invented
        "bullets": [
            "Built a UCI chess engine in Rust with alpha-beta search and transposition tables",
            "Scaled the engine to 50k concurrent games on Kubernetes",  # invented
        ],
    }
    return json.dumps(base | over)


def run_sync(
    gateway: LLMGateway,
    engine: Engine,
    repos: list[dict[str, object]],
    calls: list[str],
    **kw: object,
):  # type: ignore[no-untyped-def]
    client = GitHubClient(transport=httpx.MockTransport(github_api(repos, calls)))
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    args = {
        "username": "abhi",
        "include_private": False,
        "include_forks": False,
        "exclude": [],
        "tagger": SkillTagger(),
    } | kw
    return sync_github(gateway, maker, client, **args)  # type: ignore[arg-type]


def test_role_from_contributors() -> None:
    assert role_from_contributors("abhi", [("abhi", 10)]) == "Sole developer"
    assert role_from_contributors("abhi", [("abhi", 3), ("x", 7)]) == "Contributor (30% of commits)"
    assert role_from_contributors("abhi", [("x", 7)]) == "Repository owner"


def test_guard_drops_unsupported_claims() -> None:
    clean, dropped = guard_summary(
        RepoSummaryLLM.model_validate_json(summary_json()), README + " Rust", SkillTagger()
    )
    assert clean.tech_stack == ["Rust"]
    assert clean.outcomes == ["Searches 2.1M nodes/sec"]
    assert len(clean.bullets) == 1 and "alpha-beta" in clean.bullets[0]
    assert "tech: Kubernetes" in dropped and any("10k users" in d for d in dropped)


def test_sync_filters_summarizes_and_guards(
    gateway: LLMGateway, backend: FakeBackend, engine: Engine, db: Session
) -> None:
    backend.script("summ", summary_json(), summary_json(problem="Tool.", outcomes=[], bullets=[]))
    repos = [
        repo("chess-engine", stars=120, topics=["chess"]),
        repo("dotfiles"),
        repo("forked-lib", fork=True),
        repo("secret", private=True),
        repo("skip-me"),
    ]
    calls: list[str] = []
    rep = run_sync(gateway, engine, repos, calls, exclude=["skip-me"])
    assert (rep.repos_seen, rep.repos_kept, rep.summarized) == (5, 2, 2)

    chess = db.query(GitHubRepo).filter_by(full_name="abhi/chess-engine").one()
    assert chess.summary is not None and chess.summary["role"] == "Sole developer"
    assert chess.summary["tech_stack"] == ["Rust"] and chess.commit_count == 42
    assert "tech: Kubernetes" in chess.summary["dropped"]
    bullets = db.query(Bullet).filter_by(github_repo_id=chess.id).all()
    assert [b.text[:22] for b in bullets] == ["Built a UCI chess engi"]
    assert bullets[0].skills == ["Rust"] and bullets[0].heading == "chess-engine"
    assert db.query(Embedding).filter_by(owner_type="github_repo").count() >= 2
    # the private repo and fork were never fetched in detail
    assert not any("/secret/" in c or "/forked-lib/readme" in c for c in calls)
    # the prompt carried the deterministic role
    assert "Sole developer" in backend.calls[0][1][-1]["content"]


def test_resync_skips_unchanged_and_removes_gone(
    gateway: LLMGateway, backend: FakeBackend, engine: Engine, db: Session
) -> None:
    backend.script("summ", summary_json(), summary_json())
    run_sync(gateway, engine, [repo("chess-engine"), repo("old")], [])
    n_calls = len(backend.calls)
    rep = run_sync(gateway, engine, [repo("chess-engine")], [])
    assert rep.unchanged == 1 and rep.summarized == 0 and rep.removed == 1
    assert len(backend.calls) == n_calls  # no LLM call for an unchanged repo
    assert [r.full_name for r in db.query(GitHubRepo).all()] == ["abhi/chess-engine"]


def test_llm_not_configured_keeps_metadata(
    gateway: LLMGateway, engine: Engine, db: Session
) -> None:
    from app.models import LLMTaskConfig
    from app.models.enums import LLMTask

    db.delete(db.get(LLMTaskConfig, LLMTask.REPO_SUMMARIZER))
    db.commit()
    rep = run_sync(gateway, engine, [repo("chess-engine"), repo("b")], [])
    assert rep.repos_kept == 2 and rep.summarized == 0 and "summaries paused" in rep.errors[0]
    db.expire_all()
    assert db.query(GitHubRepo).count() == 2


def test_rate_limit_error_is_explained() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1900000000"}
        )

    client = GitHubClient(transport=httpx.MockTransport(handler))
    with pytest.raises(GitHubRateLimitedError, match="Add a GitHub token"):
        client.list_repos("abhi", include_private=False)


def test_sync_job_status_and_notification(
    gateway: LLMGateway, backend: FakeBackend, engine: Engine, db: Session
) -> None:
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    assert run_pending_github_sync(gateway, maker) is None  # nothing requested

    update_app_settings(db, AppSettingsPatch(github_username="abhi"))
    request_github_sync(db)
    assert get_status(db)["state"] == "queued"  # type: ignore[index]
    backend.script("summ", summary_json())

    def factory(token: str | None) -> GitHubClient:
        return GitHubClient(token, httpx.MockTransport(github_api([repo("chess-engine")], [])))

    status = run_pending_github_sync(gateway, maker, factory)
    assert status is not None and status["state"] == "done" and status["summarized"] == 1

    def boom(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    request_github_sync(db)
    status = run_pending_github_sync(
        gateway, maker, lambda t: GitHubClient(t, httpx.MockTransport(boom))
    )
    assert status is not None and status["state"] == "failed" and "not found" in status["error"]
    db.expire_all()
    assert db.query(Notification).filter_by(kind="github.sync").count() == 1


def test_retired_model_stops_sync_once_and_alerts(
    gateway: LLMGateway, backend: FakeBackend, engine: Engine, db: Session
) -> None:
    from app.llm.errors import ErrorKind, LLMProviderError

    backend.script("summ", *[LLMProviderError(ErrorKind.NOT_FOUND, "404 retired")] * 5)
    rep = run_sync(gateway, engine, [repo("a"), repo("b"), repo("c")], [])
    assert rep.repos_kept == 3 and rep.summarized == 0
    assert len(rep.errors) == 1 and "summaries stopped" in rep.errors[0]
    assert len(backend.calls) == 1  # no pointless calls for the other repos
    db.expire_all()
    alert = db.query(Notification).filter_by(kind="llm.config").one()
    assert "repo_summarizer" in alert.title and "isn't available" in alert.title
