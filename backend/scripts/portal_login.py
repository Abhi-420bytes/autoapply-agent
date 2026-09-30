"""Sign in to a portal yourself, once, and hand the session to the agent.

For portals that show a CAPTCHA ("I'm not a robot") before login. The agent never solves
CAPTCHAs; instead you solve it and log in in a normal browser window, and the agent reuses
that signed-in session (stored encrypted, only the portal's own cookies) until it expires.

    cd "job agent "            # the project folder
    backend/.venv/bin/python backend/scripts/portal_login.py 2   # 2 = portal id

Needs the stack running (the API on http://127.0.0.1:8000). Your password is typed into the
portal's own page and never passes through this script.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from playwright.sync_api import sync_playwright

API = "http://127.0.0.1:8000"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def api_token() -> str:
    """The same API_TOKEN the dashboard uses (from the environment or the project's .env)."""
    if os.environ.get("API_TOKEN"):
        return os.environ["API_TOKEN"]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "API_TOKEN":
                return value.strip().strip("'\"")
    return ""


def portal_host(portal: dict[str, Any]) -> str:
    """Registrable part of the portal host, e.g. "haveloc.com"."""
    host = urlparse(portal["base_url"]).hostname or ""
    return ".".join(host.split(".")[-2:])


SESSION_JS = (
    "() => ({origin: location.origin, items: Object.fromEntries(Object.entries(sessionStorage))})"
)


def snapshot(context: Any, page: Any) -> dict[str, Any]:
    """Cookies + localStorage + IndexedDB, plus the tab's sessionStorage (many portals keep
    their login token there, and a browser's saved state doesn't include it)."""
    state = dict(context.storage_state(indexed_db=True))
    got = page.evaluate(SESSION_JS)
    if got.get("items"):
        state["session_storage"] = {got["origin"]: got["items"]}
    return state


def wait_for_user(context: Any, page: Any) -> dict[str, Any] | None:
    """Until the user closes the window (or presses Enter in a terminal), keep the latest
    signed-in state; closing the window can't lose it."""
    state: dict[str, Any] | None = None
    stop = threading.Event()
    if sys.stdin.isatty():

        def enter() -> None:
            try:
                input("Press Enter when you're logged in... ")
            except (EOFError, KeyboardInterrupt):
                return
            stop.set()

        threading.Thread(target=enter, daemon=True).start()
    try:
        while not stop.is_set() and not page.is_closed():
            state = snapshot(context, page)
            page.wait_for_timeout(1000)
    except Exception:  # noqa: BLE001, S110 — the window was closed mid-poll: keep last state
        pass
    return state


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("portal_id", type=int, help="portal id (Settings → Portals)")
    ap.add_argument("--api", default=API)
    args = ap.parse_args()

    token = api_token()
    if not token:
        print(f"API_TOKEN not found (looked in the environment and {ENV_FILE}).")
        return 1
    headers = {"Authorization": f"Bearer {token}", "X-AutoApply": "1"}
    with httpx.Client(base_url=args.api, headers=headers, timeout=30) as api:
        try:
            portals = api.get("/api/portals").raise_for_status().json()
        except httpx.HTTPError as exc:
            print(f"Can't reach the AutoApply API at {args.api} ({exc}). Is the stack running?")
            return 1
        portal = next((p for p in portals if p["id"] == args.portal_id), None)
        if portal is None:
            ids = ", ".join(f"{p['id']} = {p['name']}" for p in portals) or "none"
            print(f"No portal {args.portal_id}. Portals: {ids}")
            return 1

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=False)
            context = browser.new_context()
            page = context.new_page()
            page.goto(portal["base_url"])
            print(
                f"\nA browser window opened on {portal['name']}.\n"
                "  1. Solve the CAPTCHA and log in as you normally would (OTP too, if asked).\n"
                "  2. Wait until you can see the jobs list.\n"
                "  3. Close the browser window (or press Enter here) to save the session.\n"
            )
            state = wait_for_user(context, page)
            browser.close()
        if state is None:
            print("Cancelled; nothing was saved.")
            return 1

        own = [c for c in state.get("cookies", []) if portal_host(portal) in c.get("domain", "")]
        stores = [
            o["origin"]
            for o in state.get("origins", [])
            if o.get("localStorage") or o.get("indexedDB")
        ]
        stores += [f"{o} (session)" for o in state.get("session_storage", {})]
        print(
            f"Found {len(own)} {portal['name']} cookie(s), "
            f"{len(state.get('cookies', []))} cookie(s) in total, "
            f"site storage for: {', '.join(stores) or 'none'}"
        )
        r = api.put(f"/api/portals/{args.portal_id}/session", json=state)
        if r.status_code >= 400:
            print(f"Not saved: {r.json().get('detail', r.text)}")
            return 1
        print(
            f"Saved. The agent will reuse your {portal['name']} session "
            "(click 'Search now' on the Job websites page to try it)."
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
