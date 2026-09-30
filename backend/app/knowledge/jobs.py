"""Background knowledge jobs, run by the worker.

The API only records a *request* (a settings row); the worker picks it up, runs it and
writes a status row the dashboard polls. That keeps long LLM-heavy syncs out of HTTP
requests and makes them resumable across restarts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.knowledge.bank import embed_missing
from app.knowledge.github import GitHubClient, GitHubError, sync_github
from app.knowledge.past_resumes import build_tagger
from app.llm.errors import LLMError
from app.llm.gateway import LLMGateway
from app.models import Setting
from app.models.enums import NotificationLevel
from app.services.notifications import notify
from app.services.settings_service import get_app_settings, get_secret

log = logging.getLogger(__name__)

SYNC_REQUEST = "github.sync_request"
SYNC_STATUS = "github.sync_status"


def _put(db: Session, key: str, value: Any) -> None:
    row = db.get(Setting, key) or Setting(key=key, is_secret=False)
    row.value = value
    db.add(row)


def get_status(db: Session) -> dict[str, Any] | None:
    row = db.get(Setting, SYNC_STATUS)
    return row.value if row else None


def request_github_sync(db: Session, reason: str = "manual") -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    _put(db, SYNC_REQUEST, {"requested_at": now, "reason": reason})
    status = {"state": "queued", "requested_at": now, "reason": reason}
    _put(db, SYNC_STATUS, status)
    db.commit()
    return status


def run_pending_github_sync(
    gateway: LLMGateway,
    session_factory: Callable[[], Session],
    client_factory: Callable[[str | None], GitHubClient] = GitHubClient,
) -> dict[str, Any] | None:
    with session_factory() as db:
        req = db.get(Setting, SYNC_REQUEST)
        if req is None:
            return None
        db.delete(req)
        settings = get_app_settings(db)
        token = get_secret(db, "github_token")
        tagger = build_tagger(db)
        started = datetime.now(UTC).isoformat()
        _put(db, SYNC_STATUS, {"state": "running", "started_at": started})
        db.commit()

    status: dict[str, Any]
    if not settings.github_username:
        status = {"state": "failed", "error": "set your GitHub username first"}
    else:
        client = client_factory(token)
        try:
            report = sync_github(
                gateway,
                session_factory,
                client,
                username=settings.github_username,
                include_private=settings.github_include_private,
                include_forks=settings.github_include_forks,
                exclude=settings.github_exclude_repos,
                tagger=tagger,
            )
            status = {"state": "done", **asdict(report)}
        except GitHubError as exc:
            status = {"state": "failed", "error": str(exc)}
        except Exception as exc:  # keep the worker alive; surface the failure
            log.exception("GitHub sync crashed")
            status = {"state": "failed", "error": f"unexpected error: {type(exc).__name__}"}
        finally:
            client.close()

    status |= {"started_at": started, "finished_at": datetime.now(UTC).isoformat()}
    with session_factory() as db:
        _put(db, SYNC_STATUS, status)
        db.commit()
        if status["state"] == "failed" or status.get("errors"):
            notify(
                db,
                NotificationLevel.WARNING,
                "github.sync",
                "GitHub sync " + ("failed" if status["state"] == "failed" else "had problems"),
                status.get("error") or "; ".join(status.get("errors", [])[:3]),
            )
    return status


def run_embedding_sync(gateway: LLMGateway, session_factory: Callable[[], Session]) -> int:
    try:
        return embed_missing(gateway, session_factory)
    except LLMError as exc:
        log.warning("embedding sync failed, will retry: %s", exc)
        return 0
