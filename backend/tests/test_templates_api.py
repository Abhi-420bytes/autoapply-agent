from __future__ import annotations

import io
import os
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.templates import get_compiler
from tests.fakes import FakeCompiler, make_pdf

SAMPLE = (Path(__file__).resolve().parents[1] / "templates" / "sample" / "resume.tex").read_bytes()


@pytest.fixture
def fake_compiler(client: TestClient) -> Iterator[FakeCompiler]:
    fc = FakeCompiler(per_page=10)
    client.app.dependency_overrides[get_compiler] = lambda: fc  # type: ignore[attr-defined]
    yield fc


def upload(client: TestClient, name: str, data: bytes, **extra: bytes) -> dict:  # type: ignore[type-arg]
    files = {"file": (name, data)}
    if "reference_pdf" in extra:
        files["reference_pdf"] = ("ref.pdf", extra["reference_pdf"])
    r = client.post("/api/templates", files=files, data={"label": "Mine"})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def test_upload_tex_and_activate(client: TestClient, fake_compiler: FakeCompiler) -> None:
    t = upload(client, "resume.tex", SAMPLE)
    assert t["is_active"] and t["page_count"] == 1 and t["bullet_command"] == "resumeItem"
    assert [r["name"] for r in t["regions"]] == ["EDUCATION", "EXPERIENCE", "PROJECTS", "SKILLS"]
    assert t["build_report"]["ok"] is True

    t2 = upload(client, "resume.tex", SAMPLE)
    listed = client.get("/api/templates").json()
    assert [x["is_active"] for x in listed] == [True, False]  # newest active
    client.post(f"/api/templates/{t['id']}/activate")
    assert client.get(f"/api/templates/{t2['id']}").json()["is_active"] is False


def test_upload_rejects_bad_markers_and_types(
    client: TestClient, fake_compiler: FakeCompiler
) -> None:
    broken = SAMPLE.replace(b"%%END:SKILLS%%", b"")
    r = client.post("/api/templates", files={"file": ("r.tex", broken)})
    assert r.status_code == 422 and "never closed" in r.text
    r = client.post("/api/templates", files={"file": ("r.docx", b"x")})
    assert r.status_code == 422
    r = client.post("/api/templates", files={"file": ("r.tex", b"\xff\xfe bad")})
    assert r.status_code == 422 and "UTF-8" in r.text
    r = client.post(
        "/api/templates", files={"file": ("r.tex", SAMPLE.replace(b"\\begin{bullets}", b"FAIL", 1))}
    )
    assert r.status_code == 422 and "does not compile" in r.text


def test_upload_overleaf_zip_safely(client: TestClient, fake_compiler: FakeCompiler) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("resume.tex", SAMPLE)
        z.writestr("other.tex", b"\\section{not main}")
        z.writestr("custom.cls", b"% class")
        z.writestr("img/logo.png", b"png")
        z.writestr("../../evil.tex", b"x")
        z.writestr("__MACOSX/._resume.tex", b"x")
        z.writestr("run.sh", b"rm -rf /")
    t = upload(client, "project.zip", buf.getvalue())
    assert t["main_file"] == "resume.tex"
    assert t["assets"] == ["custom.cls", "img/logo.png", "other.tex"]
    stored = Path(os.environ["DATA_DIR"]) / "templates" / str(t["id"])
    assert sorted(p.name for p in stored.rglob("*") if p.is_file()) == [
        "custom.cls",
        "logo.png",
        "other.tex",
    ]
    assert not (stored / "resume.tex").exists()  # main file lives in the DB, filled per render


def test_overleaf_fidelity_check(client: TestClient, fake_compiler: FakeCompiler) -> None:
    t = upload(client, "resume.tex", SAMPLE, reference_pdf=make_pdf(2, "different"))
    check = t["overleaf_check"]
    assert check["reference_pages"] == 2 and check["compiled_pages"] == 1 and not check["ok"]


def test_suggest_markers_endpoint(client: TestClient) -> None:
    unmarked = (
        b"\\documentclass{article}\n\\begin{document}\n\\section{Skills}\nPython\n\\end{document}\n"
    )
    r = client.post("/api/templates/suggest-markers", files={"file": ("r.tex", unmarked)})
    assert r.status_code == 200 and r.json()["regions"] == ["SKILLS"]
    assert "%%BEGIN:SKILLS%%\nPython\n%%END:SKILLS%%" in r.json()["tex"]


def test_render_versions_and_downloads(client: TestClient, fake_compiler: FakeCompiler) -> None:
    assert client.post("/api/resumes/render", json={}).status_code == 404  # no template yet
    t = upload(client, "resume.tex", SAMPLE)
    body = {"regions": {"SKILLS": "\\textbf{Languages:} Go, Rust"}, "label": "try 1"}
    r = client.post("/api/resumes/render", json=body).json()
    assert (
        r["report"]["ok"] and r["resume"]["version"] == 2 and r["resume"]["lineage_id"] == t["id"]
    )
    assert r["resume"]["contents"]["SKILLS"] == "\\textbf{Languages:} Go, Rust"
    r2 = client.post("/api/resumes/render", json=body).json()
    assert r2["resume"]["version"] == 3

    rid = r["resume"]["id"]
    pdf = client.get(f"/api/resumes/{rid}/pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    tex = client.get(f"/api/resumes/{rid}/tex").text
    assert "Go, Rust" in tex and "\\usepackage[margin=0.5in]{geometry}" in tex
    assert client.get(f"/api/resumes/{rid}/secret").status_code == 404
    assert len(client.get(f"/api/resumes?lineage_id={t['id']}").json()) == 3


def test_render_rejects_unsafe_content(client: TestClient, fake_compiler: FakeCompiler) -> None:
    upload(client, "resume.tex", SAMPLE)
    r = client.post("/api/resumes/render", json={"regions": {"SKILLS": "\\input{/etc/passwd}"}})
    assert r.status_code == 422 and "not allowed" in r.text
    r = client.post("/api/resumes/render", json={"regions": {"NOPE": "x"}})
    assert r.status_code == 422 and "unknown region" in r.text


def test_render_compile_failure_returns_report(
    client: TestClient, fake_compiler: FakeCompiler
) -> None:
    upload(client, "resume.tex", SAMPLE)
    r = client.post("/api/resumes/render", json={"regions": {"SKILLS": "FAIL"}}).json()
    assert r["resume"] is None and r["report"]["compiled"] is False and r["report"]["errors"]


def test_upload_unmarked_template_with_auto_markers(
    client: TestClient, fake_compiler: FakeCompiler
) -> None:
    import re

    unmarked = re.sub(rb"%%(BEGIN|END):[A-Z]+%%\n", b"", SAMPLE)
    r = client.post("/api/templates", files={"file": ("r.tex", unmarked)})
    assert r.status_code == 422 and "no editable regions" in r.text

    r = client.post(
        "/api/templates", files={"file": ("r.tex", unmarked)}, data={"auto_markers": "true"}
    )
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["auto_marked"] == ["EDUCATION", "EXPERIENCE", "PROJECTS", "TECHNICAL_SKILLS"]
    assert [x["name"] for x in t["regions"]] == t["auto_marked"]
    assert "\\begin{center}" in t["tex_source"].split("%%BEGIN:EDUCATION%%")[0]  # header locked


def test_auto_markers_leaves_marked_template_alone(
    client: TestClient, fake_compiler: FakeCompiler
) -> None:
    r = client.post(
        "/api/templates", files={"file": ("r.tex", SAMPLE)}, data={"auto_markers": "true"}
    )
    assert r.status_code == 201 and r.json()["auto_marked"] == []
