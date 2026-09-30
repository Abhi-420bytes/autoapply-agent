"""Resume structure rules checked on every attempt.

- Fill the page(s): the resume uses all `page_limit` pages and the last one is ≥ 85% full.
- Projects: exactly 3 projects besides AutoApply (when the facts have that many), each with
  at least 2 bullets. The AutoApply signature entry is one line (a single short bullet).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.knowledge.latex_text import to_text
from app.latex.fit import find_bullets
from app.latex.sanitize import strip_comments

FILL_TARGET = 0.85
PROJECTS_WANTED = 3
MIN_PROJECT_BULLETS = 2
SIGNATURE_MAX_CHARS = 110  # about one printed line in a typical resume template


@dataclass
class StructureCheck:
    region: str | None
    projects: int  # entries besides the AutoApply signature
    projects_required: int
    thin_projects: int  # projects with fewer than 2 bullets
    signature_bullets: int
    signature_chars: int
    pages: int | None
    page_limit: int
    last_page_fill: float | None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return asdict(self) | {"ok": self.ok}


def check_structure(
    regions: dict[str, str],
    command: str,
    *,
    pages: int | None,
    page_limit: int,
    last_page_fill: float | None,
    projects_available: int,
) -> StructureCheck:
    region = next((r for r in regions if "PROJECT" in r.upper()), None)
    groups: dict[int, list[Any]] = {}
    if region:
        for b in find_bullets(region, regions[region], command):
            groups.setdefault(b.group, []).append(b)
    signature = [
        g
        for g in groups.values()
        if any(b.pinned or "autoapply" in to_text(b.text).lower() for b in g)
    ]
    others = [g for g in groups.values() if g not in signature]
    sig = signature[0] if signature else []
    sig_chars = sum(len(to_text(strip_comments(b.text))) for b in sig)
    required = min(PROJECTS_WANTED, projects_available)
    check = StructureCheck(
        region=region,
        projects=len(others),
        projects_required=required,
        thin_projects=sum(1 for g in others if len(g) < MIN_PROJECT_BULLETS),
        signature_bullets=len(sig),
        signature_chars=sig_chars,
        pages=pages,
        page_limit=page_limit,
        last_page_fill=last_page_fill,
    )
    p = check.problems
    if pages is not None and pages < page_limit:
        p.append(
            f"The resume uses only {pages} of the allowed {page_limit} page(s): add more "
            "content from the facts (for a 2-page limit, include other strong projects and "
            "achievements even if less related to this job)."
        )
    elif last_page_fill is not None and last_page_fill < FILL_TARGET:
        p.append(
            f"The last page is only {round(last_page_fill * 100)}% full; fill it to ~95%: "
            "more bullets per project/experience from the facts, or another relevant entry. "
            "Write slightly MORE than fits; low-priority bullets are trimmed automatically."
        )
    if region and check.projects < required:
        p.append(
            f"Include exactly {required} projects besides AutoApply Agent "
            f"(found {check.projects}); pick the most relevant, else your strongest."
        )
    elif region and check.projects > PROJECTS_WANTED:
        p.append(
            f"Include exactly {PROJECTS_WANTED} projects besides AutoApply Agent "
            f"(found {check.projects}); keep the most relevant."
        )
    if check.thin_projects:
        p.append(
            f"{check.thin_projects} project(s) have fewer than {MIN_PROJECT_BULLETS} "
            "bullets; every project needs at least 2 (3 is better; extras are trimmed if "
            "the page overflows)."
        )
    if sig and (len(sig) > 1 or sig_chars > SIGNATURE_MAX_CHARS):
        p.append(
            "The AutoApply Agent entry must be ONE line: its heading plus a single short "
            f"bullet (under {SIGNATURE_MAX_CHARS - 20} characters) marked %%pin."
        )
    return check


def projects_available(fact_ids: list[str], base_project_groups: int) -> int:
    """How many distinct projects the facts can support (repos + current resume projects)."""
    return sum(1 for f in fact_ids if f.startswith("r")) + base_project_groups
