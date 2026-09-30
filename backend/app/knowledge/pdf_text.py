"""Extract bullets from a PDF resume (layout text, no LLM)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

BULLET_GLYPHS = "•●▪◦‣∙·-–—*■□►➢✓"
_BULLET_RE = re.compile(rf"^\s*[{re.escape(BULLET_GLYPHS)}]\s+(.*)$")
_KNOWN_SECTIONS = {
    "education",
    "experience",
    "work experience",
    "professional experience",
    "internships",
    "internship",
    "projects",
    "personal projects",
    "academic projects",
    "skills",
    "technical skills",
    "achievements",
    "awards",
    "certifications",
    "publications",
    "leadership",
    "activities",
    "extracurricular activities",
    "positions of responsibility",
    "summary",
    "profile",
    "objective",
    "research",
    "coursework",
    "volunteering",
}


@dataclass(frozen=True)
class PdfBullet:
    text: str
    section: str | None


# Characters that can start an ordinary line (phone numbers, parentheses, prices...).
_NOT_BULLET_STARTS = set("([{\"'$+#@&.,:;/\\|=<>%!?")


def _bullet_start(line: str) -> str | None:
    """Text of a bullet line, or None. Besides standard glyphs (•, -, ▪...), any single
    symbol followed by a space counts: some fonts map the bullet to an odd code point
    (e.g. \x88 or a private-use character) when text is extracted."""
    m = _BULLET_RE.match(line)
    if m:
        return m.group(1)
    s = line.lstrip()
    if len(s) > 2 and s[1] in " \t" and not s[0].isalnum() and s[0] not in _NOT_BULLET_STARTS:
        return s[2:].strip()
    return None


def _is_section(line: str) -> str | None:
    clean = re.sub(r"[^A-Za-z &]", "", line).strip()
    if clean.lower() in _KNOWN_SECTIONS:
        return clean.title()
    return None


_DATE_RE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}\b|\b(?:19|20)\d{2}\s*[-–]",
    re.IGNORECASE,
)


def _is_continuation(line: str, previous: str) -> bool:
    """A wrapped bullet line: starts lowercase, or follows a near-full-width line — but an
    entry heading (company/role line with dates) is never a continuation."""
    if _DATE_RE.search(line):
        return False
    return not line[0].isupper() or len(previous) >= 60


def pdf_lines(data: bytes) -> list[str]:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        text = "\n".join(page.get_text("text", sort=True) for page in doc)
    return [ln.rstrip() for ln in text.splitlines()]


def pdf_bullets(data: bytes) -> tuple[list[PdfBullet], str]:
    """Returns (bullets, full_text). A bullet starts at a bullet glyph and continues over
    wrapped lines until the next bullet, blank line, or section heading."""
    lines = pdf_lines(data)
    bullets: list[PdfBullet] = []
    section: str | None = None
    current: list[str] | None = None

    def flush() -> None:
        nonlocal current
        if current:
            text = re.sub(r"\s+", " ", " ".join(current)).strip()
            text = re.sub(r"(\w)- (\w)", r"\1\2", text)  # re-join hyphenated wraps
            if len(text.split()) >= 4:
                bullets.append(PdfBullet(text=text, section=section))
        current = None

    for line in lines:
        heading = _is_section(line)
        if heading:
            flush()
            section = heading
            continue
        bullet_text = _bullet_start(line)
        stripped = line.strip()
        if bullet_text is not None:
            flush()
            current = [bullet_text]
        elif current is not None and stripped and _is_continuation(stripped, current[-1]):
            current.append(stripped)
        else:
            flush()
    flush()
    return bullets, "\n".join(lines)
