# ruff: noqa: S603, S607  (the desktop launcher starts its own bundled processes by design)
"""AutoApply desktop app: the whole system in one program, no Docker.

    python -m app.desktop            (from source)
    AutoApply.app / AutoApply.exe    (packaged; see packaging/)

One process runs the API, the background agent (scheduler thread) and the dashboard (the
bundled Node.js runs the Next.js server). Data lives in your user folder:
  macOS:   ~/Library/Application Support/AutoApply
  Windows: %APPDATA%\\AutoApply
  Linux:   ~/.local/share/autoapply
The encryption key and API token are generated on first launch (never shipped).
Opening the app again while it runs just shows the dashboard.

Created by Abhiram (Challa Abhiram). MIT license.
"""

from __future__ import annotations

import logging
import os
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

APP = "AutoApply"
API_PORT = 18765
WEB_PORT = 18766
DASHBOARD_URL = f"http://localhost:{WEB_PORT}"

log = logging.getLogger("autoapply.desktop")


# -- locations ------------------------------------------------------------------------------


def base_dir() -> Path:
    """Where the app's code and bundled files are (PyInstaller unpacks to sys._MEIPASS)."""
    frozen = getattr(sys, "_MEIPASS", None)
    return Path(frozen) if frozen else Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    override = os.environ.get("AUTOAPPLY_DATA_DIR")
    if override:
        d = Path(override)
    elif sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / APP
    elif sys.platform == "win32":
        d = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP
    else:
        d = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "autoapply"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _bundled(*parts: str) -> Path | None:
    p = base_dir().joinpath(*parts)
    return p if p.exists() else None


# -- first-run keys and environment ---------------------------------------------------------


def load_or_create_keys(dd: Path) -> dict[str, str]:
    """MASTER_KEY encrypts your stored credentials; generated once, kept only on this PC."""
    from cryptography.fernet import Fernet

    path = dd / "keys.env"
    keys: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip():
                keys[k.strip()] = v.strip()
    changed = False
    if not keys.get("MASTER_KEY"):
        keys["MASTER_KEY"] = Fernet.generate_key().decode()
        changed = True
    if not keys.get("API_TOKEN"):
        keys["API_TOKEN"] = secrets.token_urlsafe(32)
        changed = True
    if changed:
        path.write_text("".join(f"{k}={v}\n" for k, v in keys.items()))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return keys


def configure(dd: Path) -> dict[str, str]:
    keys = load_or_create_keys(dd)
    (dd / "files").mkdir(exist_ok=True)
    (dd / "logs").mkdir(exist_ok=True)
    env = {
        "DATABASE_URL": f"sqlite:///{(dd / 'autoapply.db').as_posix()}",
        "DATA_DIR": str(dd / "files"),
        "MASTER_KEY": keys["MASTER_KEY"],
        "API_TOKEN": keys["API_TOKEN"],
        "DASHBOARD_URL": DASHBOARD_URL,
        "CORS_ORIGINS": f'["{DASHBOARD_URL}", "http://127.0.0.1:{WEB_PORT}"]',
        "PLAYWRIGHT_BROWSERS_PATH": str(dd / "browsers"),
        "AUTOAPPLY_DESKTOP": "1",
    }
    exe = "tectonic.exe" if sys.platform == "win32" else "tectonic"
    if tectonic := _bundled("bin", exe):
        env["TECTONIC_BIN"] = str(tectonic)
    for k, v in env.items():
        os.environ.setdefault(k, v)
    return env


def setup_logging(dd: Path) -> None:
    from logging.handlers import RotatingFileHandler

    handler = RotatingFileHandler(dd / "logs" / "autoapply.log", maxBytes=2_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr and sys.stderr.isatty():
        root.addHandler(logging.StreamHandler())


def migrate() -> None:
    from alembic.config import Config

    from alembic import command

    base = base_dir()
    cfg = Config(str(base / "alembic.ini"))
    cfg.set_main_option("script_location", str(base / "alembic"))
    cfg.config_file_name = None  # don't let alembic replace the app's logging setup
    command.upgrade(cfg, "head")


# -- services -------------------------------------------------------------------------------


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_http(url: str, timeout_s: float = 90) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3):  # noqa: S310 — localhost only
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


def start_api() -> threading.Thread:
    import uvicorn

    from app.main import app

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=API_PORT, log_config=None, access_log=False)
    )
    t = threading.Thread(target=server.run, name="api", daemon=True)
    t.start()
    return t


def node_binary() -> str | None:
    exe = "node.exe" if sys.platform == "win32" else "node"
    bundled = _bundled("node", exe) or _bundled("node", "bin", exe)
    return str(bundled) if bundled else shutil.which("node")


def dashboard_dir() -> Path | None:
    return _bundled("dashboard") or _bundled("..", "frontend", ".next", "standalone")


def _is_our_dashboard(pid: int) -> bool:
    """Only ever stop a process that really is this app's dashboard (never a reused PID)."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(  # noqa: S603, S607
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.lower()
            return "node" in out
        out = subprocess.run(  # noqa: S603, S607
            ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=10
        ).stdout
        # Next.js renames its process to "next-server (vX)"
        return "next-server" in out or ("server.js" in out and "node" in out)
    except (OSError, subprocess.SubprocessError):
        return False


def stop_stale_dashboard(dd: Path) -> None:
    """A dashboard left running by a crashed launch would hold the port: stop it."""
    pid_file = dd / "dashboard.pid"
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return
    if _is_our_dashboard(pid):
        log.info("stopping a dashboard left over from a previous run (pid %s)", pid)
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)  # noqa: S603, S607
            else:
                os.kill(pid, 15)
            time.sleep(1.5)
        except OSError:
            pass
    pid_file.unlink(missing_ok=True)


def start_dashboard(env: dict[str, str], dd: Path) -> subprocess.Popen[bytes] | None:
    node, root = node_binary(), dashboard_dir()
    if not node or not root or not (root / "server.js").exists():
        log.error("dashboard files or Node.js missing (node=%s, dir=%s)", node, root)
        return None
    child_env = {
        **os.environ,
        "PORT": str(WEB_PORT),
        "HOSTNAME": "127.0.0.1",
        "NODE_ENV": "production",
        "NEXT_TELEMETRY_DISABLED": "1",
        "API_URL": f"http://127.0.0.1:{API_PORT}",
        "API_TOKEN": env["API_TOKEN"],
        "DASHBOARD_URL": DASHBOARD_URL,
        "ALLOWED_HOSTS": f"localhost:{WEB_PORT},127.0.0.1:{WEB_PORT}",
    }
    out = open(dd / "logs" / "dashboard.log", "ab")  # noqa: SIM115 — lives with the child
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(  # noqa: S603 — our bundled node + server.js
        [node, "server.js"], cwd=root, env=child_env, stdout=out, stderr=out, creationflags=flags
    )
    (dd / "dashboard.pid").write_text(str(proc.pid))
    return proc


def ensure_browser(dd: Path) -> None:
    """The portal agent's Chromium: downloaded once, on first launch, into the data folder."""
    if any((dd / "browsers").glob("chromium*")):
        return
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env

        driver = compute_driver_executable()
        cmd = (
            [*driver, "install", "chromium"]
            if isinstance(driver, tuple)
            else [str(driver), "install", "chromium"]
        )
        env = {**get_driver_env(), "PLAYWRIGHT_BROWSERS_PATH": str(dd / "browsers")}
        log.info("downloading the browser for the portal agent (first launch only)")
        subprocess.run(cmd, env=env, check=True, capture_output=True, timeout=1800)  # noqa: S603
        log.info("browser ready")
    except Exception:  # noqa: BLE001 — the rest of the app works without it
        log.exception("couldn't download the browser; portal applying will retry later")


def open_window(url: str) -> None:
    """The dashboard in its own app window (Chrome/Edge --app), else the default browser."""
    candidates: list[list[str]] = []
    if sys.platform == "darwin":
        for app in ("Google Chrome", "Microsoft Edge", "Brave Browser"):
            if Path(f"/Applications/{app}.app").exists():
                candidates.append(["open", "-na", app, "--args", f"--app={url}"])
    elif sys.platform == "win32":
        for exe in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ):
            if Path(exe).exists():
                candidates.append([exe, f"--app={url}"])
    for cmd in candidates:
        try:
            subprocess.Popen(cmd)  # noqa: S603
            return
        except OSError:
            continue
    webbrowser.open(url)


# -- main -------------------------------------------------------------------------------------


def main() -> int:
    url = f"{DASHBOARD_URL}/jobs"
    if port_open(WEB_PORT) and port_open(API_PORT):  # already running: just show it
        open_window(url)
        return 0
    dd = data_dir()
    env = configure(dd)
    setup_logging(dd)
    stop_stale_dashboard(dd)
    log.info("AutoApply starting (data: %s, %s)", dd, platform.platform())
    migrate()
    start_api()
    if not wait_http(f"http://127.0.0.1:{API_PORT}/health", 60):
        log.error("the API didn't start; see the log")
        return 1
    from app.worker import start_background

    scheduler: Any = start_background()
    dashboard = start_dashboard(env, dd)
    threading.Thread(target=ensure_browser, args=(dd,), name="browser", daemon=True).start()
    if dashboard is not None and wait_http(url, 90) and not os.environ.get("AUTOAPPLY_NO_WINDOW"):
        open_window(url)
    log.info("AutoApply is running")

    def _stop(signum: int, _frame: Any) -> None:  # quit from the OS / logout / shutdown
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    restarts: list[float] = []
    try:
        while True:
            time.sleep(5)
            if dashboard is not None and dashboard.poll() is not None:
                now = time.monotonic()
                restarts = [r for r in restarts if now - r < 60] + [now]
                if len(restarts) > 3:  # failing repeatedly: back off instead of spinning
                    log.error("dashboard keeps stopping; see logs/dashboard.log (retrying in 60 s)")
                    time.sleep(60)
                    restarts = []
                log.warning("dashboard stopped; restarting it")
                stop_stale_dashboard(dd)
                dashboard = start_dashboard(env, dd)
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.shutdown(wait=False)
        if dashboard is not None:
            dashboard.terminate()
        (dd / "dashboard.pid").unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
