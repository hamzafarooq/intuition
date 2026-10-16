"""`ea-app` serves the mock phone (Intuition's local web page); `ea-start` runs quick checks first, then serves
it and opens it.

    ea-app [--port 8765] [--host 127.0.0.1] [--open] [--fake] [--real-sites]
    ea-start [--port 8765] [--host 127.0.0.1] [--no-open] [--fake] [--real-sites]

`--real-sites` (or `EA_REAL_SITES=on`; `make demo-real`) is the instructor-only real-site search demo
(ea_harness/real_sites.py): it opens a clean, throwaway browser on port 9223, lets browser sessions
search real travel sites for quotes, and closes the browser and deletes its profile when the mock phone stops.
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
    p = argparse.ArgumentParser(prog=prog, description="The mock phone: text Intuition, the workshop's executive assistant, from your browser.")
    p.add_argument("--port", type=int, default=int(os.environ.get("EA_APP_PORT", "8765")))
    p.add_argument("--host", default="127.0.0.1")
    if start:
        p.add_argument("--no-open", action="store_true", help="don't open the browser")
    else:
        p.add_argument("--open", action="store_true", help="open the mock phone in the browser")
    p.add_argument("--fake", action="store_true", help="use the scripted demo harness (no Claude needed)")
    p.add_argument("--real-sites", action="store_true",
                   help="instructor demo: let browser sessions search real travel sites for quotes, in a clean "
                        "throwaway browser (also EA_REAL_SITES=on)")
    p.add_argument("--log-level", default=os.environ.get("EA_LOG_LEVEL", "warning"))
    return p


def load_env() -> None:
    """Read kit/.env into the environment (SERPAPI_API_KEY, OPENAI_API_KEY, ...). Values are never printed."""
    try:
        from ea_evals.env import load_dotenv
    except ImportError:
        return
    path = load_dotenv()
    if path:
        print(f"Settings: read {path.name}")


def real_sites_on(args: argparse.Namespace) -> bool:
    from ea_harness import real_sites

    return bool(args.real_sites) or real_sites.requested()


def open_clean_browser() -> bool:
    """Start the real-site demo's clean, throwaway browser. False (with the reason printed) if it can't."""
    from ea_harness import clean_browser, real_sites

    try:
        info = clean_browser.launch()
    except clean_browser.CleanBrowserError as exc:
        bad(f"{real_sites.LABEL}: {exc}")
        return False
    ok(f"{real_sites.LABEL}: clean throwaway {info.get('browser', 'browser')} on port {info['port']} "
       "(fresh profile, deleted when the mock phone stops)")
    note("Real sites give quotes only; holds and bookings stay on the workshop's booking sites. Evals never use this.")
    return True


def serve(host: str, port: int, open_browser: bool, fake: bool, log_level: str = "warning",
          real_sites: bool = False) -> None:
    import uvicorn

    from .server import create_app

    logging.basicConfig(level=log_level.upper(), format="%(asctime)s %(name)s %(levelname)s %(message)s")
    app = create_app(fake=fake, sites_port=int(os.environ.get("EA_SITES_PORT", "8766")), real_sites=real_sites)
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}"
    print(_color("1", f"The mock phone is running at {url}; text {app.state.app_name} from it") + ("  (scripted demo harness)" if fake else "")
          + ("  (real-site search: instructor demo)" if real_sites else ""))
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    config = uvicorn.Config(app, host=host, port=port, log_level=log_level.lower(), timeout_graceful_shutdown=2)
    try:
        uvicorn.Server(config).run()
    finally:
        if real_sites:
            from ea_harness import clean_browser

            if clean_browser.close():
                print("Closed the real-site demo browser and deleted its profile.")


def main(argv: list[str] | None = None) -> None:
    args = _parser("ea-app", start=False).parse_args(argv)
    load_env()
    if not checks.port_free(args.host, args.port):
        bad(f"Port {args.port} is in use.", f"uv run ea-app --port {args.port + 35}")
        sys.exit(1)
    real = real_sites_on(args) and open_clean_browser()
    serve(args.host, args.port, args.open, args.fake, args.log_level, real_sites=real)


def start(argv: list[str] | None = None) -> None:
    """Quick checks (Claude Code present and logged in, port free), then the mock phone."""
    args = _parser("ea-start", start=True).parse_args(argv)
    print(_color("1", "Checking your setup"))
    load_env()
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
    real = real_sites_on(args) and open_clean_browser()
    if not ready and not args.fake:
        note("Starting anyway; the mock phone explains what to fix. Use --fake for the scripted demo.")
    print()
    serve(args.host, args.port, not args.no_open, args.fake, args.log_level, real_sites=real)


if __name__ == "__main__":  # pragma: no cover
    main()
