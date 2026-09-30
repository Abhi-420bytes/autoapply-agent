"""Hard rule 9: no module may call a provider SDK directly — only app/llm/backend.py."""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
FORBIDDEN = {
    "litellm",
    "openai",
    "anthropic",
    "google.generativeai",
    "google.genai",
    "groq",
    "voyageai",
    "ollama",
    "sentence_transformers",
    "cohere",
    "mistralai",
}
ALLOWED = {APP / "llm" / "backend.py"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_only_backend_imports_provider_sdks() -> None:
    offenders = []
    for path in APP.rglob("*.py"):
        if path in ALLOWED:
            continue
        for name in _imports(path):
            if any(name == f or name.startswith(f + ".") for f in FORBIDDEN):
                offenders.append(f"{path.relative_to(APP)} imports {name}")
    assert offenders == []
