from __future__ import annotations

from pathlib import Path

import pymupdf

from app.knowledge.latex_text import tex_bullets, to_text
from app.knowledge.pdf_text import pdf_bullets
from app.knowledge.skills import SkillTagger

SAMPLE = (Path(__file__).resolve().parents[1] / "templates" / "sample" / "resume.tex").read_text()

JAKE = r"""\documentclass{article}
\newcommand{\resumeItem}[1]{\item\small{#1}}
\newcommand{\resumeSubheading}[4]{#1 #2 #3 #4}
\begin{document}
\section{Experience}
  \resumeSubHeadingListStart
    \resumeSubheading{Acme Corp}{June 2024 -- Aug 2024}{Backend Intern}{Remote}
      \resumeItemListStart
        \resumeItem{Built a \textbf{FastAPI} service handling 1.2k req/s with 99.9\% uptime}
        \resumeItem{Migrated CI to GitHub Actions, cutting build time by 35\%} % comment \input{x}
      \resumeItemListEnd
  \resumeSubHeadingListEnd
\section{Projects}
  \begin{itemize}
    \item Wrote a \href{https://x.io}{Rust CLI} that parses 10GB logs in under 5 seconds
    \item Too short
  \end{itemize}
\section{Technical Skills}
 \textbf{Languages}{: Python, Go}
\end{document}
"""


def test_to_text() -> None:
    assert (
        to_text(r"Cut costs by 40\% with \textbf{C\#} \& \emph{Go} --- fast")
        == "Cut costs by 40% with C# & Go — fast"
    )
    assert to_text(r"\href{https://a.b}{my site} \vspace{-2pt} done") == "my site done"


def test_tex_bullets_jake_style() -> None:
    bullets = tex_bullets(JAKE)
    assert [b.text for b in bullets] == [
        "Built a FastAPI service handling 1.2k req/s with 99.9% uptime",
        "Migrated CI to GitHub Actions, cutting build time by 35%",
        "Wrote a Rust CLI that parses 10GB logs in under 5 seconds",
    ]
    assert bullets[0].section == "Experience" and bullets[0].heading == "Acme Corp"
    assert bullets[2].section == "Projects"


def test_tex_bullets_sample_template() -> None:
    bullets = tex_bullets(SAMPLE)
    assert len(bullets) == 3
    assert (
        bullets[0].heading == "Software Engineering Intern" and bullets[0].section == "Experience"
    )
    assert bullets[2].heading == "AutoApply Agent" and bullets[2].section == "Projects"


def _pdf(lines: list[str]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 50
    for line in lines:
        page.insert_text((50, y), line, fontsize=10)
        y += 14
    data: bytes = doc.tobytes()
    doc.close()
    return data


def test_pdf_bullets_sections_and_wrapping() -> None:
    data = _pdf(
        [
            "EXPERIENCE",
            "Acme Corp                                   Jun 2024 - Aug 2024",
            "- Built a payments reconciliation service in Python and Kafka that processed",
            "  2M events per day with exactly-once delivery",
            "- Added tracing across 12 services",
            "Beta Labs                                   Jan 2024 - May 2024",
            "- Shipped an internal dashboard in React used by the ops team daily",
            "PROJECTS",
            "- Chess engine in Rust with alpha-beta search and a UCI interface",
        ]
    )
    bullets, text = pdf_bullets(data)
    assert [(b.section, b.text) for b in bullets] == [
        (
            "Experience",
            "Built a payments reconciliation service in Python and Kafka that "
            "processed 2M events per day with exactly-once delivery",
        ),
        ("Experience", "Added tracing across 12 services"),
        ("Experience", "Shipped an internal dashboard in React used by the ops team daily"),
        ("Projects", "Chess engine in Rust with alpha-beta search and a UCI interface"),
    ]
    assert "Acme Corp" in text


def test_skill_tagger_is_literal_and_boundary_aware() -> None:
    t = SkillTagger(["LangGraph", "Havlock API"])
    assert t.tags("Built C++ and C# tools; used Go and golang, k8s, Postgres") == [
        "C#",
        "C++",
        "Go",
        "Kubernetes",
        "PostgreSQL",
    ]
    assert t.tags("going to the gym, react to feedback") == ["React"]  # case-insensitive term
    assert "Go" not in t.tags("going good")  # Go is case-sensitive
    assert t.tags("Integrated the Havlock API with LangGraph") == ["Havlock API", "LangGraph"]
    assert t.tags("nothing technical here") == []


def test_pdf_bullets_with_odd_bullet_code_points() -> None:
    from app.knowledge.pdf_text import _bullet_start

    assert (
        _bullet_start("\x88  Built repeatable REST API pipelines")
        == "Built repeatable REST API pipelines"
    )
    assert _bullet_start(" Developed a model") == "Developed a model"  # private-use glyph
    assert _bullet_start("• Standard bullet") == "Standard bullet"
    for not_bullet in (
        "+91 90000 00000",
        "(2024) Award",
        "$5M raised",
        "CGPA: 7.2",
        "Python, Java",
    ):
        assert _bullet_start(not_bullet) is None
