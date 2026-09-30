"""Past-resume ingestion: .tex/.pdf → bullet bank (deterministic parsing, no LLM)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_config
from app.knowledge.bank import (
    BULLET,
    bullet_chunk_text,
    content_hash,
    embed_missing,
    near_duplicates,
)
from app.knowledge.latex_text import tex_bullets
from app.knowledge.pdf_text import pdf_bullets
from app.knowledge.skills import SkillTagger
from app.llm.errors import LLMError
from app.llm.gateway import LLMGateway
from app.models import Bullet, Embedding, GitHubRepo, Resume
from app.models.enums import AuditActor, BulletSource, ResumeKind
from app.services.audit import audit
from app.services.settings_service import get_profile

log = logging.getLogger(__name__)
MAX_FILE_BYTES = 5_000_000


class IngestError(ValueError):
    pass


@dataclass
class IngestReport:
    resume_id: int
    filename: str
    found: int
    added: int
    exact_duplicates: int
    near_duplicates: int
    warnings: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)


def build_tagger(db: Session) -> SkillTagger:
    """Vocabulary = built-in terms + the user's profile skills + GitHub languages/topics."""
    extra: list[str] = list(get_profile(db).skills)
    for langs, topics in db.execute(select(GitHubRepo.languages, GitHubRepo.topics)):
        extra += list(langs or {})
        extra += [t.replace("-", " ") for t in topics or []]
    return SkillTagger(extra)


def ingest_past_resume(
    db: Session,
    gateway: LLMGateway,
    session_factory: Callable[[], Session],
    *,
    filename: str,
    data: bytes,
    source_date: date | None = None,
) -> IngestReport:
    suffix = Path(filename).suffix.lower()
    if suffix not in (".tex", ".pdf"):
        raise IngestError(f"{filename}: upload a .tex or .pdf resume")
    if len(data) > MAX_FILE_BYTES:
        raise IngestError(f"{filename} is too large")

    if suffix == ".tex":
        try:
            source = data.decode("utf-8").replace("\r\n", "\n")
        except UnicodeDecodeError:
            raise IngestError(f"{filename} is not UTF-8 text") from None
        parsed = [(b.text, b.section, b.heading) for b in tex_bullets(source)]
    else:
        try:
            found_pdf, source = pdf_bullets(data)
        except Exception:
            raise IngestError(f"{filename} could not be read as a PDF") from None
        parsed = [(b.text, b.section, None) for b in found_pdf]
        if not source.strip():
            raise IngestError(f"{filename} has no extractable text (scanned image?)")

    # 1) Dedupe BEFORE opening a write transaction: the near-duplicate check calls the
    #    embedding API, and a DB transaction must never be held open across a network call.
    known_hashes = set(db.scalars(select(Bullet.content_hash)))
    exact_duplicates = 0
    fresh: list[tuple[str, str | None, str | None, str]] = []
    for text, section, heading in parsed:
        h = content_hash(text)
        if h in known_hashes:
            exact_duplicates += 1
            continue
        known_hashes.add(h)
        fresh.append((text, section, heading, h))
    tagger = build_tagger(db)
    db.rollback()  # end the read transaction before the network call
    dup_of = near_duplicates(
        gateway, db, [bullet_chunk_text(t, sec, head) for t, sec, head, _ in fresh]
    )
    db.rollback()

    # 2) Write everything in one short transaction.
    resume = Resume(
        kind=ResumeKind.PAST,
        version=1,
        label=filename,
        tex_source=source,
        meta={
            "filename": filename,
            "format": suffix[1:],
            "source_date": source_date.isoformat() if source_date else None,
        },
    )
    db.add(resume)
    db.flush()
    resume.lineage_id = resume.id
    folder = Path(get_config().data_dir) / "past" / str(resume.id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"original{suffix}").write_bytes(data)
    resume.tex_path = str((folder / f"original{suffix}").relative_to(get_config().data_dir))

    report = IngestReport(
        resume_id=resume.id,
        filename=filename,
        found=len(parsed),
        added=0,
        exact_duplicates=exact_duplicates,
        near_duplicates=0,
    )
    if not parsed:
        report.warnings.append("no bullet points were recognised in this file")

    for (text, section, heading, h), dup in zip(fresh, dup_of, strict=True):
        if dup is not None:
            report.near_duplicates += 1
            continue
        db.add(
            Bullet(
                text=text,
                section=section,
                heading=heading,
                skills=tagger.tags(text),
                source_type=BulletSource.PAST_RESUME,
                source_resume_id=resume.id,
                source_date=source_date,
                content_hash=h,
            )
        )
        report.added += 1
        report.bullets.append(text)

    audit(
        db,
        AuditActor.USER,
        "knowledge.resume_ingested",
        entity_type="resume",
        entity_id=resume.id,
        details={"filename": filename, "found": report.found, "added": report.added},
    )
    db.commit()
    try:
        embed_missing(gateway, session_factory)
    except LLMError as exc:
        report.warnings.append(f"bullets saved; embedding will be retried later ({exc})")
    return report


def delete_past_resume(db: Session, resume_id: int) -> None:
    resume = db.get(Resume, resume_id)
    if resume is None or resume.kind is not ResumeKind.PAST:
        raise LookupError(f"past resume {resume_id} not found")
    ids = list(db.scalars(select(Bullet.id).where(Bullet.source_resume_id == resume_id)))
    if ids:
        db.execute(
            delete(Embedding).where(Embedding.owner_type == BULLET, Embedding.owner_id.in_(ids))
        )
        db.execute(delete(Bullet).where(Bullet.id.in_(ids)))
    db.delete(resume)
    audit(
        db,
        AuditActor.USER,
        "knowledge.resume_deleted",
        entity_type="resume",
        entity_id=resume_id,
        details={"bullets_removed": len(ids)},
    )
    db.commit()
