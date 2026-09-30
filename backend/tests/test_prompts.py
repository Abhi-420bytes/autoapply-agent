from __future__ import annotations

from pathlib import Path

import pytest

from app.llm.prompts import PromptError, PromptLibrary


@pytest.fixture
def lib(tmp_path: Path) -> PromptLibrary:
    d = tmp_path / "greet"
    d.mkdir()
    (d / "v1.md").write_text("<<<system>>>\nsys v1\n<<<user>>>\nHi {{ name }}\n")
    (d / "v2.md").write_text(
        "{# description: newer #}\n<<<system>>>\nsys v2\n<<<user>>>\nHello {{ name }}\n"
    )
    return PromptLibrary(tmp_path)


def test_latest_version_is_default(lib: PromptLibrary) -> None:
    r = lib.render("greet", {"name": "Abhi"})
    assert r.ref == "greet.v2"
    assert r.messages == [
        {"role": "system", "content": "sys v2"},
        {"role": "user", "content": "Hello Abhi"},
    ]


def test_pinned_version(lib: PromptLibrary) -> None:
    assert lib.render("greet", {"name": "A"}, version=1).messages[1]["content"] == "Hi A"
    with pytest.raises(PromptError, match="no version 9"):
        lib.render("greet", {"name": "A"}, version=9)


def test_missing_variable_is_an_error(lib: PromptLibrary) -> None:
    with pytest.raises(PromptError):
        lib.render("greet", {})


def test_sandbox_blocks_unsafe_access(tmp_path: Path) -> None:
    d = tmp_path / "evil"
    d.mkdir()
    (d / "v1.md").write_text("<<<user>>>\n{{ ''.__class__.__mro__[1].__subclasses__() }}\n")
    with pytest.raises(PromptError):
        PromptLibrary(tmp_path).render("evil")


def test_invalid_names_and_missing_sections(tmp_path: Path) -> None:
    lib = PromptLibrary(tmp_path)
    with pytest.raises(PromptError):
        lib.render("../etc")
    d = tmp_path / "nosections"
    d.mkdir()
    (d / "v1.md").write_text("just text")
    with pytest.raises(PromptError, match="sections"):
        lib.render("nosections")


def test_repository_prompts_render() -> None:
    lib = PromptLibrary()
    assert {"connection_test", "structured_output", "structured_repair"} <= set(lib.names())
    lib.render("connection_test")
    lib.render("structured_output", {"schema_json": "{}"})
    lib.render("structured_repair", {"errors": "- x: bad"})
