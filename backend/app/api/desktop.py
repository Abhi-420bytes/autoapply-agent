"""Desktop-app controls (only when running as the desktop app): quit, start at login."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/desktop", tags=["desktop"])

LABEL = "com.abhiram.autoapply"
quit_requested = threading.Event()


def is_desktop() -> bool:
    return os.environ.get("AUTOAPPLY_DESKTOP") == "1"


def _require_desktop() -> None:
    if not is_desktop():
        raise HTTPException(404, "only available in the desktop app")


def app_executable() -> str:
    return sys.executable  # the frozen AutoApply binary (or python when run from source)


def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def autostart_enabled() -> bool:
    if sys.platform == "darwin":
        return _mac_plist().exists()
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
            ) as key:
                winreg.QueryValueEx(key, "AutoApply")
                return True
        except OSError:
            return False
    return False


def set_autostart(on: bool) -> None:
    exe = app_executable()
    if sys.platform == "darwin":
        plist = _mac_plist()
        if on:
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict>\n'
                f"  <key>Label</key><string>{LABEL}</string>\n"
                "  <key>ProgramArguments</key><array>"
                f"<string>{exe}</string><string>--background</string></array>\n"
                "  <key>RunAtLoad</key><true/>\n"
                "</dict></plist>\n"
            )
        else:
            plist.unlink(missing_ok=True)
    elif sys.platform == "win32":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if on:
                winreg.SetValueEx(key, "AutoApply", 0, winreg.REG_SZ, f'"{exe}" --background')
            else:
                try:
                    winreg.DeleteValue(key, "AutoApply")
                except OSError:
                    pass
    else:
        raise HTTPException(409, "start at login isn't supported on this system yet")


class DesktopInfo(BaseModel):
    desktop: bool
    autostart: bool
    platform: str


class AutostartIn(BaseModel):
    enabled: bool


@router.get("", response_model=DesktopInfo)
def info() -> DesktopInfo:
    desktop = is_desktop()
    return DesktopInfo(
        desktop=desktop, autostart=desktop and autostart_enabled(), platform=sys.platform
    )


@router.put("/autostart", response_model=DesktopInfo)
def put_autostart(body: AutostartIn) -> DesktopInfo:
    _require_desktop()
    set_autostart(body.enabled)
    return info()


@router.post("/quit", status_code=202)
def quit_app() -> dict[str, str]:
    """Stop the whole app (agent, API, dashboard). Opening AutoApply starts it again."""
    _require_desktop()
    quit_requested.set()
    return {"status": "quitting"}
