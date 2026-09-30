"""GitHub ingestion: repos → metadata, LLM summary (guarded), bullets, embeddings.

Truthfulness (hard rule 1):
- The role on each repo comes from GitHub's contributor stats, not the LLM.
- The LLM summarizes only what the repo data contains; afterwards a deterministic guard
  drops any tech term or number that doesn't appear in the repo's own data, and records
  what it dropped so the user can see it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.knowledge.bank import REPO, content_hash, embed_missing
from app.knowledge.skills import SkillTagger
from app.llm.errors import (
    BudgetExceededError,
    LLMCallFailedError,
    LLMError,
    LLMNotConfiguredError,
)
from app.llm.gateway import LLMGateway
from app.models import Bullet, Embedding, GitHubRepo
from app.models.enums import BulletSource, LLMTask

log = logging.getLogger(__name__)

API = "https://api.github.com"
README_MAX_CHARS = 30_000
README_PROMPT_CHARS = 12_000
MAX_REPOS = 100


class GitHubError(Exception):
    pass


class GitHubRateLimitedError(GitHubError):
    pass


class GitHubClient:
    def __init__(self, token: str | None = None, transport: httpx.BaseTransport | None = None):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "autoapply-agent",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.has_token = bool(token)
        self._http = httpx.Client(base_url=API, headers=headers, timeout=20, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, **kw: Any) -> httpx.Response:
        try:
            r = self._http.get(path, **kw)
        except httpx.HTTPError as exc:
            raise GitHubError(f"could not reach GitHub: {exc}") from None
        if r.status_code == 401:
            raise GitHubError("GitHub rejected the token (401). Update it in Settings.")
        if r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0":
            reset = r.headers.get("x-ratelimit-reset")
            when = datetime.fromtimestamp(int(reset), UTC).isoformat() if reset else "later"
            hint = "" if self.has_token else " Add a GitHub token to raise the limit to 5000/hour."
            raise GitHubRateLimitedError(f"GitHub rate limit reached; resets at {when}.{hint}")
        return r

    def authenticated_login(self) -> str | None:
        if not self.has_token:
            return None
        r = self._get("/user")
        return str(r.json().get("login")) if r.status_code == 200 else None

    def list_repos(self, username: str, include_private: bool) -> list[dict[str, Any]]:
        own = include_private and (self.authenticated_login() or "").lower() == username.lower()
        path = "/user/repos" if own else f"/users/{username}/repos"
        params: dict[str, Any] = {"per_page": 100, "sort": "pushed"}
        params |= {"affiliation": "owner", "visibility": "all"} if own else {"type": "owner"}
        repos: list[dict[str, Any]] = []
        page = 1
        while len(repos) < MAX_REPOS:
            r = self._get(path, params=params | {"page": page})
            if r.status_code == 404:
                raise GitHubError(f"GitHub user '{username}' not found")
            if r.status_code != 200:
                raise GitHubError(f"listing repos failed ({r.status_code})")
            batch = r.json()
            repos += batch
            if len(batch) < 100:
                break
            page += 1
        return repos[:MAX_REPOS]

    def readme(self, full_name: str) -> str | None:
        r = self._get(
            f"/repos/{full_name}/readme", headers={"Accept": "application/vnd.github.raw+json"}
        )
        return r.text[:README_MAX_CHARS] if r.status_code == 200 else None

    def languages(self, full_name: str) -> dict[str, int]:
        r = self._get(f"/repos/{full_name}/languages")
        return dict(r.json()) if r.status_code == 200 else {}

    def commit_count(self, full_name: str, author: str) -> int:
        r = self._get(f"/repos/{full_name}/commits", params={"author": author, "per_page": 1})
        if r.status_code != 200:  # 409 = empty repository
            return 0
        last = re.search(r'[?&]page=(\d+)>; rel="last"', r.headers.get("link", ""))
        return int(last.group(1)) if last else len(r.json())

    def contributors(self, full_name: str) -> list[tuple[str, int]]:
        r = self._get(f"/repos/{full_name}/contributors", params={"per_page": 100})
        if r.status_code != 200 or not r.content:
            return []
        return [(c.get("login", ""), int(c.get("contributions", 0))) for c in r.json()]


def role_from_contributors(username: str, contributors: list[tuple[str, int]]) -> str:
    total = sum(n for _, n in contributors)
    mine = sum(n for login, n in contributors if login.lower() == username.lower())
    if total and mine == total:
        return "Sole developer"
    if mine:
        return f"Contributor ({round(100 * mine / total)}% of commits)"
    return "Repository owner"


class RepoSummaryLLM(BaseModel):
    problem: str = Field(description="One sentence: what the project does / solves.")
    tech_stack: list[str] = Field(default_factory=list, max_length=15)
    outcomes: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Measurable results ONLY if stated in the data (numbers, users, stars).",
    )
    bullets: list[str] = Field(
        default_factory=list,
        max_length=4,
        description="Resume bullets: action verb + what + impact, each under 200 chars.",
    )


_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER_RE.findall(text)}


def guard_summary(
    summary: RepoSummaryLLM, source_text: str, tagger: SkillTagger
) -> tuple[RepoSummaryLLM, list[str]]:
    """Drop claims not supported by the repo data. Returns (clean summary, dropped items)."""
    src_lower = source_text.lower()
    src_numbers = _numbers(source_text)
    src_tags = set(tagger.tags(source_text))
    dropped: list[str] = []

    tech = []
    for t in summary.tech_stack:
        if t.lower() in src_lower or t in src_tags:
            tech.append(t)
        else:
            dropped.append(f"tech: {t}")

    def supported(claim: str) -> bool:
        if not _numbers(claim) <= src_numbers:
            return False
        return set(tagger.tags(claim)) <= (src_tags | set(tech))

    outcomes: list[str] = []
    for o in summary.outcomes:
        if supported(o):
            outcomes.append(o)
        else:
            dropped.append(f"outcome: {o}")
    bullets: list[str] = []
    for b in summary.bullets:
        if supported(b):
            bullets.append(b[:220])
        else:
            dropped.append(f"bullet: {b}")
    problem = summary.problem
    if not supported(problem):
        dropped.append(f"problem: {problem}")
        problem = ""
    return (
        RepoSummaryLLM(problem=problem, tech_stack=tech, outcomes=outcomes, bullets=bullets),
        dropped,
    )


@dataclass
class SyncReport:
    repos_seen: int = 0
    repos_kept: int = 0
    summarized: int = 0
    unchanged: int = 0
    removed: int = 0
    bullets: int = 0
    embedded: int = 0
    errors: list[str] = field(default_factory=list)


def _source_text(repo: GitHubRepo) -> str:
    return "\n".join(
        [
            repo.full_name,
            repo.description or "",
            " ".join(repo.topics or []),
            " ".join(repo.languages or {}),
            repo.readme or "",
            f"{repo.stars} stars",
            f"{repo.forks} forks",
        ]
    )


def sync_github(
    gateway: LLMGateway,
    session_factory: Callable[[], Session],
    client: GitHubClient,
    *,
    username: str,
    include_private: bool,
    include_forks: bool,
    exclude: list[str],
    tagger: SkillTagger,
) -> SyncReport:
    report = SyncReport()
    listed = client.list_repos(username, include_private)
    report.repos_seen = len(listed)
    excluded = {e.lower() for e in exclude}
    llm_available = True
    kept_names: set[str] = set()

    for meta in listed:
        full_name = meta["full_name"]
        if meta["name"].lower() in excluded or full_name.lower() in excluded:
            continue
        if meta.get("private") and not include_private:
            continue
        commits = client.commit_count(full_name, username)
        if meta.get("fork") and not (include_forks and commits > 0):
            continue
        kept_names.add(full_name)
        readme = client.readme(full_name)
        languages = client.languages(full_name)
        role = role_from_contributors(username, client.contributors(full_name))

        # 1) Save metadata and commit: no transaction stays open during the LLM call.
        with session_factory() as db:
            repo = db.scalar(select(GitHubRepo).where(GitHubRepo.full_name == full_name))
            if repo is None:
                repo = GitHubRepo(full_name=full_name, url=meta["html_url"])
                db.add(repo)
            repo.url = meta["html_url"]
            repo.description = meta.get("description")
            repo.readme = readme
            repo.languages = languages
            repo.topics = list(meta.get("topics") or [])
            repo.stars = int(meta.get("stargazers_count") or 0)
            repo.forks = int(meta.get("forks_count") or 0)
            repo.is_fork = bool(meta.get("fork"))
            repo.is_private = bool(meta.get("private"))
            repo.archived = bool(meta.get("archived"))
            repo.commit_count = commits
            pushed = meta.get("pushed_at")
            repo.pushed_at = (
                datetime.fromisoformat(pushed.replace("Z", "+00:00")) if pushed else None
            )
            repo.synced_at = datetime.now(UTC)
            source = _source_text(repo)
            new_hash = hashlib.sha256((source + role).encode()).hexdigest()
            needs_summary = repo.summary is None or repo.source_hash != new_hash
            db.commit()
            repo_id = repo.id
            pushed_date = repo.pushed_at.date() if repo.pushed_at else None
            prompt_vars = {
                "name": meta["name"],
                "description": repo.description or "",
                "languages": ", ".join(languages),
                "topics": ", ".join(repo.topics),
                "stars": repo.stars,
                "role": role,
                "readme": (readme or "")[:README_PROMPT_CHARS],
            }
        report.repos_kept += 1
        if not needs_summary:
            report.unchanged += 1
            continue
        if not llm_available:
            continue

        # 2) LLM call with no DB transaction open.
        error: str | None = None
        try:
            result = gateway.structured(
                LLMTask.REPO_SUMMARIZER, "repo_summary", prompt_vars, RepoSummaryLLM
            )
        except (LLMNotConfiguredError, BudgetExceededError) as exc:
            llm_available = False
            error = str(exc)
            report.errors.append(f"summaries paused: {exc}")
        except LLMCallFailedError as exc:
            error = str(exc)[:500]
            if exc.config_problem:
                # Every other repo would fail identically: stop and say so once.
                llm_available = False
                report.errors.append(
                    "summaries stopped: the repo_summarizer model can't be used "
                    f"({exc.errors[-1].kind}). Change it in Settings → LLM, then sync again."
                )
            else:
                report.errors.append(f"{full_name}: {exc}"[:300])
        except LLMError as exc:
            error = str(exc)[:500]
            report.errors.append(f"{full_name}: {exc}"[:300])

        # 3) Save the (guarded) summary and bullets.
        with session_factory() as db:
            repo = db.get(GitHubRepo, repo_id)
            if repo is None:
                continue
            if error is not None:
                repo.sync_error = error
                db.commit()
                continue
            clean, dropped = guard_summary(result.value, source, tagger)
            repo.summary = {
                "problem": clean.problem,
                "tech_stack": clean.tech_stack,
                "outcomes": clean.outcomes,
                "role": role,
                "bullets": clean.bullets,
                "dropped": dropped,
                "models": result.models_used,
                "summarized_at": datetime.now(UTC).isoformat(),
            }
            repo.source_hash = new_hash
            repo.sync_error = None
            db.execute(delete(Bullet).where(Bullet.github_repo_id == repo_id))
            existing_hashes = set(db.scalars(select(Bullet.content_hash)))
            for text in clean.bullets:
                h = content_hash(text)
                if h in existing_hashes:
                    continue
                existing_hashes.add(h)
                db.add(
                    Bullet(
                        text=text,
                        section="Projects",
                        heading=meta["name"],
                        skills=tagger.tags(text),
                        source_type=BulletSource.GITHUB,
                        github_repo_id=repo_id,
                        content_hash=h,
                        source_date=pushed_date,
                    )
                )
                report.bullets += 1
            report.summarized += 1
            db.commit()

    # repos that disappeared, became excluded, or were filtered out
    with session_factory() as db:
        gone = [r for r in db.scalars(select(GitHubRepo)) if r.full_name not in kept_names]
        for r in gone:
            db.execute(
                delete(Embedding).where(Embedding.owner_type == REPO, Embedding.owner_id == r.id)
            )
            db.execute(delete(Bullet).where(Bullet.github_repo_id == r.id))
            db.delete(r)
        report.removed = len(gone)
        db.commit()

    try:
        report.embedded = embed_missing(gateway, session_factory)
    except LLMError as exc:
        report.errors.append(f"embedding will be retried: {exc}")
    return report


def summary_json(repo: GitHubRepo) -> str:
    return json.dumps(repo.summary or {}, ensure_ascii=False)
