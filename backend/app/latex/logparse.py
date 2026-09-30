"""Parse TeX log files for errors and overfull boxes."""

from __future__ import annotations

import re
from dataclasses import dataclass

_OVERFULL_RE = re.compile(
    r"^Overfull \\([hv])box \(([\d.]+)pt too (?:wide|high)\)"
    r"(?: [^\n]*? at lines? (\d+)(?:--(\d+))?)?",
    re.MULTILINE,
)
_ERROR_RE = re.compile(r"^! (.+)$", re.MULTILINE)
_ERROR_LINE_RE = re.compile(r"^l\.(\d+)", re.MULTILINE)


@dataclass(frozen=True)
class OverfullBox:
    kind: str  # "hbox" | "vbox"
    amount_pt: float
    line_start: int | None
    line_end: int | None
    region: str | None = None  # filled in by the caller when the line is inside a region


@dataclass(frozen=True)
class TexError:
    message: str
    line: int | None


def parse_overfull(log: str) -> list[OverfullBox]:
    out = []
    for m in _OVERFULL_RE.finditer(log):
        start = int(m.group(3)) if m.group(3) else None
        end = int(m.group(4)) if m.group(4) else start
        out.append(
            OverfullBox(
                kind=m.group(1) + "box", amount_pt=float(m.group(2)), line_start=start, line_end=end
            )
        )
    return out


def parse_errors(log: str) -> list[TexError]:
    errors = []
    for m in _ERROR_RE.finditer(log):
        after = log[m.end() : m.end() + 2000]
        line_m = _ERROR_LINE_RE.search(after)
        errors.append(
            TexError(message=m.group(1).strip(), line=int(line_m.group(1)) if line_m else None)
        )
    return errors
