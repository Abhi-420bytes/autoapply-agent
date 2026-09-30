"""Desktop app: launcher helpers and the in-app controls (quit, start at login)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from app.api import desktop as desktop_api
from app.desktop import load_or_create_keys


def test_first_launch_creates_private_keys_once(tmp_path: Path) -> None:
    first = load_or_create_keys(tmp_path)
    assert first["MASTER_KEY"] and first["API_TOKEN"]
    assert load_or_create_keys(tmp_path) == first  # reused on later launches
    if sys.platform != "win32":
        assert oct((tmp_path / "keys.env").stat().st_mode & 0o777) == "0o600"


def test_desktop_controls_are_hidden_outside_the_desktop_app(client: Any) -> None:
    assert client.get("/api/desktop").json()["desktop"] is False
    assert client.post("/api/desktop/quit").status_code == 404
    assert client.put("/api/desktop/autostart", json={"enabled": True}).status_code == 404


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS login item")
def test_start_at_login_and_quit_in_the_desktop_app(
    client: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AUTOAPPLY_DESKTOP", "1")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    desktop_api.quit_requested.clear()

    r = client.put("/api/desktop/autostart", json={"enabled": True})
    assert r.status_code == 200 and r.json()["autostart"] is True
    plist = tmp_path / "Library" / "LaunchAgents" / "com.abhiram.autoapply.plist"
    assert "--background" in plist.read_text()
    assert (
        client.put("/api/desktop/autostart", json={"enabled": False}).json()["autostart"] is False
    )
    assert not plist.exists()

    assert client.post("/api/desktop/quit").status_code == 202
    assert desktop_api.quit_requested.is_set()
    desktop_api.quit_requested.clear()
