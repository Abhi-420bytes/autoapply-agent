from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.knowledge import jobs
from app.knowledge.bank import BULLET, REPO
from app.knowledge.past_resumes import (
    MAX_FILE_BYTES,
    IngestError,
    build_tagger,
    delete_past_resume,
    ingest_past_resume,
)
from app.knowledge.retrieval import search
from app.llm import get_gateway
from app.llm.errors import LLMError, LLMNotConfiguredError
from app.llm.gateway import LLMGateway
from app.llm.reindex import pending_reindex
from app.models import Bullet, Embedding, GitHubRepo, Resume
from app.models.enums import BulletSource, ResumeKind
from app.schemas.knowledge import (
    BulletOut,
    BulletPatch,
    IngestOut,
    KnowledgeStatus,
    PastResumeOut,
    RepoOut,
    SearchHitOut,
    SearchRequest,
    bullet_out,
    repo_out,
)
from app.services.settings_service import get_app_settings, get_secret

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
MAX_FILES = 10


@router.get("/status", response_model=KnowledgeStatus)
def status(
    db: Session = Depends(get_db), gateway: LLMGateway = Depends(get_gateway)
) -> KnowledgeStatus:
    active = gateway.active_embedding_key()
    by_source: dict[BulletSource, int] = {
        k: v
        for k, v in db.execute(
            select(Bullet.source_type, func.count()).group_by(Bullet.source_type)
        ).tuples()
    }
    embedded_owners = (
        set(
            db.execute(
                select(Embedding.owner_type, Embedding.owner_id)
                .where(Embedding.model_name == active)
                .distinct()
            ).all()
        )
        if active
        else set()
    )
    items = {(BULLET, i) for i in db.scalars(select(Bullet.id))} | {
        (REPO, i) for i in db.scalars(select(GitHubRepo.id))
    }
    return KnowledgeStatus(
        github_username=get_app_settings(db).github_username,
        github_token_set=get_secret(db, "github_token") is not None,
        github_sync=jobs.get_status(db),
        repos=db.scalar(select(func.count(GitHubRepo.id))) or 0,
        bullets_by_source={k.value: v for k, v in by_source.items()},
        past_resumes=db.scalar(select(func.count(Resume.id)).where(Resume.kind == ResumeKind.PAST))
        or 0,
        embedding_model=active,
        embedded_chunks=db.scalar(
            select(func.count(Embedding.id)).where(Embedding.model_name == active)
        )
        or 0
        if active
        else 0,
        unembedded_items=len(items - embedded_owners) if active else len(items),
        reindex_pending=pending_reindex(db) is not None,
    )


@router.post("/github/sync", status_code=202)
def github_sync(db: Session = Depends(get_db)) -> dict[str, object]:
    if not get_app_settings(db).github_username:
        raise HTTPException(409, "set your GitHub username first")
    return jobs.request_github_sync(db)


@router.get("/github/repos", response_model=list[RepoOut])
def github_repos(db: Session = Depends(get_db)) -> list[RepoOut]:
    repos = list(db.scalars(select(GitHubRepo).order_by(GitHubRepo.pushed_at.desc())))
    bullets: dict[int, list[Bullet]] = {}
    for b in db.scalars(select(Bullet).where(Bullet.github_repo_id.is_not(None))):
        bullets.setdefault(b.github_repo_id or 0, []).append(b)
    return [repo_out(r, bullets.get(r.id, [])) for r in repos]


@router.post("/resumes", response_model=list[IngestOut])
async def upload_resumes(
    files: list[UploadFile] = File(...),
    source_date: date | None = Form(None),
    db: Session = Depends(get_db),
    gateway: LLMGateway = Depends(get_gateway),
) -> list[IngestOut]:
    if len(files) > MAX_FILES:
        raise HTTPException(413, f"upload at most {MAX_FILES} files at once")
    out = []
    for f in files:
        data = await f.read(MAX_FILE_BYTES + 1)
        try:
            report = await run_in_threadpool(
                ingest_past_resume,
                db,
                gateway,
                gateway.session_factory,
                filename=f.filename or "resume",
                data=data,
                source_date=source_date,
            )
        except IngestError as exc:
            db.rollback()
            raise HTTPException(422, str(exc)) from None
        out.append(IngestOut(**report.__dict__))
    return out


@router.get("/resumes", response_model=list[PastResumeOut])
def past_resumes(db: Session = Depends(get_db)) -> list[PastResumeOut]:
    counts: dict[int | None, int] = {
        k: v
        for k, v in db.execute(
            select(Bullet.source_resume_id, func.count()).group_by(Bullet.source_resume_id)
        ).tuples()
    }
    rows = db.scalars(
        select(Resume).where(Resume.kind == ResumeKind.PAST).order_by(Resume.id.desc())
    )
    return [
        PastResumeOut(
            id=r.id,
            filename=(r.meta or {}).get("filename"),
            format=(r.meta or {}).get("format"),
            source_date=(r.meta or {}).get("source_date"),
            created_at=r.created_at,
            bullet_count=counts.get(r.id, 0),
        )
        for r in rows
    ]


@router.delete("/resumes/{resume_id}", status_code=204)
def remove_past_resume(resume_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        delete_past_resume(db, resume_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    return Response(status_code=204)


@router.get("/bullets", response_model=list[BulletOut])
def list_bullets(
    source: BulletSource | None = None, q: str | None = None, db: Session = Depends(get_db)
) -> list[BulletOut]:
    stmt = select(Bullet).order_by(Bullet.id.desc()).limit(500)
    if source is not None:
        stmt = stmt.where(Bullet.source_type == source)
    if q:
        stmt = stmt.where(Bullet.text.ilike(f"%{q}%"))
    return [bullet_out(b) for b in db.scalars(stmt)]


@router.patch("/bullets/{bullet_id}", response_model=BulletOut)
def patch_bullet(bullet_id: int, body: BulletPatch, db: Session = Depends(get_db)) -> BulletOut:
    b = db.get(Bullet, bullet_id)
    if b is None:
        raise HTTPException(404, "bullet not found")
    b.is_active = body.is_active
    db.commit()
    return bullet_out(b)


@router.delete("/bullets/{bullet_id}", status_code=204)
def delete_bullet(bullet_id: int, db: Session = Depends(get_db)) -> Response:
    b = db.get(Bullet, bullet_id)
    if b is None:
        raise HTTPException(404, "bullet not found")
    db.delete(b)
    db.commit()  # its embedding is removed by the next embedding sync (orphan cleanup)
    return Response(status_code=204)


@router.post("/search", response_model=list[SearchHitOut])
async def knowledge_search(
    body: SearchRequest, db: Session = Depends(get_db), gateway: LLMGateway = Depends(get_gateway)
) -> list[SearchHitOut]:
    try:
        hits = await run_in_threadpool(
            search, db, gateway, body.query, k=body.k, tagger=build_tagger(db)
        )
    except LLMNotConfiguredError as exc:
        raise HTTPException(409, str(exc)) from None
    except LLMError as exc:
        raise HTTPException(502, f"embedding failed: {exc}") from None
    return [
        SearchHitOut(
            kind=h.owner_type,
            score=round(h.score, 4),
            similarity=round(h.similarity, 4),
            keyword_overlap=round(h.keyword_overlap, 3),
            matched_keywords=h.matched_keywords,
            content=h.content,
            bullet=bullet_out(h.bullet) if h.bullet else None,
            repo_name=h.repo.full_name if h.repo else None,
        )
        for h in hits
    ]
