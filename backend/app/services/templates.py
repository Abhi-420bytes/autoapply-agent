"""Template upload/activation and resume rendering (storage + versioning around app.latex)."""

from __future__ import annotations

import difflib
import io
import re
import shutil
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any

import pymupdf
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_config
from app.latex.compiler import Compiler
from app.latex.pdf import extract_text
from app.latex.regions import (
    BEGIN_RE,
    TemplateError,
    bullet_command,
    parse_regions,
    suggest_markers,
    verify_lock,
)
from app.latex.render import RenderReport, render
from app.models import Resume
from app.models.enums import AuditActor, ResumeKind
from app.services.audit import audit
from app.services.settings_service import get_app_settings

MAX_TEX_BYTES = 1_000_000
MAX_ZIP_BYTES = 20_000_000
MAX_ZIP_FILES = 200
MAX_UNZIPPED_BYTES = 60_000_000
MAX_PDF_BYTES = 10_000_000
ALLOWED_ASSET_EXT = {
    ".tex",
    ".cls",
    ".sty",
    ".bib",
    ".bst",
    ".png",
    ".jpg",
    ".jpeg",
    ".pdf",
    ".eps",
    ".otf",
    ".ttf",
    ".cfg",
    ".def",
    ".fd",
    ".txt",
}
_DOCCLASS_RE = re.compile(r"^[^%\n]*\\documentclass", re.MULTILINE)


class TemplateUploadError(ValueError):
    """User-facing problem with an upload (bad zip, no main file, doesn't compile...)."""


def data_root() -> Path:
    return Path(get_config().data_dir)


def _template_dir(resume_id: int) -> Path:
    return data_root() / "templates" / str(resume_id)


def _resume_dir(resume_id: int) -> Path:
    return data_root() / "resumes" / str(resume_id)


def _decode(data: bytes, name: str) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise TemplateUploadError(f"{name} is not UTF-8 text") from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _extract_zip(data: bytes, dest: Path) -> list[str]:
    """Safely extract an Overleaf project zip. Returns relative file paths."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise TemplateUploadError("not a valid .zip file") from None
    files = [i for i in zf.infolist() if not i.is_dir()]
    if len(files) > MAX_ZIP_FILES:
        raise TemplateUploadError(f"zip has too many files ({len(files)} > {MAX_ZIP_FILES})")
    if sum(i.file_size for i in files) > MAX_UNZIPPED_BYTES:
        raise TemplateUploadError("zip is too large when extracted")
    names: list[str] = []
    for info in files:
        rel = PurePosixPath(info.filename)
        if rel.is_absolute() or ".." in rel.parts or rel.name.startswith("."):
            continue  # zip-slip / hidden files: skip
        if rel.parts and rel.parts[0] == "__MACOSX":
            continue
        if rel.suffix.lower() not in ALLOWED_ASSET_EXT:
            continue
        target = dest.joinpath(*rel.parts)
        if not target.resolve().is_relative_to(dest.resolve()):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, target.open("wb") as out:
            shutil.copyfileobj(src, out, length=1 << 16)
        names.append(str(rel))
    return sorted(names)


def _pick_main(dest: Path, names: list[str]) -> str:
    candidates = []
    for n in names:
        if not n.endswith(".tex"):
            continue
        text = (dest / n).read_text(errors="replace")
        if _DOCCLASS_RE.search(text):
            candidates.append((n, "%%BEGIN:" in text))
    if not candidates:
        raise TemplateUploadError("no .tex file with \\documentclass found in the zip")
    marked = [n for n, has in candidates if has]
    if len(marked) == 1:
        return marked[0]
    for preferred in ("main.tex", "resume.tex"):
        if any(n == preferred for n, _ in candidates):
            return preferred
    if len(candidates) == 1:
        return candidates[0][0]
    raise TemplateUploadError(
        "several main .tex files found; add region markers to the one to use: "
        + ", ".join(n for n, _ in candidates)
    )


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text.lower())


def overleaf_check(reference_pdf: bytes, compiled_pdf: Path) -> dict[str, Any]:
    """Compare the user's Overleaf PDF with our Tectonic build of the same template."""
    try:
        with pymupdf.open(stream=reference_pdf, filetype="pdf") as doc:
            ref_pages = doc.page_count
            ref_text = "\n".join(p.get_text("text", sort=True) for p in doc)
    except Exception:
        raise TemplateUploadError("the reference PDF could not be read") from None
    ours = extract_text(compiled_pdf)
    with pymupdf.open(compiled_pdf) as doc:
        our_pages = doc.page_count
    a, b = _words(ref_text)[:5000], _words(ours)[:5000]
    similarity = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() if a and b else 0.0
    ok = ref_pages == our_pages and similarity >= 0.9
    return {
        "reference_pages": ref_pages,
        "compiled_pages": our_pages,
        "text_similarity": round(similarity, 3),
        "ok": ok,
        "message": None
        if ok
        else (
            "Tectonic's output differs from your Overleaf PDF (page count or text). "
            "Check fonts/packages; Overleaf and Tectonic may use different versions."
        ),
    }


def report_json(report: RenderReport) -> dict[str, Any]:
    return {
        "ok": report.ok,
        "compiled": report.compiled,
        "page_count": report.page_count,
        "page_limit": report.page_limit,
        "within_limit": report.within_limit,
        "alignment_ok": report.alignment_ok,
        "overfull": [asdict(b) for b in report.overfull],
        "errors": report.errors,
        "removed_bullets": [asdict(b) for b in report.removed_bullets],
        "compiles": report.compiles,
        "last_page_fill": report.last_page_fill,
        "duration_ms": report.duration_ms,
    }


def _write_outputs(resume: Resume, report: RenderReport) -> None:
    d = _resume_dir(resume.id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.tex").write_text(report.tex, encoding="utf-8")
    (d / "main.log").write_text(report.log, encoding="utf-8")
    resume.tex_path = str((d / "main.tex").relative_to(data_root()))
    if report.pdf is not None:
        (d / "main.pdf").write_bytes(report.pdf)
        resume.pdf_path = str((d / "main.pdf").relative_to(data_root()))


def create_template(
    db: Session,
    compiler: Compiler,
    *,
    filename: str,
    data: bytes,
    reference_pdf: bytes | None = None,
    label: str | None = None,
    activate: bool = True,
    auto_markers: bool = False,
) -> Resume:
    """Store and compile a base template. With `auto_markers`, a template that has no
    region markers gets one region per \\section body (header stays locked)."""
    suffix = Path(filename).suffix.lower()
    if suffix not in (".tex", ".zip"):
        raise TemplateUploadError("upload a .tex file or an Overleaf project .zip")
    limit = MAX_TEX_BYTES if suffix == ".tex" else MAX_ZIP_BYTES
    if len(data) > limit:
        raise TemplateUploadError(f"{filename} is too large")
    if reference_pdf is not None and len(reference_pdf) > MAX_PDF_BYTES:
        raise TemplateUploadError("reference PDF is too large")

    with tempfile.TemporaryDirectory(prefix="autoapply-upload-") as tmp:
        staging = Path(tmp)
        if suffix == ".zip":
            names = _extract_zip(data, staging)
            main = _pick_main(staging, names)
            tex = _decode((staging / main).read_bytes(), main)
        else:
            main = "main.tex"
            tex = _decode(data, filename)
            names = []
        # The render pipeline always writes the filled document as main.tex.
        if main != "main.tex" and (staging / main).exists():
            (staging / main).unlink()
        assets = [n for n in names if n != main]

        auto_marked: list[str] = []
        if auto_markers and not BEGIN_RE.search(tex):
            tex, auto_marked = suggest_markers(tex)
        regions = parse_regions(tex)  # TemplateError → 422 with the precise problem
        page_limit = get_app_settings(db).page_limit
        report = render(tex, {}, compiler=compiler, page_limit=page_limit, assets_dir=staging)
        if not report.compiled:
            raise TemplateUploadError("template does not compile: " + "; ".join(report.errors))

        resume = Resume(
            kind=ResumeKind.BASE_TEMPLATE,
            version=1,
            label=label or Path(filename).stem,
            tex_source=tex,
            page_limit=page_limit,
            page_count=report.page_count,
            meta={
                "main_file": main,
                "assets": assets,
                "regions": [{"name": r.name, "line": r.line} for r in regions],
                "bullet_command": bullet_command(tex),
                "auto_marked": auto_marked,
            },
            build_report=report_json(report),
        )
        db.add(resume)
        db.flush()
        resume.lineage_id = resume.id

        tdir = _template_dir(resume.id)
        if tdir.exists():
            shutil.rmtree(tdir)
        shutil.copytree(staging, tdir)
        _write_outputs(resume, report)

        if reference_pdf is not None and resume.pdf_path is not None:
            check = overleaf_check(reference_pdf, data_root() / resume.pdf_path)
            resume.meta = {**(resume.meta or {}), "overleaf_check": check}
    if activate:
        _activate(db, resume)
    audit(
        db,
        AuditActor.USER,
        "template.uploaded",
        entity_type="resume",
        entity_id=resume.id,
        details={
            "label": resume.label,
            "regions": [r.name for r in regions],
            "pages": report.page_count,
        },
    )
    db.commit()
    return resume


def _activate(db: Session, resume: Resume) -> None:
    db.execute(
        update(Resume).where(Resume.kind == ResumeKind.BASE_TEMPLATE).values(is_active=False)
    )
    resume.is_active = True


def activate_template(db: Session, resume_id: int) -> Resume:
    resume = get_template(db, resume_id)
    _activate(db, resume)
    audit(db, AuditActor.USER, "template.activated", entity_type="resume", entity_id=resume_id)
    db.commit()
    return resume


def get_template(db: Session, resume_id: int) -> Resume:
    r = db.get(Resume, resume_id)
    if r is None or r.kind is not ResumeKind.BASE_TEMPLATE:
        raise LookupError(f"template {resume_id} not found")
    return r


def active_template(db: Session) -> Resume | None:
    return db.scalar(
        select(Resume).where(Resume.kind == ResumeKind.BASE_TEMPLATE, Resume.is_active.is_(True))
    )


def list_templates(db: Session) -> list[Resume]:
    return list(
        db.scalars(
            select(Resume).where(Resume.kind == ResumeKind.BASE_TEMPLATE).order_by(Resume.id.desc())
        )
    )


def render_resume(
    db: Session,
    compiler: Compiler,
    *,
    contents: dict[str, str],
    template_id: int | None = None,
    page_limit: int | None = None,
    fit: bool = False,
    label: str | None = None,
    job_id: int | None = None,
    kind: ResumeKind = ResumeKind.MANUAL_EDIT,
) -> tuple[Resume | None, RenderReport]:
    """Render into a template and store the result as a new resume version.

    Returns (None, report) if it didn't compile, so the caller can show TeX errors."""
    template = get_template(db, template_id) if template_id is not None else active_template(db)
    if template is None:
        raise LookupError("no template uploaded yet")
    limit = page_limit or get_app_settings(db).page_limit
    report = render(
        template.tex_source,
        contents,
        compiler=compiler,
        page_limit=limit,
        assets_dir=_template_dir(template.id),
        fit=fit,
    )
    if not report.compiled:
        return None, report

    lineage = template.lineage_id or template.id
    last = db.scalar(select(func.max(Resume.version)).where(Resume.lineage_id == lineage)) or 0
    resume = Resume(
        kind=kind,
        lineage_id=lineage,
        version=last + 1,
        parent_id=template.id,
        job_id=job_id,
        label=label,
        tex_source=report.tex,
        page_limit=limit,
        page_count=report.page_count,
        meta={"template_id": template.id, "contents": report.contents},
        build_report=report_json(report),
    )
    db.add(resume)
    db.flush()
    _write_outputs(resume, report)
    audit(
        db,
        AuditActor.USER,
        "resume.rendered",
        entity_type="resume",
        entity_id=resume.id,
        details={
            "template_id": template.id,
            "pages": report.page_count,
            "ok": report.ok,
            "removed_bullets": len(report.removed_bullets),
        },
    )
    db.commit()
    return resume, report


def resume_file(resume: Resume, which: str) -> Path | None:
    rel = {"pdf": resume.pdf_path, "tex": resume.tex_path}.get(which)
    if which == "log" and resume.tex_path:
        rel = str(Path(resume.tex_path).with_name("main.log"))
    if not rel:
        return None
    path = (data_root() / rel).resolve()
    if not path.is_relative_to(data_root().resolve()) or not path.is_file():
        return None
    return path


__all__ = ["TemplateError", "TemplateUploadError"]


def edit_resume(
    db: Session, compiler: Compiler, resume_id: int, tex: str, *, page_limit: int | None = None
) -> tuple[Resume | None, RenderReport]:
    """Save a hand-edited full .tex as a new version. Everything outside the regions must be
    identical to the template (the lock); only the region bodies are taken from the edit."""
    source = db.get(Resume, resume_id)
    if source is None:
        raise LookupError(f"resume {resume_id} not found")
    template = (
        source
        if source.kind is ResumeKind.BASE_TEMPLATE
        else (db.get(Resume, source.parent_id) if source.parent_id else None)
    )
    if template is None or template.kind is not ResumeKind.BASE_TEMPLATE:
        raise LookupError("this resume has no template to edit against")
    tex = tex.replace("\r\n", "\n")
    verify_lock(template.tex_source, tex)  # TemplateError if the preamble/layout changed
    contents = {r.name: r.content for r in parse_regions(tex)}
    return render_resume(
        db,
        compiler,
        contents=contents,
        template_id=template.id,
        page_limit=page_limit or source.page_limit,
        fit=False,
        label="studio edit",
        job_id=source.job_id,
        kind=ResumeKind.MANUAL_EDIT,
    )
