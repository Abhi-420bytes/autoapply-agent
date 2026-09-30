"""Convert LaTeX fragments to plain text and extract bullets from .tex resumes."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.latex.sanitize import strip_comments

_ESCAPES = {
    r"\&": "&",
    r"\%": "%",
    r"\$": "$",
    r"\#": "#",
    r"\_": "_",
    r"\{": "{",
    r"\}": "}",
    r"\textasciitilde{}": "~",
    r"\textasciicircum{}": "^",
    r"\textbackslash{}": "\\",
    "---": "\u2014",
    "--": "\u2013",
    "``": "\u201c",
    "''": "\u201d",
    "~": " ",
    r"\,": " ",
    r"\ ": " ",
    r"\\": " ",
}
_DROP_WITH_ARG = {"vspace", "hspace", "label", "includegraphics", "setlength"}
_CMD_WITH_ARGS_RE = re.compile(r"\\([A-Za-z]+)\*?((?:\s*\[[^\]]*\])*)")


def _read_group(s: str, i: int) -> tuple[str, int] | None:
    """If s[i] (after spaces) starts a {...} group, return (inner, index after it)."""
    j = i
    while j < len(s) and s[j] in " \t":
        j += 1
    if j >= len(s) or s[j] != "{":
        return None
    depth, k = 0, j
    while k < len(s):
        ch = s[k]
        if ch == "\\":
            k += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[j + 1 : k], k + 1
        k += 1
    return None


def command_args(s: str, start: int, max_args: int = 9) -> tuple[list[str], int]:
    args: list[str] = []
    i = start
    while len(args) < max_args:
        g = _read_group(s, i)
        if g is None:
            break
        args.append(g[0])
        i = g[1]
    return args, i


def to_text(tex: str) -> str:
    """Best-effort LaTeX → plain text: commands dropped, their arguments kept."""
    s = strip_comments(tex)
    s = s.replace(r"\href", r"\hrefx")  # \href{url}{text}: keep only the text
    out: list[str] = []
    i = 0
    while i < len(s):
        if s.startswith(r"\hrefx", i):
            args, j = command_args(s, i + 6, 2)
            out.append(to_text(args[1]) if len(args) == 2 else "")
            i = j
            continue
        matched = next((e for e in _ESCAPES if s.startswith(e, i) and e.startswith("\\")), None)
        if matched:
            out.append(_ESCAPES[matched])
            i += len(matched)
            continue
        if s[i] == "\\":
            m = _CMD_WITH_ARGS_RE.match(s, i)
            if m:
                name = m.group(1)
                args, j = command_args(s, m.end())
                if name not in _DROP_WITH_ARG and name not in ("begin", "end"):
                    out.append(" ".join(to_text(a) for a in args))
                i = j
                continue
            i += 2
            continue
        if s[i] in "{}$":
            i += 1
            continue
        out.append(s[i])
        i += 1
    text = "".join(out)
    for k in ("---", "--", "``", "''", "~"):
        text = text.replace(k, _ESCAPES[k])
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class TexBullet:
    text: str
    section: str | None
    heading: str | None


_SECTION_RE = re.compile(r"\\section\*?\s*\{")
_ITEMISH_RE = re.compile(r"\\([A-Za-z]*[Ii]tem[A-Za-z]*)\b")
_HEADING_CMD_RE = re.compile(
    r"\\([A-Za-z]*(?:[Hh]eading|[Ee]ntry|[Pp]roject|[Ss]ubheading)[A-Za-z]*)\b"
)


def tex_bullets(tex: str) -> list[TexBullet]:
    """Bullets from a .tex resume: arguments of item-like macros (\\resumeItem{...}) and
    bare \\item text, with the enclosing section and entry heading."""
    body = strip_comments(tex.split("\\begin{document}", 1)[-1])
    events: list[tuple[int, str, str]] = []  # (pos, kind, value)
    for m in _SECTION_RE.finditer(body):
        g = _read_group(body, m.end() - 1)
        if g:
            events.append((m.start(), "section", to_text(g[0])))
    for m in _HEADING_CMD_RE.finditer(body):
        args, _ = command_args(body, m.end(), 1)
        if args and "item" not in m.group(1).lower():
            events.append((m.start(), "heading", to_text(args[0])))
    for m in _ITEMISH_RE.finditer(body):
        name = m.group(1)
        if name.lower() in ("itemize", "itemsep", "item") and name != "item":
            continue
        if name == "item":
            end = len(body)
            nxt = re.compile(r"\\item\b|\\end\{|\\begin\{").search(body, m.end())
            if nxt:
                end = nxt.start()
            text = to_text(body[m.end() : end])
        else:
            if name.lower().endswith(("liststart", "listend", "sep")):
                continue
            args, _ = command_args(body, m.end())
            if not args:
                continue
            text = to_text(args[-1]) if len(args) > 1 else to_text(args[0])
        events.append((m.start(), "bullet", text))

    events.sort()
    section = heading = None
    out: list[TexBullet] = []
    for _, kind, value in events:
        if kind == "section":
            section, heading = value, None
        elif kind == "heading":
            heading = value
        elif len(value.split()) >= 4:  # skip fragments like skill lists
            out.append(TexBullet(text=value, section=section, heading=heading))
    return out
