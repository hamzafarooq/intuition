"""`make browser`: open Brave (or Chrome) with remote debugging on port 9222 and its own profile.

The profile in ~/.intuition/browser-profile never touches your own cookies or logins. The window opens
on the right half of the screen so Intuition can sit on the left.

    uv run python tools/browser.py --real           # instructor demo: a clean, throwaway browser on port 9223
    uv run python tools/browser.py --real --close   # close it and delete its profile

`--real` is for the real-site search demo only (ea_harness/real_sites.py). It never reuses port 9222
or any existing profile: each launch gets a fresh temporary profile. `make demo-real` (ea-start
--real-sites) opens and closes it for you.
"""

import argparse
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CANDIDATES = [
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "brave-browser", "brave", "google-chrome", "chromium", "chromium-browser",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
]


def debug_port_open() -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=1)
        return True
    except Exception:
        return False


def find() -> str | None:
    for c in CANDIDATES:
        if Path(c).exists():
            return c
        found = shutil.which(c)
        if found:
            return found
    return None


def real(close: bool) -> int:
    from ea_harness import clean_browser

    if close:
        print("Closed the clean browser and deleted its profile." if clean_browser.close() else "No clean browser was open.")
        return 0
    try:
        info = clean_browser.launch()
    except clean_browser.CleanBrowserError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Clean browser ready on port {info['port']} ({info['browser']}, throwaway profile {info['profile']}).")
    print("Close it with: uv run python tools/browser.py --real --close")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Open the booking browser with remote debugging.")
    p.add_argument("--real", action="store_true", help="instructor demo: a clean, throwaway browser on port 9223")
    p.add_argument("--close", action="store_true", help="with --real: close it and delete its profile")
    args = p.parse_args(argv)
    if args.real:
        return real(args.close)
    if debug_port_open():
        print("A browser is already listening on port 9222.")
        return 0
    exe = find()
    if not exe:
        print("Install Brave (recommended) or Chrome to watch bookings happen in a browser.", file=sys.stderr)
        return 1
    profile = Path.home() / ".intuition" / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([exe, "--remote-debugging-port=9222", f"--user-data-dir={profile}", "--no-first-run",
                      "--no-default-browser-check", "--window-position=760,40", "--window-size=760,900",
                      "http://localhost:8766/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if debug_port_open():
            print(f"Browser ready on port 9222 ({Path(exe).name}).")
            return 0
        time.sleep(0.25)
    print("The browser started but port 9222 didn't answer.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
