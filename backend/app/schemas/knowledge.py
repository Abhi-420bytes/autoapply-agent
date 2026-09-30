from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models import Bullet, GitHubRepo


class BulletOut(BaseModel):
    id: int
    text: str
    section: str | None
    heading: str | None
    skills: list[str]
    source_type: str
    source_resume_id: int | None
    github_repo_id: int | None
    source_date: date | None
    outcome_score: float
    is_active: bool


def bullet_out(b: Bullet) -> BulletOut:
    return BulletOut(
        id=b.id,
        text=b.text,
        section=b.section,
        heading=b.heading,
        skills=b.skills,
        source_type=b.source_type.value,
        source_resume_id=b.source_resume_id,
        github_repo_id=b.github_repo_id,
        source_date=b.source_date,
        outcome_score=b.outcome_score,
        is_active=b.is_active,
    )


class RepoOut(BaseModel):
    id: int
    full_name: str
    url: str
    description: str | None
    languages: dict[str, Any]
    topics: list[str]
    stars: int
    forks: int
    is_fork: bool
    is_private: bool
    archived: bool
    commit_count: int | None
    pushed_at: datetime | None
    synced_at: datetime | None
    summary: dict[str, Any] | None
    sync_error: str | None
    bullets: list[BulletOut]


def repo_out(r: GitHubRepo, bullets: list[Bullet]) -> RepoOut:
    return RepoOut(
        id=r.id,
        full_name=r.full_name,
        url=r.url,
        description=r.description,
        languages=r.languages,
        topics=r.topics,
        stars=r.stars,
        forks=r.forks,
        is_fork=r.is_fork,
        is_private=r.is_private,
        archived=r.archived,
        commit_count=r.commit_count,
        pushed_at=r.pushed_at,
        synced_at=r.synced_at,
        summary=r.summary,
        sync_error=r.sync_error,
        bullets=[bullet_out(b) for b in bullets],
    )


class PastResumeOut(BaseModel):
    id: int
    filename: str | None
    format: str | None
    source_date: str | None
    created_at: datetime
    bullet_count: int


class IngestOut(BaseModel):
    resume_id: int
    filename: str
    found: int
    added: int
    exact_duplicates: int
    near_duplicates: int
    warnings: list[str]
    bullets: list[str]


class KnowledgeStatus(BaseModel):
    github_username: str | None
    github_token_set: bool
    github_sync: dict[str, Any] | None
    repos: int
    bullets_by_source: dict[str, int]
    past_resumes: int
    embedding_model: str | None
    embedded_chunks: int
    unembedded_items: int
    reindex_pending: bool


class BulletPatch(BaseModel):
    is_active: bool


class SearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=20_000)
    k: int = Field(default=15, ge=1, le=50)


class SearchHitOut(BaseModel):
    kind: str
    score: float
    similarity: float
    keyword_overlap: float
    matched_keywords: list[str]
    content: str
    bullet: BulletOut | None
    repo_name: str | None
