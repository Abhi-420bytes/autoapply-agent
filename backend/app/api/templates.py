from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.latex.compiler import Compiler, CompilerUnavailableError, TectonicCompiler
from app.latex.regions import TemplateError, parse_regions, suggest_markers
from app.latex.sanitize import ContentError
from app.models import Resume
from app.schemas.templates import (
    RegionOut,
    RenderOut,
    RenderRequest,
    ResumeOut,
    SuggestMarkersOut,
    TemplateOut,
    resume_out,
)
from app.services import templates as svc
from app.services.settings_service import resume_filename
from app.services.templates import TemplateUploadError

router = APIRouter(tags=["templates"])

_MAX_UPLOAD = svc.MAX_ZIP_BYTES + 1


@lru_cache
def get_compiler() -> Compiler:
    return TectonicCompiler()


async def _read(upload: UploadFile, limit: int = _MAX_UPLOAD) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"{upload.filename} is too large")
    return data


def _template_out(r: Resume) -> TemplateOut:
    meta = r.meta or {}
    regions = parse_regions(r.tex_source)
    lines = {x["name"]: x["line"] for x in meta.get("regions", [])}
    return TemplateOut(
        id=r.id,
        label=r.label,
        is_active=r.is_active,
        created_at=r.created_at,
        page_count=r.page_count,
        page_limit=r.page_limit,
        regions=[
            RegionOut(name=g.name, line=lines.get(g.name, g.line), content=g.content)
            for g in regions
        ],
        bullet_command=meta.get("bullet_command"),
        auto_marked=meta.get("auto_marked", []),
        main_file=meta.get("main_file"),
        assets=meta.get("assets", []),
        overleaf_check=meta.get("overleaf_check"),
        build_report=r.build_report,
        tex_source=r.tex_source,
    )


def _user_error(exc: Exception) -> HTTPException:
    return HTTPException(422, str(exc))


@router.post("/api/templates/suggest-markers", response_model=SuggestMarkersOut)
async def suggest(file: UploadFile = File(...)) -> SuggestMarkersOut:
    data = await _read(file, svc.MAX_TEX_BYTES)
    try:
        tex, names = suggest_markers(svc._decode(data, file.filename or "template.tex"))
    except (TemplateError, TemplateUploadError) as exc:
        raise _user_error(exc) from None
    return SuggestMarkersOut(tex=tex, regions=names)


@router.post("/api/templates", response_model=TemplateOut, status_code=201)
async def upload_template(
    file: UploadFile = File(..., description=".tex file or Overleaf project .zip"),
    reference_pdf: UploadFile | None = File(None, description="the PDF Overleaf produced"),
    label: str | None = Form(None),
    auto_markers: bool = Form(False, description="wrap each \\section body if unmarked"),
    db: Session = Depends(get_db),
    compiler: Compiler = Depends(get_compiler),
) -> TemplateOut:
    data = await _read(file)
    ref = await _read(reference_pdf, svc.MAX_PDF_BYTES) if reference_pdf else None
    try:
        resume = await run_in_threadpool(
            svc.create_template,
            db,
            compiler,
            filename=file.filename or "template.tex",
            data=data,
            reference_pdf=ref,
            label=label,
            auto_markers=auto_markers,
        )
    except (TemplateError, TemplateUploadError) as exc:
        db.rollback()
        raise _user_error(exc) from None
    except CompilerUnavailableError as exc:
        raise HTTPException(503, str(exc)) from None
    return _template_out(resume)


@router.get("/api/templates", response_model=list[TemplateOut])
def read_templates(db: Session = Depends(get_db)) -> list[TemplateOut]:
    return [_template_out(r) for r in svc.list_templates(db)]


@router.get("/api/templates/{template_id}", response_model=TemplateOut)
def read_template(template_id: int, db: Session = Depends(get_db)) -> TemplateOut:
    try:
        return _template_out(svc.get_template(db, template_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None


@router.post("/api/templates/{template_id}/activate", response_model=TemplateOut)
def activate(template_id: int, db: Session = Depends(get_db)) -> TemplateOut:
    try:
        return _template_out(svc.activate_template(db, template_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None


@router.post("/api/resumes/render", response_model=RenderOut)
async def render_resume(
    body: RenderRequest,
    db: Session = Depends(get_db),
    compiler: Compiler = Depends(get_compiler),
) -> RenderOut:
    try:
        resume, report = await run_in_threadpool(
            svc.render_resume,
            db,
            compiler,
            contents=body.regions,
            template_id=body.template_id,
            page_limit=body.page_limit,
            fit=body.fit,
            label=body.label,
        )
    except (TemplateError, ContentError) as exc:
        raise _user_error(exc) from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except CompilerUnavailableError as exc:
        raise HTTPException(503, str(exc)) from None
    return RenderOut(resume=resume_out(resume) if resume else None, report=svc.report_json(report))


class EditIn(BaseModel):
    tex: str = Field(min_length=20, max_length=500_000)
    page_limit: int | None = Field(default=None, ge=1, le=3)


@router.post("/api/resumes/{resume_id}/edit", response_model=RenderOut)
async def edit_resume(
    resume_id: int,
    body: EditIn,
    db: Session = Depends(get_db),
    compiler: Compiler = Depends(get_compiler),
) -> RenderOut:
    try:
        resume, report = await run_in_threadpool(
            svc.edit_resume, db, compiler, resume_id, body.tex, page_limit=body.page_limit
        )
    except (TemplateError, ContentError) as exc:
        raise _user_error(exc) from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    return RenderOut(resume=resume_out(resume) if resume else None, report=svc.report_json(report))


@router.get("/api/resumes", response_model=list[ResumeOut])
def list_resumes(
    lineage_id: int | None = None, job_id: int | None = None, db: Session = Depends(get_db)
) -> list[ResumeOut]:
    stmt = select(Resume).order_by(Resume.id.desc()).limit(200)
    if lineage_id is not None:
        stmt = stmt.where(Resume.lineage_id == lineage_id)
    if job_id is not None:
        stmt = stmt.where(Resume.job_id == job_id)
    return [resume_out(r) for r in db.scalars(stmt)]


@router.get("/api/resumes/{resume_id}", response_model=ResumeOut)
def read_resume(resume_id: int, db: Session = Depends(get_db)) -> ResumeOut:
    r = db.get(Resume, resume_id)
    if r is None:
        raise HTTPException(404, "resume not found")
    return resume_out(r)


@router.get("/api/resumes/{resume_id}/{which}")
def resume_download(resume_id: int, which: str, db: Session = Depends(get_db)) -> FileResponse:
    media = {
        "pdf": "application/pdf",
        "tex": "text/x-tex; charset=utf-8",
        "log": "text/plain; charset=utf-8",
    }
    if which not in media:
        raise HTTPException(404, "unknown file")
    r = db.get(Resume, resume_id)
    path = svc.resume_file(r, which) if r else None
    if path is None:
        raise HTTPException(404, "file not found")
    name = resume_filename(db, which) if which == "pdf" else f"resume-{resume_id}.{which}"
    return FileResponse(path, media_type=media[which], filename=name)
