from __future__ import annotations

import pytest

from app.latex.sanitize import (
    ContentError,
    build_policy,
    check_content,
    escape_text,
    strip_comments,
)

TEMPLATE = r"""\documentclass{article}
\newcommand{\resumeItem}[1]{\item\small{#1}}
\newenvironment{bullets}{\begin{itemize}}{\end{itemize}}
\begin{document}
%%BEGIN:EXP%%
\textbf{Intern} \hfill 2025 \\
\vspace{-2pt}
\begin{bullets}
  \resumeItem{Did a thing with 40\% gain}
\end{bullets}
%%END:EXP%%
\end{document}
"""
POLICY = build_policy(TEMPLATE)


def ok(content: str) -> None:
    check_content("EXP", content, POLICY)


def bad(content: str, match: str) -> None:
    with pytest.raises(ContentError, match=match):
        check_content("EXP", content, POLICY)


def test_template_style_content_is_allowed() -> None:
    ok(r"""\textbf{Backend Intern} \hfill May 2025 \\
\vspace{-2pt}
\begin{bullets}
  \resumeItem{Built \href{https://x.io}{a tool} in C\# \& Go, cut costs by 30\%} %%prio:0.4
  \resumeItem{\emph{Shipped} v2 --- fast} % \input{/etc/passwd} is only a comment
\end{bullets}""")


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (r"\input{/etc/passwd}", "not allowed"),
        (r"\immediate\write18{rm -rf /}", "not allowed"),
        (r"\def\x{1}", "not allowed"),
        (r"\makeatletter", "not allowed"),
        (r"\vspace{-10pt}", "spacing"),
        (r"\vspace*{-2pt}x\vspace{3pt}", "spacing"),
        (r"\setlength{\itemsep}{0pt}", "not allowed"),
        (r"\newpage", "not allowed"),
        (r"\fontsize{8}{9}\selectfont", "not allowed"),
        (r"\customThing{x}", "isn't used anywhere"),
        (r"\textbf{unclosed", "unbalanced"),
        (r"closed}", "unbalanced"),
        (r"\begin{tabular}{ll}a&b\end{tabular}", "environment 'tabular'"),
        (r"\begin{document}", "environment 'document'"),
        (r"line \\[6pt] next", "spacing"),
        ("^^5cinput", "character codes"),
        (r"\@secondoftwo", "not allowed"),
        ("%%BEGIN:X%%", "markers"),
        ("x" * 20_001, "too long"),
    ],
)
def test_rejected_content(content: str, match: str) -> None:
    bad(content, match)


def test_strip_comments_respects_escaped_percent() -> None:
    assert strip_comments(r"50\% done % comment \input{x}") == r"50\% done "
    assert strip_comments("a\\\\% comment") == "a\\\\"


def test_escape_text_roundtrip_is_allowed() -> None:
    raw = r"C++ & C# at 100% for $5 {x} ~ ^ \ _"
    esc = escape_text(raw)
    assert (
        esc
        == r"C++ \& C\# at 100\% for \$5 \{x\} \textasciitilde{} \textasciicircum{} \textbackslash{} \_"
    )
    ok(r"\resumeItem{" + esc + "}")
