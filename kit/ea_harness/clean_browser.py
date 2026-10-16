"""The clean, throwaway browser for the real-site search demo (instructor only; see real_sites.py).

    uv run python tools/browser.py --real           # open it
    uv run python tools/browser.py --real --close   # close it and delete its profile

`ea-start --real-sites` (and `make demo-real`) opens it before the app starts and closes it when the
app stops. Each launch gets a brand-new profile in a temporary folder (no cookies, no saved logins,
no history), on its own debugging port (real_sites.PORT, 9223), so the demo never touches your own
browser profile or the ordinary booking browser on port 9222. Closing it deletes that folder.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from . import real_sites

PROFILE_PREFIX = "intuition-real-sites-"
CANDIDATES = [
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "brave-browser", "brave", "google-chrome", "chromium", "chromium-browser",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


class CleanBrowserError(RuntimeError):
    """The clean browser couldn't start (the message says what to do)."""


def state_file() -> Path:
    return Path.home() / ".intuition" / "real-site-browser.json"


def port_answers(port: int = real_sites.PORT, timeout: float = 0.6) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def find_browser() -> str | None:
    for c in CANDIDATES:
        if Path(c).exists():
            return c
        found = shutil.which(c)
        if found:
            return found
    return None


def _alive(pid: Any) -> bool:
    try:
        with contextlib.suppress(ChildProcessError, AttributeError):
            os.waitpid(int(pid), os.WNOHANG)  # reap it if it's our own child that has exited
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _ours(profile: Any, tmp_root: Any) -> bool:
    """Only folders this module made: our prefix, directly in the temp folder it was made in."""
    try:
        p = Path(str(profile)).resolve()
        root = Path(str(tmp_root or tempfile.gettempdir())).resolve()
    except (OSError, ValueError):
        return False
    return p.name.startswith(PROFILE_PREFIX) and p.parent == root


def _signal(pid: int, sig: int) -> None:
    killpg = getattr(os, "killpg", None)  # the browser runs in its own session: stop it and its helpers
    with contextlib.suppress(OSError):
        if killpg:
            killpg(pid, sig)
        else:
            os.kill(pid, sig)


def status() -> dict[str, Any] | None:
    """The running clean browser this kit started, or None."""
    try:
        info = json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(info, dict) or not _alive(info.get("pid")) or not port_answers(int(info.get("port", 0) or 0)):
        return None
    return info


def launch(exe: str | None = None, port: int = real_sites.PORT, url: str = "about:blank") -> dict[str, Any]:
    """Open a browser with a fresh throwaway profile on `port`; reuse the one we already started."""
    running = status()
    if running and int(running.get("port", 0)) == port:
        return running
    if port_answers(port):
        raise CleanBrowserError(
            f"Something else is already listening on port {port}. The real-site demo only uses its own clean "
            f"browser: close that program (or the browser using --remote-debugging-port={port}) and try again.")
    exe = exe or find_browser()
    if not exe:
        raise CleanBrowserError("Install Brave (recommended) or Chrome for the real-site demo.")
    close()  # tidy a stale profile from an earlier run, if any
    profile = Path(tempfile.mkdtemp(prefix=PROFILE_PREFIX))
    args = [exe, f"--remote-debugging-port={port}", f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--disable-sync", "--password-store=basic",
            "--window-position=760,40", "--window-size=760,900", url]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    info = {"pid": proc.pid, "port": port, "profile": str(profile), "tmp_root": str(profile.parent),
            "browser": Path(exe).name, "started": datetime.now().astimezone().isoformat(timespec="seconds")}
    state_file().parent.mkdir(parents=True, exist_ok=True)
    state_file().write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    for _ in range(60):
        if port_answers(port):
            return info
        time.sleep(0.25)
    close()
    raise CleanBrowserError(f"The browser started but port {port} didn't answer.")


def close() -> bool:
    """Stop the clean browser we started and delete its profile. True if there was one."""
    try:
        info = json.loads(state_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    pid = info.get("pid") if isinstance(info, dict) else None
    if _alive(pid):
        _signal(int(pid), signal.SIGTERM)
        for _ in range(40):
            if not _alive(pid):
                break
            time.sleep(0.125)
        if _alive(pid):
            _signal(int(pid), getattr(signal, "SIGKILL", signal.SIGTERM))
    profile = info.get("profile") if isinstance(info, dict) else None
    if profile and _ours(profile, info.get("tmp_root")):
        shutil.rmtree(profile, ignore_errors=True)
    state_file().unlink(missing_ok=True)
    return True
