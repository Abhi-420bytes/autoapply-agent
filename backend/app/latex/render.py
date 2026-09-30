"""Fill → validate → compile → check pages/overfull → (optionally) shrink and retry."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from app.latex.compiler import Compiler, CompileResult
from app.latex.fit import pick_removable, remove_bullet
from app.latex.logparse import OverfullBox
from app.latex.pdf import last_page_fill
from app.latex.regions import bullet_command, fill, parse_regions
from app.latex.sanitize import build_policy, check_all

DEFAULT_OVERFULL_TOLERANCE_PT = 1.0
MAX_COMPILES = 16  # binary search: enough for ~16k removable bullets


@dataclass(frozen=True)
class RemovedBullet:
    region: str
    text: str
    priority: float


@dataclass
class RenderReport:
    ok: bool  # compiled, within page limit, no overfull boxes beyond tolerance
    compiled: bool
    page_count: int | None
    page_limit: int
    within_limit: bool
    overfull: list[OverfullBox]
    alignment_ok: bool
    errors: list[str]
    removed_bullets: list[RemovedBullet] = field(default_factory=list)
    compiles: int = 0
    duration_ms: int = 0
    last_page_fill: float | None = None  # 0..1: how full the last page is
    tex: str = ""
    contents: dict[str, str] = field(default_factory=dict)
    pdf: bytes | None = None
    log: str = ""


def _region_spans(tex: str) -> list[tuple[str, int, int]]:
    """(name, first content line, last content line), 1-based, in the filled tex."""
    spans = []
    for r in parse_regions(tex):
        first = tex.count("\n", 0, r.start) + 1
        last = tex.count("\n", 0, r.end)
        spans.append((r.name, first, last))
    return spans


def _attach_regions(tex: str, boxes: list[OverfullBox]) -> list[OverfullBox]:
    spans = _region_spans(tex)
    out = []
    for b in boxes:
        region = next(
            (n for n, a, z in spans if b.line_start is not None and a <= b.line_start <= z), None
        )
        out.append(replace(b, region=region))
    return out


def render(
    template_tex: str,
    contents: dict[str, str],
    *,
    compiler: Compiler,
    page_limit: int,
    assets_dir: Path | None = None,
    fit: bool = False,
    overfull_tolerance_pt: float = DEFAULT_OVERFULL_TOLERANCE_PT,
    max_compiles: int = MAX_COMPILES,
) -> RenderReport:
    """Render region contents into the template. Raises TemplateError/ContentError for
    invalid input (before compiling anything)."""
    policy = build_policy(template_tex)
    check_all(contents, policy)
    # Work on the full content map so fitting can also trim base-template bullets.
    current = {r.name: r.content for r in parse_regions(template_tex)} | contents
    command = bullet_command(template_tex)
    stats = {"compiles": 0, "ms": 0}

    def attempt(state: dict[str, str]) -> tuple[str, CompileResult]:
        tex = fill(template_tex, state)
        result = compiler.compile(tex, assets_dir)
        stats["compiles"] += 1
        stats["ms"] += result.duration_ms
        return tex, result

    tex, result = attempt(current)
    removed: list[RemovedBullet] = []

    if result.ok and fit and (result.page_count or 0) > page_limit:
        # Removal order is deterministic (lowest priority first), so precompute every
        # state and binary-search the fewest removals that fit: ~log2(n) compiles, and
        # the page stays as full as possible.
        states: list[tuple[dict[str, str], list[RemovedBullet]]] = [(current, [])]
        while True:
            state, gone = states[-1]
            victim = pick_removable(state, command)
            if victim is None:
                break
            states.append(
                (
                    remove_bullet(state, victim),
                    [*gone, RemovedBullet(victim.region, victim.text, victim.priority)],
                )
            )
        if len(states) > 1:
            best: tuple[int, str, CompileResult] | None = None
            last = len(states) - 1
            tex_all, res_all = attempt(states[last][0])
            if res_all.ok and (res_all.page_count or 0) <= page_limit:
                best = (last, tex_all, res_all)
                lo, hi = 1, last - 1  # answer in [lo, last]
                while lo <= hi and stats["compiles"] < max_compiles:
                    mid = (lo + hi) // 2
                    t_mid, r_mid = attempt(states[mid][0])
                    if r_mid.ok and (r_mid.page_count or 0) <= page_limit:
                        best, hi = (mid, t_mid, r_mid), mid - 1
                    else:
                        lo = mid + 1
                k, tex, result = best
            else:  # even removing every removable bullet doesn't fit
                k, tex, result = last, tex_all, res_all
            current, removed = states[k]

    if not result.ok:
        return RenderReport(
            ok=False,
            compiled=False,
            page_count=None,
            page_limit=page_limit,
            within_limit=False,
            overfull=[],
            alignment_ok=False,
            errors=[f"{e.message}" + (f" (line {e.line})" if e.line else "") for e in result.errors]
            or ["compile failed"],
            removed_bullets=removed,
            compiles=stats["compiles"],
            duration_ms=stats["ms"],
            tex=tex,
            contents=current,
            log=result.log,
        )

    pages = result.page_count or 0
    within = pages <= page_limit
    overfull = _attach_regions(tex, result.overfull)
    alignment_ok = all(b.amount_pt <= overfull_tolerance_pt for b in overfull)
    errors = []
    if not within:
        errors.append(
            f"{pages} page(s) exceeds the limit of {page_limit}"
            + ("; no more removable bullets" if fit else "")
        )
    page_fill = last_page_fill(result.pdf) if result.pdf else None
    return RenderReport(
        last_page_fill=page_fill,
        ok=within and alignment_ok,
        compiled=True,
        page_count=pages,
        page_limit=page_limit,
        within_limit=within,
        overfull=overfull,
        alignment_ok=alignment_ok,
        errors=errors,
        removed_bullets=removed,
        compiles=stats["compiles"],
        duration_ms=stats["ms"],
        tex=tex,
        contents=current,
        pdf=result.pdf,
        log=result.log,
    )
