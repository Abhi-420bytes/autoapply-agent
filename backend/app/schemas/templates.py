from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import Resume


class RegionOut(BaseModel):
    name: str
    line: int
    content: str


class TemplateOut(BaseModel):
    id: int
    label: str | None
    is_active: bool
    created_at: datetime
    page_count: int | None
    page_limit: int | None
    regions: list[RegionOut]
    bullet_command: str | None
    auto_marked: list[str]  # regions added automatically at upload (empty if none)
    main_file: str | None
    assets: list[str]
    overleaf_check: dict[str, Any] | None
    build_report: dict[str, Any] | None
    tex_source: str


class SuggestMarkersOut(BaseModel):
    tex: str
    regions: list[str]


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: int | None = None  # default: the active template
    regions: dict[str, str] = Field(default_factory=dict)  # NAME -> LaTeX; omitted = keep
    page_limit: int | None = Field(default=None, ge=1, le=3)
    fit: bool = False  # drop lowest-priority bullets until within the page limit
    label: str | None = Field(default=None, max_length=255)


class ResumeOut(BaseModel):
    id: int
    kind: str
    lineage_id: int | None
    version: int
    parent_id: int | None
    job_id: int | None
    label: str | None
    created_at: datetime
    page_count: int | None
    page_limit: int | None
    build_report: dict[str, Any] | None
    contents: dict[str, str] | None
    ats_score: float | None = None
    score_report: dict[str, Any] | None = None


class RenderOut(BaseModel):
    resume: ResumeOut | None  # None when it didn't compile
    report: dict[str, Any]


def resume_out(r: Resume) -> ResumeOut:
    return ResumeOut(
        id=r.id,
        kind=r.kind.value,
        lineage_id=r.lineage_id,
        version=r.version,
        parent_id=r.parent_id,
        job_id=r.job_id,
        label=r.label,
        created_at=r.created_at,
        page_count=r.page_count,
        page_limit=r.page_limit,
        build_report=r.build_report,
        contents=(r.meta or {}).get("contents"),
        ats_score=r.ats_score,
        score_report=r.score_report,
    )
