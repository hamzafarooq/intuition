"""PreToolUse hook for the booking browser: the browser only visits the workshop's booking sites.

    python -m ea_harness.browser_guard [--run-dir DIR]

Claude Code runs this before every ``mcp__browser__*`` call (matcher in ``assistant/.claude/settings.json``)
with the hook JSON on stdin (``tool_name``, ``tool_input``, ``session_id``, ``cwd``, ...).

- ``new_page`` and ``navigate_page`` (and any call whose input carries a ``url``) may only go to
  ``http(s)://{skyway,stays,tables}.localhost:8766/...`` or the path fallback
  ``http://localhost:8766/{skyway,stays,tables}...`` (also ``127.0.0.1:8766``). ``navigate_page`` with
  ``type`` back, forward or reload and no url is allowed.
- Browser tools outside the allowed list in spec/16-browser-bookings.md are denied, as is
  ``navigate_page``'s ``initScript`` (it runs JavaScript, like the denied ``evaluate_script``), and
  saving a screenshot or snapshot outside an ``outputs/browser/`` folder.
- A denial prints ``{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
  "permissionDecisionReason": ...}}`` and exits 0. An allowed call prints nothing, so Claude Code's normal
  permission rules still apply (the hook never grants anything).
- Every call, allowed or denied, is logged to ``<run_dir>/browser.jsonl`` (run dir from ``--run-dir``, else
  ``EA_RUN_DIR``, else ``runs/CURRENT``).
- The hook never crashes: on an internal error it allows the call and reports the error on stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import unquote, urlsplit

PORT = 8766
SITE_HOSTS = frozenset({"skyway.localhost", "stays.localhost", "tables.localhost"})
PATH_HOSTS = frozenset({"localhost", "127.0.0.1"})
SITE_PATH = re.compile(r"^/(skyway|stays|tables)(?:[/?#]|$)")

# spec/16-browser-bookings.md, "Allowed browser tools"
ALLOWED_TOOLS = frozenset({
    "new_page", "navigate_page", "select_page", "list_pages", "take_snapshot", "click", "fill", "fill_form",
    "press_key", "hover", "wait_for", "take_screenshot", "close_page",
})
HISTORY_TYPES = frozenset({"back", "forward", "reload"})
SAVE_EXTENSIONS = {"take_screenshot": {".png", ".jpg", ".jpeg", ".webp"},
                   "take_snapshot": {".txt", ".md", ".json"}}

DENY_URL = "The booking browser only visits the workshop's booking sites."
DENY_TOOL = ("This browser tool isn't available to the assistant. Use new_page, navigate_page, take_snapshot, "
             "click, fill, fill_form, press_key, hover, wait_for, take_screenshot, select_page, list_pages or close_page.")
DENY_SCRIPT = "The booking browser doesn't run scripts (initScript isn't allowed)."
DENY_SAVE = "Browser screenshots and snapshots can only be saved as image or text files under an outputs/browser/ folder."


# ---------------------------------------------------------------- checks


def url_allowed(url: Any) -> bool:
    """True only for the booking sites on port 8766 (host routing, or the localhost path fallback)."""
    if not isinstance(url, str):
        return False
    u = url.strip()
    if not u or len(u) > 4096 or "\\" in u:
        return False
    if any(ch.isspace() or not (0x21 <= ord(ch) <= 0x7E) for ch in u):
        return False  # whitespace, control or non-ASCII characters are parsed differently by browsers
    try:
        parts = urlsplit(u)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme.lower() not in ("http", "https"):
        return False
    if "@" in parts.netloc:
        return False  # no user-info tricks such as http://skyway.localhost:8766@evil.example
    if port != PORT:
        return False
    if any(seg in (".", "..") for seg in unquote(parts.path).split("/")):
        return False
    host = (parts.hostname or "").lower()
    if host in SITE_HOSTS:
        return True
    if host in PATH_HOSTS:
        return bool(SITE_PATH.match(parts.path or ""))
    return False


def save_path_allowed(tool: str, file_path: Any, cwd: str | None) -> bool:
    if not isinstance(file_path, str) or not file_path.strip():
        return False
    p = Path(file_path.strip()).expanduser()
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    p = Path(os.path.normpath(p))
    dirs = [d.lower() for d in p.parent.parts]
    under_outputs = any(a == "outputs" and b == "browser" for a, b in zip(dirs, dirs[1:], strict=False))
    return under_outputs and p.suffix.lower() in SAVE_EXTENSIONS.get(tool, set())


def find_urls(value: Any, depth: int = 0) -> Iterator[Any]:
    """Every value stored under a `url` key, at any depth of the tool input."""
    if depth > 6:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str) and k.lower() == "url":
                yield v
            else:
                yield from find_urls(v, depth + 1)
    elif isinstance(value, list):
        for v in value:
            yield from find_urls(v, depth + 1)


def short_name(tool_name: str) -> str:
    """mcp__browser__navigate_page -> navigate_page."""
    if tool_name.startswith("mcp__"):
        return tool_name.split("__", 2)[-1]
    return tool_name


def decide(payload: dict[str, Any]) -> tuple[str, str | None]:
    """Return ("allow" | "deny", reason)."""
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    tool = short_name(tool_name)
    if tool_name.startswith("mcp__browser__") and tool not in ALLOWED_TOOLS:
        return "deny", DENY_TOOL
    if tool_input.get("initScript"):
        return "deny", DENY_SCRIPT
    urls = list(find_urls(tool_input))
    if tool == "navigate_page":
        nav = tool_input.get("type") or ("url" if "url" in tool_input else None)
        if nav in HISTORY_TYPES and not any(u not in (None, "") for u in urls):
            return "allow", None
        if not urls:
            return "deny", DENY_URL
    if tool == "new_page" and not urls:
        return "deny", DENY_URL
    for u in urls:
        if u in (None, "") and tool not in ("new_page", "navigate_page"):
            continue
        if not url_allowed(u):
            return "deny", DENY_URL
    if tool in SAVE_EXTENSIONS and tool_input.get("filePath") not in (None, ""):
        if not save_path_allowed(tool, tool_input.get("filePath"), payload.get("cwd")):
            return "deny", DENY_SAVE
    return "allow", None


# ---------------------------------------------------------------- logging


def resolve_run_dir(arg: str | None) -> Path | None:
    if arg:
        return Path(arg)
    env = os.environ.get("EA_RUN_DIR")
    if env:
        return Path(env)
    from ea_world.state import read_current

    return read_current()


def log_call(run_dir: Path | None, record: dict[str, Any]) -> None:
    if run_dir is None or not Path(run_dir).is_dir():
        return
    from ea_world.state import append_jsonl, file_lock

    with file_lock(Path(run_dir) / ".browser.lock"):
        append_jsonl(Path(run_dir) / "browser.jsonl", record)


def deny_output(reason: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


# ---------------------------------------------------------------- entry point


def main(argv: list[str] | None = None, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    run_dir_arg = None
    payload: dict[str, Any] = {}
    decision, reason, error = "allow", None, None
    try:
        parser = argparse.ArgumentParser(prog="browser_guard", add_help=False)
        parser.add_argument("--run-dir", default=None)
        args, _unknown = parser.parse_known_args(argv)
        run_dir_arg = args.run_dir
        raw = stdin.read()
        loaded = json.loads(raw) if raw.strip() else None
        if not isinstance(loaded, dict):
            raise ValueError("hook input is not a JSON object")
        payload = loaded
        decision, reason = decide(payload)
    except Exception as exc:  # never block the session because the guard itself failed
        decision, reason, error = "allow", None, f"{type(exc).__name__}: {exc}"
        print(f"browser_guard: {error}; allowing the call", file=sys.stderr)
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    record: dict[str, Any] = {
        "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "session_id": payload.get("session_id"),
        "tool_use_id": payload.get("tool_use_id"),
        "agent_id": payload.get("agent_id"),
        "tool": payload.get("tool_name"),
        "input": tool_input,
        "url": next((u for u in find_urls(tool_input) if isinstance(u, str)), None),
        "decision": decision,
        "reason": reason,
    }
    if error:
        record["error"] = error
    try:
        log_call(resolve_run_dir(run_dir_arg), record)
    except Exception as exc:
        print(f"browser_guard: couldn't write browser.jsonl: {exc}", file=sys.stderr)
    if decision == "deny" and reason:
        stdout.write(json.dumps(deny_output(reason)) + "\n")
        stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
