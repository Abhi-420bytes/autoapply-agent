from __future__ import annotations

from pathlib import Path

import pytest

from app.latex.regions import (
    TemplateError,
    bullet_command,
    fill,
    parse_regions,
    skeleton,
    suggest_markers,
    verify_lock,
)

SAMPLE = (Path(__file__).resolve().parents[1] / "templates" / "sample" / "resume.tex").read_text()

DOC = r"""\documentclass{article}
\newcommand{\cvitem}[1]{\item #1}
\begin{document}
Header stays
%%BEGIN:A%%
old a
%%END:A%%
middle
%%BEGIN:B%%
  \cvitem{x}
  \cvitem{y}
%%END:B%%
\end{document}
"""


def test_parse_sample_template() -> None:
    names = [r.name for r in parse_regions(SAMPLE)]
    assert names == ["EDUCATION", "EXPERIENCE", "PROJECTS", "SKILLS"]
    assert bullet_command(SAMPLE) == "resumeItem"


def test_fill_replaces_only_named_regions() -> None:
    out = fill(DOC, {"A": "new a\nsecond line"})
    assert "new a\nsecond line\n%%END:A%%" in out
    assert "\\cvitem{x}" in out  # B untouched
    assert skeleton(out) == skeleton(DOC)
    assert fill(DOC, {"A": ""}).count("%%BEGIN:A%%\n%%END:A%%") == 1


def test_fill_rejects_unknown_and_marker_injection() -> None:
    with pytest.raises(TemplateError, match="unknown region"):
        fill(DOC, {"Z": "x"})
    with pytest.raises(TemplateError, match="markers"):
        fill(DOC, {"A": "%%END:A%%\n\\usepackage{evil}\n%%BEGIN:A%%"})


def test_lock_detects_changes_outside_regions() -> None:
    changed = DOC.replace("Header stays", "Header CHANGED")
    with pytest.raises(TemplateError, match="lock"):
        verify_lock(DOC, changed)
    verify_lock(DOC, DOC.replace("old a", "anything"))


@pytest.mark.parametrize(
    ("tex", "message"),
    [
        (DOC.replace("%%END:A%%", ""), "starts inside"),
        (DOC.replace("%%BEGIN:B%%", "%%BEGIN:A%%").replace("%%END:B%%", "%%END:A%%"), "twice"),
        (DOC.replace("%%END:B%%\n", ""), "never closed"),
        (DOC.replace("%%END:A%%", "%%END:B%%"), "doesn't match"),
        (
            DOC.replace("\\begin{document}", "%%BEGIN:P%%\n%%END:P%%\n\\begin{document}"),
            "outside the document",
        ),
        (DOC.replace("%%BEGIN:A%%", "%%BEGIN:lower%%"), "malformed"),
        (DOC.replace("%%BEGIN:A%%", "  %%BEGIN:A%% trailing"), "malformed"),
        ("\\documentclass{article}\\begin{document}x\\end{document}", "no editable regions"),
        ("no document here", "begin{document}"),
    ],
)
def test_marker_errors(tex: str, message: str) -> None:
    with pytest.raises(TemplateError, match=message):
        parse_regions(tex)


def test_marker_syntax_mentioned_in_a_comment_is_fine() -> None:
    tex = "% usage: wrap with %%BEGIN:NAME%% ... %%END:NAME%%\n" + DOC
    assert len(parse_regions(tex)) == 2


def test_bullet_command_detection_and_hint() -> None:
    assert bullet_command(DOC) == "cvitem"
    hinted = DOC.replace("\\begin{document}", "\\begin{document}\n%%BULLET:\\myBullet%%")
    assert bullet_command(hinted) == "myBullet"


def test_suggest_markers_wraps_sections_and_keeps_header() -> None:
    unmarked = r"""\documentclass{article}
\begin{document}
\textbf{My Name}
\section{Work Experience}
Job one
\section*{Projects \& Talks}
Proj
\end{document}
"""
    tex, names = suggest_markers(unmarked)
    assert names == ["WORK_EXPERIENCE", "PROJECTS_TALKS"]
    regions = {r.name: r.content for r in parse_regions(tex)}
    assert regions == {"WORK_EXPERIENCE": "Job one", "PROJECTS_TALKS": "Proj"}
    assert tex.startswith("\\documentclass{article}\n\\begin{document}\n\\textbf{My Name}")
    with pytest.raises(TemplateError, match="already"):
        suggest_markers(tex)
