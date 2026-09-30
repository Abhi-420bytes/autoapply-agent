from __future__ import annotations

from pathlib import Path

import pytest

from app.latex.fit import find_bullets, pick_removable
from app.latex.render import render
from app.latex.sanitize import ContentError
from tests.fakes import FakeCompiler

SAMPLE = (Path(__file__).resolve().parents[1] / "templates" / "sample" / "resume.tex").read_text()


def items(n: int, start: int = 0, prio: bool = True) -> str:
    lines = [
        f"  \\resumeItem{{bullet {i}}}" + (f" %%prio:0.{i:02d}" if prio else "")
        for i in range(start, start + n)
    ]
    return "\\begin{bullets}\n" + "\n".join(lines) + "\n\\end{bullets}"


def test_find_bullets_groups_priorities_and_pins() -> None:
    content = "\\entry{a}{b}{c}{d}\n\\begin{bullets}\n  \\resumeItem{x} %%prio:0.9\n\n  \\resumeItem{y}\n\\end{bullets}\n\\begin{bullets}\n  \\resumeItem{z} %%pin\n\\end{bullets}"
    bs = find_bullets("R", content, "resumeItem")
    assert [(b.text, b.group, b.pinned) for b in bs] == [
        ("\\resumeItem{x}", 0, False),
        ("\\resumeItem{y}", 0, False),
        ("\\resumeItem{z}", 1, True),
    ]
    assert bs[0].priority == 0.9 and bs[1].priority == pytest.approx(0.499)


def test_pick_removable_respects_pins_and_last_in_group() -> None:
    contents = {
        "A": "\\begin{bullets}\n\\resumeItem{only one} %%prio:0.01\n\\end{bullets}",
        "B": "\\begin{bullets}\n\\resumeItem{keep} %%pin\n\\resumeItem{low} %%prio:0.2\n\\resumeItem{hi} %%prio:0.8\n\\end{bullets}",
    }
    victim = pick_removable(contents, "resumeItem")
    assert victim is not None and victim.text == "\\resumeItem{low}"
    assert pick_removable({"A": contents["A"]}, "resumeItem") is None


def test_render_within_limit() -> None:
    fc = FakeCompiler(per_page=10)
    r = render(SAMPLE, {"EXPERIENCE": items(5)}, compiler=fc, page_limit=1)
    assert r.ok and r.page_count == 1 and r.compiles == 1 and r.pdf


def test_over_limit_without_fit_reports_error() -> None:
    r = render(SAMPLE, {"EXPERIENCE": items(25)}, compiler=FakeCompiler(), page_limit=1)
    assert not r.ok and not r.within_limit and r.page_count == 3
    assert "exceeds the limit of 1" in r.errors[0]


def test_fit_drops_lowest_priority_bullets_until_within_limit() -> None:
    fc = FakeCompiler(per_page=10)
    # 13 in EXPERIENCE + base PROJECTS has 1 → 14 total, limit 1 page = 10 bullets
    r = render(SAMPLE, {"EXPERIENCE": items(13, start=1)}, compiler=fc, page_limit=1, fit=True)
    assert r.ok and r.page_count == 1
    assert [b.priority for b in r.removed_bullets] == [0.01, 0.02, 0.03, 0.04]  # minimal set
    assert r.compiles <= 6  # initial + all-removed + binary search
    assert "bullet 1}" not in r.contents["EXPERIENCE"] and "bullet 13}" in r.contents["EXPERIENCE"]
    # the signature project's single bullet is never removed
    assert "AutoApply Agent" in r.contents["PROJECTS"]


def test_fit_gives_up_when_nothing_removable() -> None:
    r = render(
        SAMPLE, {"EXPERIENCE": items(1)}, compiler=FakeCompiler(per_page=1), page_limit=1, fit=True
    )
    assert not r.ok and "no more removable bullets" in r.errors[0]


def test_invalid_content_rejected_before_compiling() -> None:
    fc = FakeCompiler()
    with pytest.raises(ContentError):
        render(SAMPLE, {"SKILLS": r"\input{secrets}"}, compiler=fc, page_limit=1)
    assert fc.calls == []


def test_overfull_boxes_are_mapped_to_regions() -> None:
    r = render(
        SAMPLE, {"SKILLS": "\\textbf{Tools:} OVERFULL"}, compiler=FakeCompiler(), page_limit=1
    )
    assert r.compiled and not r.alignment_ok and not r.ok
    assert [(b.region, b.amount_pt) for b in r.overfull] == [("SKILLS", 12.5)]


def test_compile_failure_is_reported() -> None:
    r = render(SAMPLE, {"SKILLS": "FAIL"}, compiler=FakeCompiler(), page_limit=1)
    assert not r.compiled and r.errors and r.pdf is None


def test_fit_large_overflow_uses_few_compiles_and_minimal_removals() -> None:
    fc = FakeCompiler(per_page=10)
    r = render(
        SAMPLE, {"EXPERIENCE": items(90, start=1, prio=False)}, compiler=fc, page_limit=1, fit=True
    )
    assert r.ok and r.page_count == 1
    assert len(r.removed_bullets) == 81  # 91 bullets incl. PROJECTS → keep exactly 10
    assert r.compiles <= 10


def test_fit_reports_failure_when_even_full_trim_overflows() -> None:
    r = render(
        SAMPLE, {"EXPERIENCE": items(3)}, compiler=FakeCompiler(per_page=1), page_limit=1, fit=True
    )
    assert not r.ok and r.page_count == 2 and len(r.removed_bullets) == 2
