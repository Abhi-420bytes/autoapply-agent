"""Bullet bank helpers and embedding sync.

Chunks are derived deterministically from the source tables (bullets, github_repos), so
`embed_missing()` can bring the vector store in line with them for the active embedding
model: new/changed items are embedded, stale chunks replaced, orphans removed.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Callable, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.knowledge.skills import normalize
from app.llm.errors import LLMError
from app.llm.gateway import LLMGateway
from app.models import Bullet, Embedding, GitHubRepo

log = logging.getLogger(__name__)

BULLET = "bullet"
REPO = "github_repo"
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150
MAX_README_CHUNKS = 6
NEAR_DUPLICATE_SIMILARITY = 0.93


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):  # prefer to break at a paragraph/sentence boundary
            cut = max(text.rfind("\n\n", start, end), text.rfind(". ", start, end))
            if cut > start + size // 2:
                end = cut + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def repo_summary_text(repo: GitHubRepo) -> str:
    s = repo.summary or {}
    parts = [
        f"Project: {repo.full_name.split('/')[-1]}",
        repo.description or "",
        f"Problem: {s.get('problem')}" if s.get("problem") else "",
        "Tech: " + ", ".join(s.get("tech_stack") or list(repo.languages or {})),
        f"Role: {s['role']}" if s.get("role") else "",
        "Outcomes: " + "; ".join(s["outcomes"]) if s.get("outcomes") else "",
        "Topics: " + ", ".join(repo.topics) if repo.topics else "",
    ]
    return "\n".join(p for p in parts if p)


def repo_chunks(repo: GitHubRepo) -> list[str]:
    chunks = [repo_summary_text(repo)]
    if repo.readme:
        chunks += chunk_text(repo.readme)[:MAX_README_CHUNKS]
    return chunks


def bullet_chunk_text(text: str, section: str | None, heading: str | None) -> str:
    """The exact text embedded for a bullet (used for storage AND duplicate checks, so
    both sides are compared in the same form)."""
    context = " — ".join(x for x in (section, heading) if x)
    return text + (f" ({context})" if context else "")


def bullet_chunks(b: Bullet) -> list[str]:
    return [bullet_chunk_text(b.text, b.section, b.heading)]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def embed_missing(gateway: LLMGateway, session_factory: Callable[[], Session]) -> int:
    """Make the vector store match the source tables for the active embedding model.
    Returns the number of chunks embedded. Safe to call repeatedly."""
    active = gateway.active_embedding_key()
    if active is None:
        return 0

    with session_factory() as db:
        desired: dict[tuple[str, int], list[str]] = {}
        for b in db.scalars(select(Bullet)):
            desired[(BULLET, b.id)] = bullet_chunks(b)
        for r in db.scalars(select(GitHubRepo)):
            desired[(REPO, r.id)] = repo_chunks(r)

        existing: dict[tuple[str, int], dict[int, str]] = {}
        for owner_type, owner_id, idx, content in db.execute(
            select(
                Embedding.owner_type, Embedding.owner_id, Embedding.chunk_index, Embedding.content
            ).where(Embedding.model_name == active)
        ):
            existing.setdefault((owner_type, owner_id), {})[idx] = content

        # orphans: vectors whose source item is gone
        for key in set(existing) - set(desired):
            db.execute(
                delete(Embedding).where(
                    Embedding.owner_type == key[0], Embedding.owner_id == key[1]
                )
            )
        db.commit()

    stale = [key for key, chunks in desired.items() if existing.get(key) != dict(enumerate(chunks))]
    embedded = 0
    batch_keys: list[tuple[str, int]] = []
    batch_texts: list[str] = []

    def flush() -> None:
        nonlocal embedded
        if not batch_texts:
            return
        result = gateway.embed(batch_texts)
        if result.model_key != active:
            raise LLMError("embedding model changed during sync")
        with session_factory() as db:
            pos = 0
            for key in batch_keys:
                chunks = desired[key]
                db.execute(
                    delete(Embedding).where(
                        Embedding.owner_type == key[0],
                        Embedding.owner_id == key[1],
                        Embedding.model_name == active,
                    )
                )
                for i, text in enumerate(chunks):
                    db.add(
                        Embedding(
                            owner_type=key[0],
                            owner_id=key[1],
                            chunk_index=i,
                            content=text,
                            model_name=active,
                            dimension=result.dimension,
                            vector=result.vectors[pos],
                        )
                    )
                    pos += 1
            db.commit()
        embedded += len(batch_texts)
        batch_keys.clear()
        batch_texts.clear()

    for key in stale:
        chunks = desired[key]
        if not chunks:
            continue
        if len(batch_texts) + len(chunks) > 64:
            flush()
        batch_keys.append(key)
        batch_texts.extend(chunks)
    flush()
    if embedded:
        log.info("embedded %d chunk(s) with %s", embedded, active)
    return embedded


def near_duplicates(
    gateway: LLMGateway, db: Session, texts: list[str], threshold: float = NEAR_DUPLICATE_SIMILARITY
) -> list[int | None]:
    """For each text, the id of an existing bullet it nearly duplicates (or None).
    Also catches duplicates within `texts` itself. Falls back to all-None if no embedding
    model is configured or the call fails (exact-hash dedupe still applies)."""
    active = gateway.active_embedding_key()
    if active is None or not texts:
        return [None] * len(texts)
    try:
        new_vecs = gateway.embed(texts).vectors
    except LLMError as exc:
        log.warning("near-duplicate check skipped: %s", exc)
        return [None] * len(texts)
    known = [
        (owner_id, vec)
        for owner_id, vec in db.execute(
            select(Embedding.owner_id, Embedding.vector).where(
                Embedding.model_name == active, Embedding.owner_type == BULLET
            )
        )
    ]
    out: list[int | None] = []
    accepted: list[list[float]] = []
    for vec in new_vecs:
        match = next((oid for oid, v in known if cosine(vec, v) >= threshold), None)
        if match is None and any(cosine(vec, v) >= threshold for v in accepted):
            match = -1  # duplicates an earlier bullet in this same upload
        out.append(match)
        if match is None:
            accepted.append(vec)
    return out
