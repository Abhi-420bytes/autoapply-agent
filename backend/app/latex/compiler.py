"""Tectonic compilation in an isolated temporary directory."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.latex.logparse import OverfullBox, TexError, parse_errors, parse_overfull
from app.latex.pdf import page_count

MAIN = "main.tex"
COMPAT = "autoapply-compat.tex"

# Tectonic runs XeTeX, while Overleaf defaults to pdfLaTeX. Popular resume templates
# (e.g. Jake's) use pdfTeX-only primitives to make PDF text copyable for ATS parsers
# (\input{glyphtounicode}, \pdfgentounicode=1). XeTeX emits Unicode text natively, so
# these are defined as harmless no-ops when the engine lacks them. The shim is loaded on
# the same line as the document's first line, so TeX line numbers don't shift.
COMPAT_SOURCE = r"""\ifdefined\pdfglyphtounicode\else\def\pdfglyphtounicode#1#2{}\fi
\ifdefined\pdfgentounicode\else\newcount\pdfgentounicode\fi
\ifdefined\pdfminorversion\else\newcount\pdfminorversion\fi
\ifdefined\pdfsuppresswarningpagegroup\else\newcount\pdfsuppresswarningpagegroup\fi
\endinput
"""
COMPILE_TIMEOUT_S = 180  # first run may download packages into the cache
_MAX_PARALLEL = threading.BoundedSemaphore(2)


class CompilerUnavailableError(RuntimeError):
    pass


@dataclass
class CompileResult:
    ok: bool
    pdf: bytes | None
    log: str
    page_count: int | None
    errors: list[TexError] = field(default_factory=list)
    overfull: list[OverfullBox] = field(default_factory=list)
    duration_ms: int = 0


class Compiler(Protocol):
    def compile(self, tex: str, assets_dir: Path | None = None) -> CompileResult: ...


def find_tectonic() -> str | None:
    explicit = os.environ.get("TECTONIC_BIN")
    if explicit and Path(explicit).is_file():
        return explicit
    found = shutil.which("tectonic")
    if found:
        return found
    # dev convenience: a binary dropped next to the venv's python
    local = Path(sys.executable).parent / "tectonic"
    return str(local) if local.is_file() else None


class TectonicCompiler:
    def __init__(self, binary: str | None = None, timeout_s: float = COMPILE_TIMEOUT_S) -> None:
        self.binary = binary or find_tectonic()
        self.timeout_s = timeout_s

    def compile(self, tex: str, assets_dir: Path | None = None) -> CompileResult:
        if not self.binary:
            raise CompilerUnavailableError("tectonic is not installed")
        with _MAX_PARALLEL, tempfile.TemporaryDirectory(prefix="autoapply-tex-") as tmp:
            work = Path(tmp)
            if assets_dir is not None and assets_dir.is_dir():
                shutil.copytree(assets_dir, work, dirs_exist_ok=True)
            (work / COMPAT).write_text(COMPAT_SOURCE, encoding="utf-8")
            (work / MAIN).write_text(f"\\input{{{COMPAT[:-4]}}}" + tex, encoding="utf-8")
            start = time.monotonic()
            try:
                proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
                    [
                        self.binary,
                        "--untrusted",
                        "--keep-logs",
                        "--chatter",
                        "minimal",
                        "--outdir",
                        str(work),
                        str(work / MAIN),
                    ],
                    cwd=work,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return CompileResult(
                    ok=False,
                    pdf=None,
                    log="",
                    page_count=None,
                    errors=[TexError(f"compile timed out after {self.timeout_s:.0f}s", None)],
                    duration_ms=int((time.monotonic() - start) * 1000),
                )
            duration = int((time.monotonic() - start) * 1000)
            log_path = work / "main.log"
            tex_log = log_path.read_text(errors="replace") if log_path.exists() else ""
            pdf_path = work / "main.pdf"
            errors = parse_errors(tex_log)
            if proc.returncode != 0 and not errors:
                errors = [
                    TexError((proc.stderr or proc.stdout).strip()[-800:] or "compile failed", None)
                ]
            ok = proc.returncode == 0 and pdf_path.exists()
            return CompileResult(
                ok=ok,
                pdf=pdf_path.read_bytes() if ok else None,
                log=tex_log,
                page_count=page_count(pdf_path) if ok else None,
                errors=[] if ok else errors,
                overfull=parse_overfull(tex_log),
                duration_ms=duration,
            )
