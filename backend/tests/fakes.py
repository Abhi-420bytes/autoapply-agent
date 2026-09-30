from __future__ import annotations

import json
import math
import re
from collections import deque
from decimal import Decimal
from pathlib import Path

import pymupdf

from app.latex.compiler import CompileResult
from app.latex.logparse import parse_overfull
from app.llm.backend import Message, RawCompletion, RawEmbedding, Target
from app.llm.errors import LLMProviderError
from app.models.enums import LLMProviderKind


def bag_of_words(text: str, dim: int) -> list[float]:
    """Deterministic hashed bag-of-words vector: texts sharing words are similar."""
    import hashlib

    vec = [0.0] * dim
    for word in re.findall(r"[a-z0-9+#.]+", text.lower()):
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % dim] += 1.0  # noqa: S324
    vec[0] += 0.01  # never all-zero
    return vec


class FakeBackend:
    """Scripted backend: queue responses/errors per model id; records every call."""

    def __init__(self) -> None:
        self.scripts: dict[str, deque[str | LLMProviderError]] = {}
        self.calls: list[tuple[Target, list[Message]]] = []
        self.embed_calls: list[tuple[Target, list[str]]] = []
        self.models: dict[LLMProviderKind, list[str] | LLMProviderError] = {}
        self.dim = 64

    def script(self, model: str, *items: str | LLMProviderError) -> None:
        self.scripts.setdefault(model, deque()).extend(items)

    def complete(self, target, messages, *, temperature, max_tokens, timeout_s):  # type: ignore[no-untyped-def]
        self.calls.append((target, messages))
        queue = self.scripts.get(target.model)
        item = queue.popleft() if queue else "OK"
        if isinstance(item, LLMProviderError):
            raise item
        return RawCompletion(text=item, tokens_in=100, tokens_out=20, cost_usd=Decimal("0.001"))

    def embed(self, target, texts, *, timeout_s):  # type: ignore[no-untyped-def]
        self.embed_calls.append((target, texts))
        queue = self.scripts.get(target.model)
        if queue and isinstance(queue[0], LLMProviderError):
            raise queue.popleft()
        vecs = [bag_of_words(t, self.dim) for t in texts]
        return RawEmbedding(vectors=vecs, tokens_in=len(texts), cost_usd=Decimal("0.0001"))

    def web_search(self, target, query, *, timeout_s):  # type: ignore[no-untyped-def]
        from app.llm.backend import RawSearch, WebResult

        self.calls.append((target, [{"role": "user", "content": f"web_search: {query}"}]))
        queue = self.scripts.get(target.model)
        item = queue.popleft() if queue else "[]"
        if isinstance(item, LLMProviderError):
            raise item
        results = [WebResult(**r) for r in json.loads(item)]
        return RawSearch(results=results, tokens_in=10, cost_usd=Decimal(0))

    def list_models(self, kind, api_key, base_url, *, embedding=False):  # type: ignore[no-untyped-def]
        result = self.models.get(kind, [])
        if isinstance(result, LLMProviderError):
            raise result
        return result


def make_pdf(pages: int, text: str = "hello", body: str | None = None) -> bytes:
    """A PDF with `pages` pages. With `body`, the text is laid out across them (so text
    extraction sees real content, like a compiled resume)."""
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if body:
            page.insert_textbox(
                pymupdf.Rect(40, 40, 560, 800), chunks_text(body, pages, i), fontsize=7
            )
        else:
            page.insert_text((72, 72), f"{text} page {i + 1}")
    data: bytes = doc.tobytes()
    doc.close()
    return data


def chunks_text(body: str, pages: int, i: int) -> str:
    lines = body.split("\n")
    per = max(1, -(-len(lines) // pages))
    return "\n".join(lines[i * per : (i + 1) * per])


class FakeCompiler:
    """Page count = ceil(bullets / per_page). Lines containing FAIL break the compile;
    lines containing OVERFULL produce an overfull-box warning at that line."""

    def __init__(self, per_page: int = 10, bullet: str = "resumeItem") -> None:
        self.per_page = per_page
        self.bullet = bullet
        self.calls: list[tuple[str, Path | None]] = []

    def compile(self, tex: str, assets_dir: Path | None = None) -> CompileResult:
        self.calls.append((tex, assets_dir))
        if "FAIL" in tex:
            return CompileResult(
                ok=False,
                pdf=None,
                log="! Undefined control sequence.\nl.12 FAIL",
                page_count=None,
                errors=[],
                duration_ms=5,
            )
        body = tex.split("\\begin{document}", 1)[-1]  # skip the macro definition
        n = len(re.findall(rf"\\{self.bullet}\b", body))
        pages = max(1, math.ceil(n / self.per_page))
        log_lines = [
            f"Overfull \\hbox (12.5pt too wide) in paragraph at lines {i}--{i}"
            for i, line in enumerate(tex.splitlines(), start=1)
            if "OVERFULL" in line
        ]
        log = "\n".join(log_lines)
        return CompileResult(
            ok=True,
            pdf=make_pdf(pages, body=_plain_body(tex)),
            log=log,
            page_count=pages,
            overfull=parse_overfull(log),
            duration_ms=5,
        )


def _plain_body(tex: str) -> str:
    """Rough text rendering of a filled template: section titles + region text lines."""
    from app.knowledge.latex_text import to_text

    body = tex.split("\\begin{document}", 1)[-1]
    lines = []
    for raw in body.splitlines():
        if raw.strip().startswith("%"):
            continue
        t = to_text(raw)
        if t:
            lines.append(t)
    return "\n".join(lines)
