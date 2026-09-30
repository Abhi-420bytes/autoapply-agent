"""Bullets inside regions, with priorities, for page-limit fitting (hard rule 3).

A bullet is one line that starts with the template's bullet macro (e.g. \\resumeItem{...})
and has balanced braces. Generated content can annotate bullets with trailing comments:

    \\resumeItem{...}  %%prio:0.35     lower = less valuable, removed first
    \\resumeItem{...}  %%pin           never removed (e.g. the AutoApply signature project)

Unannotated bullets get prio 0.5, slightly lower the further down their group they are.
An entry keeps at least 1 bullet (projects at least 2) when trimming.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.latex.sanitize import strip_comments

_PRIO_RE = re.compile(r"%%prio:([0-9]*\.?[0-9]+)")
_PIN_RE = re.compile(r"%%pin\b")
DEFAULT_PRIO = 0.5


@dataclass(frozen=True)
class Bullet:
    region: str
    line_index: int  # index into region.splitlines()
    text: str
    priority: float
    pinned: bool
    group: int  # consecutive bullet lines share a group


def _balanced(s: str) -> bool:
    depth, i = 0, 0
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        depth += {"{": 1, "}": -1}.get(s[i], 0)
        if depth < 0:
            return False
        i += 1
    return depth == 0


def find_bullets(region: str, content: str, command: str) -> list[Bullet]:
    lead = re.compile(rf"^\s*\\{re.escape(command)}(?![A-Za-z])")
    bullets: list[Bullet] = []
    group, pos_in_group, prev_was_bullet = -1, 0, False
    for idx, line in enumerate(content.splitlines()):
        code = strip_comments(line)
        is_bullet = bool(lead.match(code)) and _balanced(code)
        if not is_bullet:
            if code.strip():  # blank lines don't break a group
                prev_was_bullet = False
            continue
        if not prev_was_bullet:
            group += 1
            pos_in_group = 0
        prio_m = _PRIO_RE.search(line)
        prio = float(prio_m.group(1)) if prio_m else DEFAULT_PRIO - 0.001 * pos_in_group
        bullets.append(
            Bullet(
                region=region,
                line_index=idx,
                text=code.strip(),
                priority=prio,
                pinned=bool(_PIN_RE.search(line)),
                group=group,
            )
        )
        pos_in_group += 1
        prev_was_bullet = True
    return bullets


def min_bullets(region: str) -> int:
    """Fewest bullets an entry may be trimmed to: projects keep at least 2 each."""
    return 2 if "PROJECT" in region.upper() else 1


def pick_removable(contents: dict[str, str], command: str) -> Bullet | None:
    """The lowest-priority bullet that may be removed, or None."""
    candidates: list[Bullet] = []
    for region, content in contents.items():
        bullets = find_bullets(region, content, command)
        sizes: dict[int, int] = {}
        for b in bullets:
            sizes[b.group] = sizes.get(b.group, 0) + 1
        floor = min_bullets(region)
        candidates += [b for b in bullets if not b.pinned and sizes[b.group] > floor]
    if not candidates:
        return None
    # lowest priority first; ties → later regions/lines first (usually less important)
    return min(candidates, key=lambda b: (b.priority, -b.line_index))


def remove_bullet(contents: dict[str, str], bullet: Bullet) -> dict[str, str]:
    lines = contents[bullet.region].splitlines()
    del lines[bullet.line_index]
    return {**contents, bullet.region: "\n".join(lines)}
