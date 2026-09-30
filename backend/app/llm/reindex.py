"""Full knowledge-base re-index when the embedding model changes.

The API only *requests* a re-index (a settings flag); the worker picks it up. The job is
resumable: chunks already embedded with the active model are skipped, and old-model rows
are deleted only after every chunk has a new vector. Retrieval always filters on the
active model key, so a half-finished re-index never mixes vectors.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import and_, delete, exists, select
from sqlalchemy.orm import Session, aliased

from app.llm.gateway import LLMGateway
from app.models import Embedding, Setting
from app.models.enums import NotificationLevel
from app.services.notifications import notify

log = logging.getLogger(__name__)

REINDEX_KEY = "embedding.reindex_request"
BATCH = 256


def request_reindex(db: Session, model_key: str) -> None:
    row = db.get(Setting, REINDEX_KEY) or Setting(key=REINDEX_KEY, is_secret=False)
    row.value = {"model_key": model_key, "requested_at": datetime.now(UTC).isoformat()}
    db.add(row)


def pending_reindex(db: Session) -> str | None:
    row = db.get(Setting, REINDEX_KEY)
    return row.value.get("model_key") if row and row.value else None


def run_pending_reindex(gateway: LLMGateway, session_factory: Callable[[], Session]) -> int:
    """Re-embed every chunk that lacks a vector for the active model. Returns count."""
    with session_factory() as db:
        requested = pending_reindex(db)
    if requested is None:
        return 0
    active = gateway.active_embedding_key()
    if active is None:
        log.warning("re-index requested but no embedding model is configured")
        return 0

    done = 0
    while True:
        with session_factory() as db:
            new = aliased(Embedding)
            todo = db.execute(
                select(
                    Embedding.owner_type,
                    Embedding.owner_id,
                    Embedding.chunk_index,
                    Embedding.content,
                )
                .where(Embedding.model_name != active)
                .where(
                    ~exists().where(
                        and_(
                            new.model_name == active,
                            new.owner_type == Embedding.owner_type,
                            new.owner_id == Embedding.owner_id,
                            new.chunk_index == Embedding.chunk_index,
                        )
                    )
                )
                .distinct()
                .limit(BATCH)
            ).all()
        if not todo:
            break
        result = gateway.embed([r.content for r in todo])
        if result.model_key != active:  # model changed again mid-run; next run handles it
            log.warning("embedding model changed during re-index; stopping this run")
            return done
        with session_factory() as db:
            for row, vec in zip(todo, result.vectors, strict=True):
                db.add(
                    Embedding(
                        owner_type=row.owner_type,
                        owner_id=row.owner_id,
                        chunk_index=row.chunk_index,
                        content=row.content,
                        model_name=result.model_key,
                        dimension=result.dimension,
                        vector=vec,
                    )
                )
            db.commit()
        done += len(todo)
        log.info("re-index: %d chunks embedded with %s", done, active)

    with session_factory() as db:
        removed = db.execute(delete(Embedding).where(Embedding.model_name != active)).rowcount  # type: ignore[attr-defined]
        flag = db.get(Setting, REINDEX_KEY)
        if flag is not None:
            db.delete(flag)
        db.commit()
        notify(
            db,
            NotificationLevel.INFO,
            "embedding.reindexed",
            f"Knowledge base re-indexed with {active}",
            f"{done} chunk(s) embedded; {removed} old vector(s) removed.",
        )
    return done
