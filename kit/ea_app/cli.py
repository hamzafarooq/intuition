"""`ea-app` serves the Intuition app; `ea-start` runs quick checks first, then serves it and opens it.

    ea-app [--port 8765] [--host 127.0.0.1] [--open] [--fake]
    ea-start [--port 8765] [--host 127.0.0.1] [--no-open] [--fake]
"""

import argparse
import logging
import os
import sys
import threading
import webbrowser

from . import checks


def _color(code: str, text: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def ok(text: str) -> None:
    print(_color("32", "  ✓ ") + text)


def bad(text: str, fix: str = "") -> None:
    print(_color("31", "  ✗ ") + text)
    if fix:
        print("      " + _color("2", "Fix: ") + fix)


def note(text: str) -> None:
    print(_color("33", "  • ") + text)


def _parser(prog: str, start: bool) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog, description="Intuition: the executive-assistant workshop app.")
    p.add_argument("--port", type=int, default=int(os.environ.get("EA_APP_PORT", "8765")))
    p.add_argument("--host", default="127.0.0.1")
    if start:
        p.add_argument("--no-open", action="store_true", help="don't open the browser")
    else:
        p.add_argument("--open", action="store_true", help="open the app in the browser")
    p.add_argument("--fake", action="store_true", help="use the scripted demo harness (no Claude needed)")
    p.add_argument("--log-level", default=os.environ.get("EA_LOG_LEVEL", "warning"))
    return p


def serve(host: str, port: int, open_browser: bool, fake: bool, log_level: str = "warning") -> None:
    import uvicorn

    from .server import create_app

    logging.basicConfig(level=log_level.upper(), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    app = create_app(fake=fake)
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}"
    print(_color("1", f"{app.state.app_name} is running at {url}") + ("  (scripted demo harness)" if fake else ""))
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    config = uvicorn.Config(app, host=host, port=port, log_level=log_level.lower(), timeout_graceful_shutdown=2)
    uvicorn.Server(config).run()


def main(argv: list[str] | None = None) -> None:
    args = _parser("ea-app", start=False).parse_args(argv)
    if not checks.port_free(args.host, args.port):
        bad(f"Port {args.port} is in use.", f"uv run ea-app --port {args.port + 35}")
        sys.exit(1)
    serve(args.host, args.port, args.open, args.fake, args.log_level)


def start(argv: list[str] | None = None) -> None:
    """Quick checks (Claude Code present and logged in, port free), then the app."""
    args = _parser("ea-start", start=True).parse_args(argv)
    print(_color("1", "Checking your setup"))
    c = checks.claude_status()
    ready = True
    if not c["installed"]:
        bad("Claude Code isn't installed (no `claude` command).",
            "install Claude Code (see docs/SETUP.md), open a new terminal, and run this again.")
        ready = False
    else:
        ok(f"Claude Code {c.get('version') or ''} at {c.get('path')}".strip())
        if c["logged_in"] is True:
            ok("Claude Code is logged in")
        elif c["logged_in"] is False:
            bad("Claude Code isn't logged in.", "run `claude` once in a terminal and log in.")
            ready = False
        else:
            note("Couldn't confirm the Claude Code login (`claude auth status` gave no answer).")
    if checks.port_free(args.host, args.port):
        ok(f"Port {args.port} is free")
    else:
        bad(f"Port {args.port} is in use.", f"uv run ea-start --port {args.port + 35}  (or stop whatever uses it)")
        sys.exit(1)
    if checks.live_search_available():
        ok("SERPAPI_API_KEY is set: live search is available")
    else:
        note("No SERPAPI_API_KEY: search uses recorded results (that's fine for the workshop)")
    b = checks.browser_status()
    (ok if b["available"] else note)(b["message"])
    if not ready and not args.fake:
        note("Starting anyway; the app explains what to fix. Use --fake for the scripted demo.")
    print()
    serve(args.host, args.port, not args.no_open, args.fake, args.log_level)


if __name__ == "__main__":  # pragma: no cover
    main()
