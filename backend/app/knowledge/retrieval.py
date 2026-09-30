"""Retrieval over the knowledge base for a query (e.g. a job description).

score = cosine similarity (active embedding model only)
      + KEYWORD_WEIGHT × share of the query's skill keywords the item has
      + OUTCOME_WEIGHT × the bullet's outcome score (learned in Phase 9)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import ColumnElement, Float, select
from sqlalchemy.orm import Session

from app.knowledge.bank import BULLET, REPO, cosine
from app.knowledge.skills import SkillTagger
from app.llm.errors import LLMNotConfiguredError
from app.llm.gateway import LLMGateway
from app.models import Bullet, Embedding, GitHubRepo

KEYWORD_WEIGHT = 0.15
OUTCOME_WEIGHT = 0.10
CANDIDATE_FACTOR = 5


@dataclass
class Hit:
    owner_type: str
    owner_id: int
    content: str
    similarity: float
    keyword_overlap: float
    score: float
    matched_keywords: list[str] = field(default_factory=list)
    bullet: Bullet | None = None
    repo: GitHubRepo | None = None


def cosine_distance(qvec: list[float]) -> ColumnElement[float]:
    """pgvector cosine distance. return_type matters: without it SQLAlchemy applies the
    vector column's result processor to the float distance and crashes."""
    return Embedding.vector.op("<=>", return_type=Float)(qvec)


def _candidates(
    db: Session, model_key: str, qvec: list[float], owner_types: tuple[str, ...], limit: int
) -> list[tuple[str, int, str, float]]:
    """(owner_type, owner_id, content, similarity) of the nearest chunks."""
    base = select(Embedding.owner_type, Embedding.owner_id, Embedding.content).where(
        Embedding.model_name == model_key,
        Embedding.owner_type.in_(owner_types),
        Embedding.dimension == len(qvec),
    )
    if db.get_bind().dialect.name == "postgresql":
        distance = cosine_distance(qvec)
        rows = db.execute(base.add_columns(distance).order_by(distance).limit(limit)).all()
        return [(t, i, c, 1.0 - float(d)) for t, i, c, d in rows]
    rows = db.execute(base.add_columns(Embedding.vector)).all()  # SQLite (tests)
    scored = [(t, i, c, cosine(qvec, v)) for t, i, c, v in rows]
    return sorted(scored, key=lambda r: r[3], reverse=True)[:limit]


def search(
    db: Session,
    gateway: LLMGateway,
    query: str,
    *,
    k: int = 20,
    owner_types: tuple[str, ...] = (BULLET, REPO),
    keywords: list[str] | None = None,
    tagger: SkillTagger | None = None,
) -> list[Hit]:
    model_key = gateway.active_embedding_key()
    if model_key is None:
        raise LLMNotConfiguredError("no embedding model configured. Set it in Settings → LLM.")
    qvec = gateway.embed([query]).vectors[0]
    kw = {
        w.lower()
        for w in (keywords if keywords is not None else (tagger or SkillTagger()).tags(query))
    }

    best: dict[tuple[str, int], tuple[str, float]] = {}
    for t, i, content, sim in _candidates(db, model_key, qvec, owner_types, k * CANDIDATE_FACTOR):
        if (t, i) not in best or sim > best[(t, i)][1]:
            best[(t, i)] = (content, sim)

    bullets = {
        b.id: b
        for b in db.scalars(
            select(Bullet).where(Bullet.id.in_([i for t, i in best if t == BULLET]))
        )
    }
    repos = {
        r.id: r
        for r in db.scalars(
            select(GitHubRepo).where(GitHubRepo.id.in_([i for t, i in best if t == REPO]))
        )
    }

    hits: list[Hit] = []
    for (t, i), (content, sim) in best.items():
        b, r = bullets.get(i) if t == BULLET else None, repos.get(i) if t == REPO else None
        if t == BULLET and (b is None or not b.is_active):
            continue
        if t == REPO and r is None:
            continue
        item_terms: set[str] = set()
        if b is not None:
            item_terms = {s.lower() for s in b.skills}
        elif r is not None:
            item_terms = {
                x.lower()
                for x in (*r.languages, *r.topics, *((r.summary or {}).get("tech_stack") or []))
            }
        matched = sorted(kw & item_terms)
        overlap = len(matched) / len(kw) if kw else 0.0
        outcome = b.outcome_score if b is not None else 0.0
        hits.append(
            Hit(
                owner_type=t,
                owner_id=i,
                content=content,
                similarity=sim,
                keyword_overlap=overlap,
                matched_keywords=matched,
                score=sim + KEYWORD_WEIGHT * overlap + OUTCOME_WEIGHT * outcome,
                bullet=b,
                repo=r,
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:k]
