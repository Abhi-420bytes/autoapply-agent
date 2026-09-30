"""Real Tectonic compiles. Skipped when the binary isn't available."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.latex.compiler import TectonicCompiler, find_tectonic
from app.latex.pdf import extract_text
from app.latex.render import render

pytestmark = pytest.mark.skipif(find_tectonic() is None, reason="tectonic not installed")
SAMPLE = (Path(__file__).resolve().parents[1] / "templates" / "sample" / "resume.tex").read_text()


def test_sample_template_compiles_to_one_clean_page(tmp_path: Path) -> None:
    r = render(SAMPLE, {}, compiler=TectonicCompiler(), page_limit=1)
    assert r.ok and r.page_count == 1 and r.overfull == []
    pdf = tmp_path / "out.pdf"
    pdf.write_bytes(r.pdf or b"")
    text = extract_text(pdf)
    assert "Abhi Ram" in text and "AutoApply Agent" in text
    # reading order: sections appear in document order
    assert text.index("Education") < text.index("Experience") < text.index("Projects")


def test_real_overfull_box_is_detected_in_its_region() -> None:
    long_url = "\\href{https://example.com}{" + "x" * 300 + "}"
    r = render(
        SAMPLE,
        {"SKILLS": "\\textbf{Tools:} " + long_url},
        compiler=TectonicCompiler(),
        page_limit=1,
    )
    assert r.compiled and not r.alignment_ok
    assert any(b.region == "SKILLS" and b.amount_pt > 1 for b in r.overfull)


def test_real_fit_to_one_page() -> None:
    bullets = "\n".join(
        f"  \\resumeItem{{Bullet {i}: implemented a feature with measurable impact across "
        f"several production services and teams.}} %%prio:0.{i:02d}"
        for i in range(1, 80)
    )
    exp = "\\entry{Intern}{2025}{Corp}{Remote}\n\\begin{bullets}\n" + bullets + "\n\\end{bullets}"
    r = render(
        SAMPLE,
        {"EXPERIENCE": exp},
        compiler=TectonicCompiler(),
        page_limit=1,
        fit=True,
        max_compiles=60,
    )
    assert r.ok and r.page_count == 1 and r.removed_bullets
    removed = [b.priority for b in r.removed_bullets]
    assert removed == sorted(removed)  # lowest value first


def test_compile_error_is_reported() -> None:
    result = TectonicCompiler().compile(
        "\\documentclass{article}\\begin{document}\\nosuchcmd\\end{document}"
    )
    assert not result.ok and any("Undefined control sequence" in e.message for e in result.errors)


def test_pdftex_only_primitives_compile_under_xetex() -> None:
    """Jake's-resume style ATS lines must not break Tectonic (XeTeX)."""
    tex = SAMPLE.replace(
        "\\begin{document}",
        "\\input{glyphtounicode}\n\\pdfgentounicode=1\n\\begin{document}",
    )
    r = render(tex, {}, compiler=TectonicCompiler(), page_limit=1)
    assert r.ok, r.errors


def test_overfull_line_numbers_unaffected_by_compat_shim() -> None:
    r = render(
        SAMPLE,
        {"SKILLS": "\\href{https://e.com}{" + "y" * 300 + "}"},
        compiler=TectonicCompiler(),
        page_limit=1,
    )
    skills_line = next(i for i, line in enumerate(r.tex.splitlines(), 1) if "y" * 50 in line)
    assert any(b.line_start == skills_line for b in r.overfull)
