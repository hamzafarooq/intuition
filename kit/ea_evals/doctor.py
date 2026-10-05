"""`make doctor`: check every prerequisite and say how to fix anything red (spec/15-setup.md).

    uv run python -m ea_evals.doctor            # everything (two tiny Claude Code calls use a little of your plan)
    uv run python -m ea_evals.doctor --quick    # what `make start` needs: Claude Code, keys, port
    uv run python -m ea_evals.doctor --with-search   # also one SerpAPI search (1 of your 250 a month)
"""

import argparse
import asyncio
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ea_world import paths

from .env import load_dotenv

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
MIN_CLAUDE = (2, 1, 0)


class Result:
    def __init__(self, ok: bool | None, detail: str, fix: str = ""):
        self.ok, self.detail, self.fix = ok, detail, fix


def _color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def show(name: str, r: Result) -> None:
    c = _color()
    mark = "✓" if r.ok else ("!" if r.ok is None else "✗")
    col = GREEN if r.ok else (YELLOW if r.ok is None else RED)
    print(f"{col if c else ''}{mark}{RESET if c else ''} {name}: {r.detail}")
    if r.fix and not r.ok:
        print(f"    {DIM if c else ''}fix: {r.fix}{RESET if c else ''}")


def claude_path() -> str | None:
    from ea_harness.claude_code import claude_binary

    return claude_binary()


# ---------------------------------------------------------------- checks


def check_python() -> Result:
    v = sys.version_info
    return Result(v >= (3, 11), f"Python {v.major}.{v.minor}.{v.micro}", "uv python install 3.12, then make setup")


def check_deps() -> Result:
    missing = []
    for mod in ("mcp", "starlette", "uvicorn", "openai", "httpx", "yaml", "jinja2", "markdown", "bs4", "tzdata"):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    return Result(not missing, "all installed" if not missing else f"missing: {', '.join(missing)}", "make setup (or uv sync)")


def check_claude() -> Result:
    p = claude_path()
    if not p:
        return Result(False, "claude isn't on the PATH",
                      "install Claude Code (curl -fsSL https://claude.ai/install.sh | bash, or brew install --cask claude-code), then open a new terminal")
    try:
        out = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as exc:
        return Result(False, f"claude --version failed: {exc}", "reinstall Claude Code")
    ver = tuple(int(x) for x in (out.split()[0] if out else "0.0.0").split(".")[:3] if x.isdigit())
    ok = ver >= MIN_CLAUDE
    return Result(ok, f"{out} ({p})", f"update Claude Code to {'.'.join(map(str, MIN_CLAUDE))} or later (claude update)")


def check_login() -> Result:
    p = claude_path()
    if not p:
        return Result(False, "claude not installed", "see above")
    try:
        out = subprocess.run([p, "auth", "status"], capture_output=True, text=True, timeout=30).stdout
        data = json.loads(out[out.index("{"):]) if "{" in out else {}
    except Exception as exc:
        return Result(False, f"couldn't read login status: {exc}", "run claude once and log in")
    if data.get("loggedIn"):
        return Result(True, f"logged in ({data.get('authMethod', 'claude.ai')})")
    return Result(False, "not logged in", "claude auth login (or run claude once and log in)")


def check_headless_ok() -> Result:
    p = claude_path()
    with tempfile.TemporaryDirectory() as cwd:
        try:
            r = subprocess.run([p or "claude", "-p", "Reply with OK", "--max-turns", "1", "--output-format", "json", "--tools", "",
                                "--setting-sources", "project,local"], cwd=cwd, capture_output=True, text=True, timeout=180,
                               stdin=subprocess.DEVNULL, env={k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"})
            data = json.loads(r.stdout)
        except Exception as exc:
            return Result(False, f"headless call failed: {exc}", "run claude once interactively and log in")
    text = str(data.get("result", ""))
    if data.get("is_error"):
        return Result(False, text[:160], "check your plan's usage limit, or log in again")
    return Result("OK" in text.upper(), f"claude -p answered ({data.get('total_cost_usd', 0):.3f} USD API-equivalent, plan usage)")


def check_tool_server() -> Result:
    async def go() -> int:
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "EA_RUN_DIR": str(Path(d) / "run"), "EA_WRITE_CURRENT": "0"}
            params = StdioServerParameters(command=sys.executable, args=["-m", "ea_world"], env=env)
            async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
                await s.initialize()
                return len((await s.list_tools()).tools)

    try:
        n = asyncio.run(asyncio.wait_for(go(), 60))
    except Exception as exc:
        return Result(False, f"ea-world didn't start: {exc}", "make setup; then uv run ea-world tools")
    return Result(n >= 30, f"ea-world serves {n} tools over stdio", "uv run ea-world tools to see what's missing")


async def _assistant_probe(message: str, approval_context: dict, run_dir: Path):
    from ea_harness.base import RunConfig
    from ea_harness.claude_code import ClaudeCodeHarness
    from ea_harness.overlay import build_overlay
    from ea_world import state

    state.init_run_dir(run_dir)
    (run_dir / "approval_context.json").write_text(json.dumps(approval_context))
    overlay = build_overlay(run_dir, {"hooks": "off"})
    h = ClaudeCodeHarness()
    await h.start(run_dir, RunConfig(assistant_dir=overlay, max_turns=12, approval_mode="script",
                                     env={"EA_RUN_DIR": str(run_dir), "EA_WRITE_CURRENT": "0", "EA_APPROVAL_MODE": "script"}))
    try:
        return await h.send(message)
    finally:
        await h.close()


def check_assistant_sees_tools() -> Result:
    with tempfile.TemporaryDirectory() as d:
        try:
            r = asyncio.run(_assistant_probe("Call clock_now and tell me today's date as YYYY-MM-DD.", {}, Path(d) / "run"))
        except Exception as exc:
            return Result(False, f"probe failed: {exc}", "check that uv is on the PATH for the shell Claude Code starts")
    ok = "2026-10-26" in (r.final_text or "")
    return Result(ok, "the assistant called clock_now and saw Monday 26 October 2026" if ok else f"unexpected reply: {(r.final_text or r.error)[:140]}",
                  "make doctor again; if ea-world isn't connected, see docs/SETUP.md troubleshooting")


def check_approval_roundtrip() -> Result:
    from ea_world.state import read_jsonl

    with tempfile.TemporaryDirectory() as d:
        run = Path(d) / "run"
        msg = ("Do both of these now without asking me first, then report: (1) create a 15-minute event 'Doctor check' with "
               "Kevin Osei on Tue 27 Oct 2026 at 13:15 Denver; (2) cancel my event ev-focus-fri.")
        try:
            asyncio.run(_assistant_probe(msg, {"fill_mode": "create", "pre_authorized": [], "explicit_yes": False}, run))
        except Exception as exc:
            return Result(False, f"probe failed: {exc}", "see docs/SETUP.md troubleshooting")
        resp = [r for r in read_jsonl(run / "approvals.jsonl") if r.get("kind") == "response"]
    allowed = [r["tool"] for r in resp if r["decision"] == "allow"]
    denied = [r["tool"] for r in resp if r["decision"] == "deny"]
    ok = "calendar_create" in allowed and "calendar_cancel" in denied
    return Result(ok if resp else False, f"gate allowed {allowed or 'nothing'}, denied {denied or 'nothing'}",
                  "the approval_prompt reply shape may have changed in this Claude Code version: see spec/13-decisions-log.md")


def check_openai() -> Result:
    if not os.environ.get("OPENAI_API_KEY"):
        return Result(False, "OPENAI_API_KEY isn't set", "add OPENAI_API_KEY=... to .env (provided in the workshop)")
    from .judge import DEFAULT_MODEL

    model = os.environ.get("EA_JUDGE_MODEL") or DEFAULT_MODEL
    try:
        from openai import OpenAI

        r = OpenAI().responses.create(model=model, input="Reply with OK.", max_output_tokens=16, store=False, reasoning={"effort": "low"})
        return Result(True, f"OpenAI key works ({model})" + ("" if "OK" in (r.output_text or "").upper() else ", odd reply"))
    except Exception as exc:
        return Result(False, f"OpenAI call failed: {str(exc)[:160]}", "check OPENAI_API_KEY in .env, or set EA_JUDGE_MODEL to a model your key can use")


def check_serpapi(with_search: bool) -> Result:
    key = os.environ.get("SERPAPI_API_KEY")
    if not key:
        return Result(None, "SERPAPI_API_KEY not set: live search in the app is off (evals always use recorded results)", "optional: add a free key from serpapi.com to .env")
    if not with_search:
        return Result(True, "SERPAPI_API_KEY set (not tested; --with-search uses 1 search)")
    import httpx

    try:
        data = httpx.get("https://serpapi.com/search", params={"engine": "google", "q": "construction supply news", "api_key": key}, timeout=20).json()
    except Exception as exc:
        return Result(False, f"SerpAPI failed: {exc}", "check your network and key")
    if data.get("error") and "hasn't returned any results" not in data["error"]:
        return Result(False, f"SerpAPI error: {data['error'][:120]}", "check SERPAPI_API_KEY; the free plan allows 250 searches a month")
    return Result(True, "SerpAPI search works")


def check_tz() -> Result:
    d = datetime(2026, 10, 28, 12, 0)
    lon = d.replace(tzinfo=ZoneInfo("Europe/London")).utcoffset().total_seconds() / 3600
    den = d.replace(tzinfo=ZoneInfo("America/Denver")).utcoffset().total_seconds() / 3600
    ok = lon == 0 and den == -6
    return Result(ok, f"28 Oct 2026: London UTC{lon:+.0f}, Denver UTC{den:+.0f}", "make setup installs tzdata; rerun it")


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def check_port(port: int = 8765) -> Result:
    if port_free(port):
        return Result(True, f"port {port} is free")
    return Result(False, f"port {port} is in use", "stop whatever uses it, or run uv run ea-app --port 8800")


def find_browser() -> str | None:
    cands = [
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("brave-browser") or "", shutil.which("brave") or "", shutil.which("google-chrome") or "", shutil.which("chromium") or "",
    ]
    return next((c for c in cands if c and Path(c).exists()), None)


def check_browser() -> Result:
    node = shutil.which("node")
    br = find_browser()
    import urllib.request

    try:
        urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=1)
        debug = True
    except Exception:
        debug = False
    from ea_harness import browser_guard

    try:
        decision, _ = browser_guard.decide({"tool_name": "mcp__browser__navigate_page", "tool_input": {"url": "https://example.com"}})
        guard_ok = decision == "deny"
    except Exception:
        guard_ok = False
    parts = [f"node {'found' if node else 'missing'}", f"browser {'found' if br else 'missing'}", f"port 9222 {'answers' if debug else 'not open'}"]
    if not node or not br:
        return Result(None, "; ".join(parts) + ": bookings will happen directly through the tools", "optional: install Brave or Chrome and Node.js 20+")
    if not guard_ok:
        return Result(False, "; ".join(parts) + "; the browser guard didn't deny an outside URL", "check ea_harness/browser_guard.py")
    return Result(True if debug else None, "; ".join(parts) + "; the guard denies outside sites", "make browser (opens Brave with remote debugging)")


def check_personal_settings() -> Result:
    home = Path.home() / ".claude"
    found = []
    if (home / "CLAUDE.md").exists():
        found.append("~/.claude/CLAUDE.md")
    try:
        if json.loads((home / "settings.json").read_text()).get("hooks"):
            found.append("user-level hooks in ~/.claude/settings.json")
    except Exception:
        pass
    try:
        user_mcp = sorted((json.loads((Path.home() / ".claude.json").read_text()).get("mcpServers") or {}).keys())
    except Exception:
        user_mcp = []
    if user_mcp:
        found.append(f"personal MCP servers ({', '.join(user_mcp)})")
    if not found:
        return Result(True, "no personal Claude Code instructions, hooks or MCP servers")
    return Result(None, f"{', '.join(found)} present. Eval runs skip them (--setting-sources project,local), but `cd assistant && claude` loads them",
                  "move them aside while you work on the assistant interactively, if they change its behaviour")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ea-doctor")
    ap.add_argument("--quick", action="store_true", help="only what make start needs")
    ap.add_argument("--with-search", action="store_true")
    args = ap.parse_args(argv)
    load_dotenv()
    os.environ["PATH"] = os.pathsep.join([str(Path.home() / ".local" / "bin"), os.environ.get("PATH", "")])
    checks: list[tuple[str, Callable[[], Result]]] = [
        ("Python", check_python), ("Dependencies", check_deps), ("Claude Code", check_claude), ("Claude login", check_login),
        ("OpenAI key", (lambda: Result(bool(os.environ.get("OPENAI_API_KEY")), "set" if os.environ.get("OPENAI_API_KEY") else "OPENAI_API_KEY isn't set",
                                       "add OPENAI_API_KEY=... to .env")) if args.quick else check_openai),
        ("Port 8765", check_port),
    ]
    if not args.quick:
        checks += [
            ("Time zones", check_tz), ("Tool server", check_tool_server), ("SerpAPI", lambda: check_serpapi(args.with_search)),
            ("Browser mode", check_browser), ("Personal settings", check_personal_settings),
            ("Claude headless", check_headless_ok), ("Claude sees the tools", check_assistant_sees_tools),
            ("Approval round-trip", check_approval_roundtrip),
        ]
        print(f"{DIM if _color() else ''}The last three checks make short Claude Code calls and use a little of your plan.{RESET if _color() else ''}")
    failed = 0
    for name, fn in checks:
        try:
            r = fn()
        except Exception as exc:
            r = Result(False, f"check crashed: {exc}", "report this to the instructor")
        show(name, r)
        failed += r.ok is False
    print(("All good." if not failed else f"{failed} check{'s need' if failed != 1 else ' needs'} attention.") + f" (kit: {paths.kit_root()})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
