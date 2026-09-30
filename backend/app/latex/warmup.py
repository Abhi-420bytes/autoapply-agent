"""Pre-warm Tectonic's package cache through the real compiler (run at image build).

python -m app.latex.warmup
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from app.latex.compiler import TectonicCompiler

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


ATTEMPTS = 3


def main() -> int:
    compiler = TectonicCompiler(timeout_s=1800)
    for doc in (TEMPLATES / "warmup.tex", TEMPLATES / "sample" / "resume.tex"):
        for attempt in range(1, ATTEMPTS + 1):
            result = compiler.compile(doc.read_text(encoding="utf-8"))
            status = "ok" if result.ok else "FAILED"
            print(
                f"warmup {doc.name} (attempt {attempt}): {status} "
                f"in {result.duration_ms / 1000:.1f}s"
            )
            if result.ok:
                break
            for e in result.errors:
                print(f"  {e.message} (line {e.line})", file=sys.stderr)
            if attempt < ATTEMPTS:  # bundle downloads fail transiently; already-fetched
                time.sleep(15 * attempt)  # files stay cached, so a retry resumes
        else:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
