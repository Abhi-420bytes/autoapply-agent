"""Versioned prompt templates.

Layout:  prompts/<name>/v<N>.md

A template is Jinja2 (sandboxed, StrictUndefined) split into role sections:

    {# description: what this prompt is for #}
    <<<system>>>
    You are ...
    <<<user>>>
    Job description:
    {{ jd_text }}

Files are re-read on every render, so prompts can be edited without a restart. Adding a
new version (v2.md) makes it the default; a task can pin a version via its params.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from app.llm.backend import Message

_DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "prompts"
_VERSION_RE = re.compile(r"^v(\d+)\.md$")
_SECTION_RE = re.compile(r"^<<<(system|user|assistant)>>>\s*$", re.MULTILINE)
_NAME_RE = re.compile(r"^[a-z0-9_]+$")


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class RenderedPrompt:
    name: str
    version: int
    messages: list[Message]

    @property
    def ref(self) -> str:
        return f"{self.name}.v{self.version}"


class PromptLibrary:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _DEFAULT_ROOT
        self._env = SandboxedEnvironment(
            undefined=StrictUndefined, autoescape=False, keep_trailing_newline=False
        )

    def versions(self, name: str) -> list[int]:
        if not _NAME_RE.match(name):
            raise PromptError(f"invalid prompt name {name!r}")
        folder = self.root / name
        if not folder.is_dir():
            return []
        return sorted(int(m.group(1)) for p in folder.iterdir() if (m := _VERSION_RE.match(p.name)))

    def names(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and self.versions(p.name))

    def render(
        self, name: str, variables: dict[str, Any] | None = None, version: int | None = None
    ) -> RenderedPrompt:
        available = self.versions(name)
        if not available:
            raise PromptError(f"no prompt templates found for {name!r} in {self.root}")
        v = version if version is not None else available[-1]
        if v not in available:
            raise PromptError(f"prompt {name!r} has no version {v} (have {available})")

        source = (self.root / name / f"v{v}.md").read_text(encoding="utf-8")
        try:
            rendered = self._env.from_string(source).render(**(variables or {}))
        except TemplateError as exc:
            raise PromptError(f"failed to render {name}.v{v}: {exc}") from exc
        return RenderedPrompt(name=name, version=v, messages=_split_sections(rendered, name))


def _split_sections(text: str, name: str) -> list[Message]:
    parts = _SECTION_RE.split(text)
    # parts = [preamble, role1, body1, role2, body2, ...]
    if len(parts) < 3:
        raise PromptError(f"prompt {name!r} has no <<<system>>>/<<<user>>> sections")
    messages: list[Message] = []
    for role, body in zip(parts[1::2], parts[2::2], strict=True):
        content = body.strip()
        if content:
            messages.append({"role": role, "content": content})
    if not messages:  # fragments (e.g. structured_output) may be system-only
        raise PromptError(f"prompt {name!r} has only empty sections")
    return messages
