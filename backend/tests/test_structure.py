from __future__ import annotations

from pathlib import Path

import pytest

from app.generation.structure import check_structure, projects_available
from app.latex.compiler import find_tectonic
from app.latex.fit import pick_removable
from app.latex.pdf import last_page_fill
from tests.fakes import make_pdf


def proj(name: str, n: int, pin: bool = False, long: bool = False) -> str:
    text = "Built it " + ("with a long description " * 8 if long else "fast")
    items = "\n".join(
        f"  \\resumeItem{{{text} {i}}} {'%%pin' if pin else f'%%prio:0.{i}'}" for i in range(n)
    )
    return f"\\project{{{name}}}{{Python}}\n\\begin{{bullets}}\n{items}\n\\end{{bullets}}"


def regions(*projects: str) -> dict[str, str]:
    return {"EXPERIENCE": "x", "PROJECTS": "\n".join(projects)}


def check(
    r: dict[str, str], pages: int = 1, limit: int = 1, fill: float = 0.95, available: int = 5
):  # type: ignore[no-untyped-def]
    return check_structure(
        r,
        "resumeItem",
        pages=pages,
        page_limit=limit,
        last_page_fill=fill,
        projects_available=available,
    )


def test_good_structure_passes() -> None:
    c = check(
        regions(proj("A", 2), proj("B", 3), proj("C", 2), proj("AutoApply Agent", 1, pin=True))
    )
    assert c.ok and c.projects == 3 and c.signature_bullets == 1


@pytest.mark.parametrize(
    ("r", "kw", "problem"),
    [
        (
            regions(proj("A", 2), proj("B", 2), proj("C", 2), proj("AutoApply Agent", 1, pin=True)),
            {"fill": 0.6},
            "only 60% full",
        ),
        (
            regions(proj("A", 2), proj("B", 2), proj("C", 2), proj("AutoApply Agent", 1, pin=True)),
            {"pages": 1, "limit": 2},
            "only 1 of the allowed 2",
        ),
        (
            regions(proj("A", 2), proj("B", 2), proj("AutoApply Agent", 1, pin=True)),
            {},
            "exactly 3 projects",
        ),
        (
            regions(proj("A", 2), proj("B", 1), proj("C", 2), proj("AutoApply Agent", 1, pin=True)),
            {},
            "fewer than 2 bullets",
        ),
        (
            regions(proj("A", 2), proj("B", 2), proj("C", 2), proj("AutoApply Agent", 2, pin=True)),
            {},
            "ONE line",
        ),
        (
            regions(
                proj("A", 2),
                proj("B", 2),
                proj("C", 2),
                proj("AutoApply Agent", 1, pin=True, long=True),
            ),
            {},
            "ONE line",
        ),
        (
            regions(*(proj(n, 2) for n in "ABCDE"), proj("AutoApply Agent", 1, pin=True)),
            {},
            "exactly 3",
        ),
    ],
)
def test_structure_problems(r: dict[str, str], kw: dict[str, float], problem: str) -> None:
    c = check(r, **kw)  # type: ignore[arg-type]
    assert not c.ok and any(problem in p for p in c.problems), c.problems


def test_fewer_projects_than_three_available_is_ok() -> None:
    c = check(
        regions(proj("A", 2), proj("B", 2), proj("AutoApply Agent", 1, pin=True)), available=2
    )
    assert c.ok and c.projects_required == 2
    assert projects_available(["b1", "r3", "r4", "base:X", "profile"], 1) == 3


def test_trimming_keeps_two_bullets_per_project() -> None:
    contents = {
        "PROJECTS": proj("A", 2) + "\n" + proj("B", 3),
        "EXPERIENCE": "\\begin{bullets}\n  \\resumeItem{a} %%prio:0.9\n  \\resumeItem{b} %%prio:0.8\n\\end{bullets}",
    }
    victim = pick_removable(contents, "resumeItem")
    assert victim is not None
    # project A (2 bullets) is protected; B can go to 2; experience can go to 1
    assert not (victim.region == "PROJECTS" and victim.group == 0)


def test_last_page_fill_measures_real_pdfs() -> None:
    short = last_page_fill(make_pdf(1, body="one line"))
    full = last_page_fill(make_pdf(1, body="\n".join(f"line {i} of content" for i in range(75))))
    assert short < 0.2 and full > 0.8


@pytest.mark.skipif(find_tectonic() is None, reason="tectonic not installed")
def test_fill_on_real_compile() -> None:
    from app.latex.compiler import TectonicCompiler
    from app.latex.render import render

    t = (Path(__file__).resolve().parents[1] / "templates" / "sample" / "resume.tex").read_text()
    sparse = render(t, {}, compiler=TectonicCompiler(), page_limit=1)
    assert sparse.last_page_fill is not None and sparse.last_page_fill < 0.5
    items = "\n".join(
        f"  \\resumeItem{{Bullet {i}: implemented a production feature with measurable impact across services.}} %%prio:0.{i:02d}"
        for i in range(1, 40)
    )
    full = render(
        t,
        {"EXPERIENCE": "\\entry{A}{B}{C}{D}\n\\begin{bullets}\n" + items + "\n\\end{bullets}"},
        compiler=TectonicCompiler(),
        page_limit=1,
        fit=True,
    )
    assert full.page_count == 1 and (full.last_page_fill or 0) > 0.9
