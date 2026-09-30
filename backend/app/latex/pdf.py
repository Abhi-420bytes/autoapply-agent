"""PDF checks: page count (PyMuPDF) and text extraction (pdftotext, PyMuPDF fallback)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pymupdf


def page_count(pdf: Path) -> int:
    with pymupdf.open(pdf) as doc:
        return int(doc.page_count)


def extract_text(pdf: Path) -> str:
    """Text in reading order, as an ATS parser would see it.

    Prefers poppler's pdftotext (what many ATS pipelines use); falls back to PyMuPDF.
    """
    exe = shutil.which("pdftotext")
    if exe:
        result = subprocess.run(  # noqa: S603 — fixed executable, no shell
            [exe, "-enc", "UTF-8", str(pdf), "-"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout
    with pymupdf.open(pdf) as doc:
        return "\n".join(page.get_text("text", sort=True) for page in doc)


def last_page_fill(pdf: bytes) -> float:
    """How far down the LAST page the content reaches, as 0..1 of the usable height.

    Usable height = page height minus the top margin, taking the first page's top margin
    as the (symmetric) bottom margin. 1.0 = the page is full.
    """
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        if doc.page_count == 0:
            return 0.0
        first = [b for b in doc[0].get_text("blocks") if str(b[4]).strip()]
        last_page = doc[doc.page_count - 1]
        blocks = [b for b in last_page.get_text("blocks") if str(b[4]).strip()]
        if not blocks:
            return 0.0
        height = float(last_page.rect.height)
        margin = min(float(b[1]) for b in first) if first else 36.0
        usable = max(1.0, height - 2 * margin)
        bottom = max(float(b[3]) for b in blocks)
        return round(max(0.0, min(1.0, (bottom - margin) / usable)), 3)
