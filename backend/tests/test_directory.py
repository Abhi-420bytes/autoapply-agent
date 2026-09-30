"""Startup directories as a company source (bangalorestartupmap.com style and plain lists)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from app.models import OutreachCompany, OutreachSource
from app.outreach import service
from app.outreach.directory import DirectoryRead, parse_directory, read_directory, score
from app.schemas.settings import AppSettingsPatch
from app.services.settings_service import update_app_settings

ENTRIES = [
    {
        "name": "CraftifAI",
        "slug": "craftifai",
        "kind": "startup",
        "tagline": "Agentic AI for devices",
        "stage": "Seed",
        "tags": ["agentic AI", "developer tools"],
        "website": "https://craftifai.com",
    },
    {
        "name": "3one4 Capital",
        "kind": "vc",
        "website": "https://www.3one4capital.com",
        "tags": ["VC"],
    },
    {
        "name": "Hasura",
        "kind": "startup",
        "stage": "Series C+",
        "tags": ["GraphQL", "API"],
        "website": "https://hasura.io",
    },
    {"name": "Infosys", "kind": "startup", "website": "https://www.infosys.com"},
    {"name": "No Site", "kind": "startup", "website": "$undefined"},
    {"name": "Map itself", "kind": "startup", "website": "https://www.bangalorestartupmap.com/x"},
]


def _next_page() -> str:
    # Next.js streams its data as an escaped JSON string inside a script
    payload = json.dumps(json.dumps({"startups": ENTRIES}))[1:-1]
    return f'<html><script>self.__next_f.push([1,"{payload}"])</script></html>'


def test_embedded_startup_data_is_parsed_and_non_startups_dropped() -> None:
    got = parse_directory(_next_page(), "https://www.bangalorestartupmap.com/")
    assert {e.domain for e in got} == {"craftifai.com", "hasura.io"}
    by = {e.domain: e for e in got}
    roles = ["AI engineer", "backend engineer"]
    assert score(by["craftifai.com"], roles) > score(by["hasura.io"], roles)  # seed + AI fit


def test_plain_link_lists_work_too() -> None:
    html = (
        '<ul><li><a href="https://acme.ai">Acme AI</a></li><li><a href="/about">About</a></li>'
        '<li><a href="https://www.linkedin.com/company/x">LinkedIn</a></li></ul>'
    )
    got = parse_directory(html, "https://list.example.com/")
    assert [(e.name, e.domain) for e in got] == [("Acme AI", "acme.ai")]


def test_robots_txt_is_respected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        return httpx.Response(200, text=_next_page())

    got = read_directory("https://dir.example.com/", transport=httpx.MockTransport(handler))
    assert got.entries == [] and "robots.txt" in (got.blocked or "")


class _NoSearch:
    def search(self, query: str, *, count: int = 10) -> list[Any]:
        return []


def test_discovery_takes_the_best_directory_startups_first(db: Session) -> None:
    update_app_settings(
        db, AppSettingsPatch(outreach_roles=["AI engineer"], outreach_new_per_search=1)
    )
    db.add(
        OutreachSource(
            url="https://www.bangalorestartupmap.com/", label="BSM", location="Bengaluru"
        )
    )
    db.commit()
    entries = parse_directory(_next_page(), "https://www.bangalorestartupmap.com/")
    n = service.run_discovery(
        db, _NoSearch(), force=True, read_dir=lambda url: DirectoryRead(entries=entries)
    )
    assert n == 1
    c = db.query(OutreachCompany).one()
    assert c.domain == "craftifai.com" and c.location == "Bengaluru" and c.status == "new"
    assert (c.source_query or "").startswith("BSM: Seed")
    src = db.query(OutreachSource).one()
    assert src.entries_found == 2 and src.added_total == 1
    # next run continues with the next best, never repeating
    service.run_discovery(
        db, _NoSearch(), force=True, read_dir=lambda url: DirectoryRead(entries=entries)
    )
    assert {x.domain for x in db.query(OutreachCompany)} == {"craftifai.com", "hasura.io"}


def test_add_a_directory(client: Any, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    entries = parse_directory(_next_page(), "https://www.bangalorestartupmap.com/")
    monkeypatch.setattr(
        "app.api.outreach.read_directory", lambda url: DirectoryRead(entries=entries)
    )
    r = client.post(
        "/api/outreach/sources", json={"url": "startups.example.com", "location": "Pune"}
    )
    assert r.status_code == 201
    got = next(s for s in r.json() if s["url"] == "https://startups.example.com")
    assert got["entries_found"] == 2 and got["location"] == "Pune"
    assert client.delete(f"/api/outreach/sources/{got['id']}").status_code == 200
