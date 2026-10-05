"""Quick environment checks shared by `ea-start` and `GET /api/config`.

Never reads `.env`: live search is available only when `SERPAPI_API_KEY` is already in the
environment (the Makefile or the lead's loader puts it there). The key itself is never returned.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

CLAUDE_FALLBACKS = [
    Path.home() / ".local" / "bin" / "claude",
    Path.home() / ".claude" / "local" / "claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
]

BROWSER_APPS = {
    "darwin": [
        ("Brave", Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")),
        ("Chrome", Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")),
    ],
}
BROWSER_COMMANDS = [("Brave", "brave-browser"), ("Brave", "brave"), ("Chrome", "google-chrome"),
                    ("Chrome", "google-chrome-stable"), ("Chrome", "chromium"), ("Chrome", "chromium-browser")]


def find_claude() -> str | None:
    """`claude` on the PATH, or in the places the installer usually puts it."""
    found = shutil.which("claude")
    if found:
        return found
    for p in CLAUDE_FALLBACKS:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    return None


def claude_status(timeout: float = 20.0) -> dict[str, Any]:
    """`{installed, logged_in, version, path, message}`. `logged_in` is None when it couldn't be told."""
    path = find_claude()
    out: dict[str, Any] = {"installed": bool(path), "logged_in": None, "version": None, "path": path, "message": ""}
    if not path:
        out["logged_in"] = False
        out["message"] = ("Claude Code isn't installed (no `claude` command found). Install it from "
                          "code.claude.com, open a new terminal, and start Intuition again.")
        return out
    try:
        v = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=timeout)
        out["version"] = (v.stdout or v.stderr).strip().splitlines()[0] if (v.stdout or v.stderr).strip() else None
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        a = subprocess.run([path, "auth", "status"], capture_output=True, text=True, timeout=timeout)
        data = json.loads(a.stdout or "{}")
        out["logged_in"] = bool(data.get("loggedIn"))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
        out["logged_in"] = None
    if out["logged_in"] is False:
        out["message"] = "Claude Code isn't logged in. Run `claude` once in a terminal, log in, then reload this page."
    elif out["logged_in"] is None:
        out["message"] = "Couldn't confirm the Claude Code login (`claude auth status` gave no answer)."
    return out


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Something already accepts connections there (another copy of the app or the sites)."""
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return not port_free(host, port)


def debug_port_answers(port: int = 9222, timeout: float = 0.6) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def installed_browsers() -> list[str]:
    names: list[str] = []
    for name, p in BROWSER_APPS.get(sys.platform, []):
        if p.exists() and name not in names:
            names.append(name)
    for name, cmd in BROWSER_COMMANDS:
        if shutil.which(cmd) and name not in names:
            names.append(name)
    return names


def browser_status() -> dict[str, Any]:
    port_open = debug_port_answers()
    names = installed_browsers()
    available = port_open or bool(names)
    if port_open:
        msg = "A browser is listening on port 9222; bookings will happen there."
    elif names:
        msg = f"{names[0]} will open so you can watch bookings happen."
    else:
        msg = "Install Brave or Chrome to see bookings happen in a browser."
    return {"available": available, "debug_port_open": port_open, "installed": names, "default": available, "message": msg}


def live_search_available() -> bool:
    return bool(os.environ.get("SERPAPI_API_KEY", "").strip())


def voice_enabled() -> bool:
    return os.environ.get("VOICE", "").strip().lower() in ("1", "true", "yes", "on")
