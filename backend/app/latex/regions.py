"""Marked-region parsing, filling and the template lock (hard rule 2).

A template marks editable regions with marker lines inside the document body:

    %%BEGIN:EXPERIENCE%%
    ...content the agent may rewrite...
    %%END:EXPERIENCE%%

Everything outside the regions — preamble, packages, macros, spacing, header — is the
"skeleton". `fill()` replaces region bodies only, and `verify_lock()` proves the skeleton of
the output is byte-identical to the template's before anything is compiled.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

BEGIN_RE = re.compile(r"^[ \t]*%%BEGIN:([A-Z][A-Z0-9_]{0,39})%%[ \t]*$", re.MULTILINE)
END_RE = re.compile(r"^[ \t]*%%END:([A-Z][A-Z0-9_]{0,39})%%[ \t]*$", re.MULTILINE)
ANY_MARKER_RE = re.compile(r"%%(?:BEGIN|END):")
_LINE_MARKER_RE = re.compile(r"^[ \t]*%%(?:BEGIN|END):", re.MULTILINE)
_BEGIN_DOC_RE = re.compile(r"^[^%\n]*\\begin\{document\}", re.MULTILINE)
_END_DOC_RE = re.compile(r"^[^%\n]*\\end\{document\}", re.MULTILINE)
_SECTION_RE = re.compile(r"^[ \t]*\\section\*?\{([^}]*)\}.*$", re.MULTILINE)
_BULLET_HINT_RE = re.compile(r"^[ \t]*%%BULLET:\\([A-Za-z]+)%%[ \t]*$", re.MULTILINE)
_LEADING_CMD_RE = re.compile(r"^[ \t]*\\([A-Za-z]+)")


class TemplateError(ValueError):
    """The template's markers are malformed. Message is shown to the user."""


@dataclass(frozen=True)
class Region:
    name: str
    content: str  # text strictly between the marker lines (no trailing newline)
    start: int  # offset of the first content char (just after the BEGIN line's newline)
    end: int  # offset of the END marker line
    line: int  # 1-based line of the BEGIN marker


def parse_regions(tex: str) -> list[Region]:
    begin_doc = _BEGIN_DOC_RE.search(tex)
    end_doc = _END_DOC_RE.search(tex)
    if not begin_doc or not end_doc:
        raise TemplateError("template needs \\begin{document} and \\end{document}")

    markers = sorted(
        [(m.start(), "BEGIN", m) for m in BEGIN_RE.finditer(tex)]
        + [(m.start(), "END", m) for m in END_RE.finditer(tex)],
        key=lambda t: t[0],
    )
    # A line that starts like a marker but isn't well-formed is a mistake (mentions of the
    # syntax later in a comment line are fine).
    if len(_LINE_MARKER_RE.findall(tex)) != len(markers):
        raise TemplateError(
            "found a malformed region marker; markers must be on their own line, like "
            "%%BEGIN:EXPERIENCE%% (NAME = capital letters, digits, underscore)"
        )

    regions: list[Region] = []
    seen: set[str] = set()
    open_m: re.Match[str] | None = None
    for pos, kind, m in markers:
        name = m.group(1)
        line = tex.count("\n", 0, pos) + 1
        if pos < begin_doc.end() or pos > end_doc.start():
            raise TemplateError(
                f"marker for {name} on line {line} is outside the document body; "
                "the preamble is always locked"
            )
        if kind == "BEGIN":
            if open_m is not None:
                raise TemplateError(
                    f"region {name} (line {line}) starts inside region {open_m.group(1)}; "
                    "regions can't be nested"
                )
            if name in seen:
                raise TemplateError(f"region {name} is defined twice (line {line})")
            open_m = m
        else:
            if open_m is None or open_m.group(1) != name:
                expected = open_m.group(1) if open_m else "none open"
                raise TemplateError(
                    f"%%END:{name}%% on line {line} doesn't match the open region ({expected})"
                )
            start = open_m.end() + 1  # skip the BEGIN line's newline
            content = tex[start:pos]
            content = content[:-1] if content.endswith("\n") else content
            regions.append(
                Region(
                    name=name,
                    content=content,
                    start=start,
                    end=pos,
                    line=tex.count("\n", 0, open_m.start()) + 1,
                )
            )
            seen.add(name)
            open_m = None
    if open_m is not None:
        raise TemplateError(f"region {open_m.group(1)} is never closed with %%END:...%%")
    if not regions:
        raise TemplateError(
            "no editable regions found. Wrap each section's content in "
            "%%BEGIN:NAME%% / %%END:NAME%% lines (or use 'Suggest markers')."
        )
    return regions


def fill(tex: str, contents: dict[str, str]) -> str:
    """Replace the bodies of the named regions. Unnamed regions keep their content."""
    regions = parse_regions(tex)
    names = {r.name for r in regions}
    unknown = set(contents) - names
    if unknown:
        raise TemplateError(f"unknown region(s): {', '.join(sorted(unknown))}")
    for name, body in contents.items():
        if ANY_MARKER_RE.search(body):
            raise TemplateError(f"content for {name} must not contain region markers")

    out: list[str] = []
    cursor = 0
    for r in regions:
        out.append(tex[cursor : r.start])
        body = contents.get(r.name, r.content).rstrip("\n")
        out.append(body + "\n" if body else "")
        cursor = r.end
    out.append(tex[cursor:])
    filled = "".join(out)
    verify_lock(tex, filled)
    return filled


def skeleton(tex: str) -> str:
    """The template with every region body replaced by a placeholder."""
    out: list[str] = []
    cursor = 0
    for r in parse_regions(tex):
        out.append(tex[cursor : r.start])
        out.append(f"<<{r.name}>>\n")
        cursor = r.end
    out.append(tex[cursor:])
    return "".join(out)


def verify_lock(template: str, candidate: str) -> None:
    """Raise if anything outside the regions differs from the template."""
    if skeleton(template) != skeleton(candidate):
        raise TemplateError("template lock violated: content outside the regions changed")


def bullet_command(tex: str) -> str:
    """The macro used for bullet points: `%%BULLET:\\cmd%%` if declared, else the most
    common line-leading item-like command in the regions, else `item`."""
    hint = _BULLET_HINT_RE.search(tex)
    if hint:
        return hint.group(1)
    counts: Counter[str] = Counter()
    for r in parse_regions(tex):
        for line in r.content.splitlines():
            m = _LEADING_CMD_RE.match(line)
            if m and "item" in m.group(1).lower():
                counts[m.group(1)] += 1
    return counts.most_common(1)[0][0] if counts else "item"


def suggest_markers(tex: str) -> tuple[str, list[str]]:
    """Wrap the body of every \\section in the document with region markers.

    The header (before the first section) is left locked. Returns (new_tex, names).
    The result is only a suggestion; the user reviews it before uploading.
    """
    if BEGIN_RE.search(tex):
        raise TemplateError("template already has region markers")
    begin_doc = _BEGIN_DOC_RE.search(tex)
    end_doc = _END_DOC_RE.search(tex)
    if not begin_doc or not end_doc:
        raise TemplateError("template needs \\begin{document} and \\end{document}")
    sections = [
        m for m in _SECTION_RE.finditer(tex) if begin_doc.end() <= m.start() < end_doc.start()
    ]
    if not sections:
        raise TemplateError("no \\section{...} found in the document body to wrap")

    names: list[str] = []
    out: list[str] = []
    cursor = 0
    for i, sec in enumerate(sections):
        body_start = sec.end() + 1
        body_end = sections[i + 1].start() if i + 1 < len(sections) else end_doc.start()
        body = tex[body_start:body_end]
        stripped = body.rstrip()
        trailing = body[len(stripped) :]
        name = _region_name(sec.group(1), names)
        names.append(name)
        out.append(tex[cursor:body_start])
        out.append(
            f"%%BEGIN:{name}%%\n{stripped.strip(chr(10))}\n%%END:{name}%%{trailing or chr(10)}"
        )
        cursor = body_end
    out.append(tex[cursor:])
    result = "".join(out)
    parse_regions(result)  # sanity check
    return result, names


def _region_name(title: str, taken: list[str]) -> str:
    base = re.sub(r"[^A-Z0-9]+", "_", re.sub(r"\\[A-Za-z]+", "", title).upper()).strip("_")
    base = (base or "SECTION")[:32]
    if not base[0].isalpha():
        base = "S_" + base
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    return name
