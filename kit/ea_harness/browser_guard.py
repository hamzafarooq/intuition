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

Real-site search demo (instructor only, ``--real-sites``; the limits live in ``ea_harness/real_sites.py``):

    python -m ea_harness.browser_guard --real-sites [--run-dir DIR]          PreToolUse
    python -m ea_harness.browser_guard --real-sites --post [--run-dir DIR]   PostToolUse

The overlay adds both hooks, with the flag, only when the app runs the demo; without the flag nothing
below applies. In this mode the mock booking sites stay allowed as above, and the browser may also
open the real travel sites in ``real_sites.SITES`` to search and read prices. On real pages the guard
denies sign-in, account, cart, checkout, payment and booking addresses; fills of personal-data fields
or personal values; clicks on Book, Reserve, Sign in, Checkout ...; key presses other than navigation
keys; and any click, fill or key press while a tab sits on a page that isn't allowed (it reads the
clean browser's open tabs on ``real_sites.PORT``). A click or fill names an element only by its uid,
so the PostToolUse hook records every snapshot the model sees (``<run_dir>/browser_pages.json``,
reset each turn) and the PreToolUse hook looks the uid up there; an unknown uid is denied. In this
mode an internal error denies the call instead of allowing it.
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

from . import real_sites as RS

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


# ---------------------------------------------------------------- real-site search demo (instructor only)

PAGES_FILE = "browser_pages.json"
INTERACTIONS = frozenset({"click", "fill", "fill_form", "press_key", "hover"})
SNAPSHOT_LINE = re.compile(r"^(\s*)uid=(\S+)\s+(\S+)\s*(.*)$")
NAME_THEN_ATTRS = re.compile(r'^"(.*?)"(?=(?:\s+[a-z][\w-]*(?:="[^"]*")?)*\s*$)', re.S)
ATTR = re.compile(r'([a-z][\w-]*)(?:="([^"]*)")?')
NAVIGATED = re.compile(r"Page navigated to (\S+?)\.?$", re.M)

DENY_TOOL_REAL = "This browser tool isn't available in the real-site demo. Use " + ", ".join(RS.REAL_TOOLS) + "."
DENY_REAL_URL = ("Real-site demo: the browser can't open {url}: {why}. On real sites it only searches and reads "
                 "prices ({sites}); holds and bookings happen on the workshop's booking sites after Sarah approves.")
DENY_SNAPSHOT_FILE = "Real-site demo: take snapshots without a filePath, so the guard can check the elements you use."
DENY_UNKNOWN_UID = ("Real-site demo: the guard hasn't seen element {uid} in a snapshot this turn. Take a fresh "
                    "snapshot (take_snapshot) and use the uids it shows.")
DENY_COMMIT = ("Real-site demo: clicking \u201c{label}\u201d is blocked: it looks like booking, signing in or "
               "checking out. On real sites, only search and read prices; don't try another way.")
DENY_LINK = "Real-site demo: that link goes to {url}, which isn't allowed: {why}."
DENY_PERSONAL_FIELD = ("Real-site demo: the field \u201c{label}\u201d asks for personal details, which never go into "
                       "a real site. Type only search terms: places, dates and the number of travellers.")
DENY_PERSONAL_VALUE = ("Real-site demo: that value looks like personal details (a name, email, phone or card "
                       "number). Type only search terms: places, dates and the number of travellers.")
DENY_KEY = ("Real-site demo: press_key only sends navigation keys (" + ", ".join(sorted(RS.SAFE_KEYS)) + "). "
            "Type text with fill.")
DENY_OFF_SITE = ("Real-site demo: a tab is open on {url}, which isn't allowed ({why}). Go back "
                 "(navigate_page with type back) or close that tab before doing anything else there.")
DENY_NO_BROWSER = ("Real-site demo: the guard couldn't read the clean browser's open tabs on port {port}. "
                   "Ask the instructor to restart the demo (make demo-real).")
DENY_ERROR = "Real-site demo: the browser guard hit an internal error, so it blocked this step to be safe."
FLAG_OFF_SITE = ("Real-site demo: a tab is now on {url}, which isn't allowed ({why}). Don't use that page: go back "
                 "(navigate_page with type back) or close it. Clicks, fills and key presses are blocked until you do.")


def real_page_problem(url: Any) -> str | None:
    """None if a tab may sit on this address in real-site mode (mock site, allowed real page, blank tab)."""
    if not isinstance(url, str) or url_allowed(url) or RS.page_ignored(url):
        return None
    return RS.check_url(url)


def _short(url: Any, n: int = 120) -> str:
    s = str(url)
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def response_text(resp: Any) -> str:
    """The text of a tool result as a PostToolUse hook sees it (MCP content blocks, a string or a dict)."""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, list):
        return "\n".join(response_text(b) for b in resp)
    if isinstance(resp, dict):
        if isinstance(resp.get("text"), str):
            return resp["text"]
        for key in ("content", "result", "output"):
            if key in resp:
                return response_text(resp[key])
    return ""


def _sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            out.setdefault(current, [])
        elif current:
            out[current].append(line)
    return out


def parse_snapshot(lines: list[str]) -> tuple[str, dict[str, dict[str, Any]], str | None]:
    """(page url, {uid: node}, focused uid) from chrome-devtools-mcp snapshot lines such as
    ``uid=1_6 button "Book now" focusable focused`` (the root line carries the page ``url``)."""
    page, nodes, focused = "", {}, None
    for line in lines:
        m = SNAPSHOT_LINE.match(line[:4000])
        if not m:
            continue
        _indent, uid, role, rest = m.groups()
        name, attrs_text = "", rest
        if rest.startswith('"'):
            nm = NAME_THEN_ATTRS.match(rest)
            if nm:
                name, attrs_text = nm.group(1), rest[nm.end():]
            else:
                name, attrs_text = rest.strip('"'), ""
        attrs = {k: v for k, v in ATTR.findall(attrs_text)}
        if not page and role == "RootWebArea":
            page = attrs.get("url", "")
        node = {"role": role, "name": name[:500], "description": attrs.get("description", "")[:300]}
        if attrs.get("url"):
            node["url"] = attrs["url"][:2000]
        nodes[uid] = node
        if "focused" in attrs:
            focused = uid
    for node in nodes.values():
        node["page"] = page
    return page, nodes, focused


def parse_pages(lines: list[str]) -> dict[str, str]:
    """``2: Title (https://…) [selected]`` lines -> {page id: url}."""
    out: dict[str, str] = {}
    for line in lines:
        m = re.match(r"^(\d+): (.*)$", line.strip())
        if not m:
            continue
        label = re.sub(r"(?: isolatedContext=\S+)?$", "", m.group(2))
        label = label.removesuffix(" [selected]").strip()
        if label.endswith(")") and " (" in label:
            label = label[label.rfind(" (") + 2 : -1]
        out[m.group(1)] = label
    return out


def turn_mark(run_dir: Path | None) -> str:
    """Changes at every turn (the adapter rewrites ``turn_start``); uids don't survive a new browser server."""
    if run_dir is None:
        return ""
    try:
        return str((Path(run_dir) / "turn_start").stat().st_mtime_ns)
    except OSError:
        return ""


def load_pages(run_dir: Path | None) -> dict[str, Any]:
    empty: dict[str, Any] = {"turn": turn_mark(run_dir), "nodes": {}, "focus": None, "pages": {}}
    if run_dir is None:
        return empty
    try:
        data = json.loads((Path(run_dir) / PAGES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict) or data.get("turn") != empty["turn"]:
        return empty
    return {**empty, **data}


def open_pages(port: int = RS.PORT) -> list[str] | None:
    """URLs of the clean browser's open tabs (Chrome DevTools /json/list), or None if it doesn't answer."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1.5) as r:
            targets = json.loads(r.read().decode("utf-8"))
    except Exception:
        return None
    return [str(t.get("url", "")) for t in targets if isinstance(t, dict) and t.get("type") == "page"]


def _label(node: dict[str, Any]) -> str:
    return " ".join(x for x in (node.get("name", ""), node.get("description", "")) if x).strip()


def decide_real(payload: dict[str, Any], run_dir: Path | None = None) -> tuple[str, str | None]:
    """The real-site demo's decision: ("allow" | "deny", reason)."""
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    tool = short_name(tool_name)
    if tool_name.startswith("mcp__browser__") and tool not in RS.REAL_TOOLS:
        return "deny", DENY_TOOL_REAL
    if tool_input.get("initScript"):
        return "deny", DENY_SCRIPT
    if tool == "take_snapshot" and tool_input.get("filePath") not in (None, ""):
        return "deny", DENY_SNAPSHOT_FILE
    sites = ", ".join(s.name for s in RS.SITES)
    urls = list(find_urls(tool_input))
    if tool == "navigate_page":
        nav = tool_input.get("type") or ("url" if "url" in tool_input else None)
        if not (nav in HISTORY_TYPES and not any(u not in (None, "") for u in urls)) and not urls:
            return "deny", DENY_URL
    if tool == "new_page" and not urls:
        return "deny", DENY_URL
    for u in urls:
        if u in (None, "") and tool not in ("new_page", "navigate_page"):
            continue
        if url_allowed(u):
            continue
        why = RS.check_url(u)
        if why:
            return "deny", DENY_REAL_URL.format(url=_short(u), why=why, sites=sites)
    if tool in SAVE_EXTENSIONS and tool_input.get("filePath") not in (None, ""):
        if not save_path_allowed(tool, tool_input.get("filePath"), payload.get("cwd")):
            return "deny", DENY_SAVE
    if tool not in INTERACTIONS:
        return "allow", None

    pages = open_pages()
    if pages is None:
        return "deny", DENY_NO_BROWSER.format(port=RS.PORT)
    for p in pages:
        why = real_page_problem(p)
        if why:
            return "deny", DENY_OFF_SITE.format(url=_short(p), why=why)
    seen = load_pages(run_dir)
    nodes: dict[str, Any] = seen.get("nodes") or {}
    if tool == "press_key":
        key = str(tool_input.get("key") or "")
        if key not in RS.SAFE_KEYS:
            return "deny", DENY_KEY
        focus = nodes.get(str(seen.get("focus") or ""))
        if key in RS.SUBMIT_KEYS and focus and not url_allowed(focus.get("page", "")) and RS.commit_text(_label(focus)):
            return "deny", DENY_COMMIT.format(label=_short(_label(focus), 80))
        return "allow", None
    if tool == "fill_form":
        elements = tool_input.get("elements")
        targets = [(e.get("uid"), e.get("value")) for e in elements if isinstance(e, dict)] if isinstance(elements, list) else []
        if not targets:
            return "deny", DENY_UNKNOWN_UID.format(uid="(none)")
    else:
        targets = [(tool_input.get("uid"), tool_input.get("value"))]
    for uid, value in targets:
        node = nodes.get(str(uid))
        if not node:
            return "deny", DENY_UNKNOWN_UID.format(uid=_short(uid, 40))
        if url_allowed(node.get("page", "")):
            continue  # a mock booking site: ordinary browser mode applies
        label = _label(node)
        if tool == "click":
            if RS.commit_text(label):
                return "deny", DENY_COMMIT.format(label=_short(label, 80))
            link = node.get("url")
            if link and real_page_problem(link):
                return "deny", DENY_LINK.format(url=_short(link), why=real_page_problem(link))
        elif tool in ("fill", "fill_form"):
            if RS.personal_field(label):
                return "deny", DENY_PERSONAL_FIELD.format(label=_short(label, 80))
            if RS.personal_value(value):
                return "deny", DENY_PERSONAL_VALUE
    return "allow", None


def record_result(payload: dict[str, Any], run_dir: Path | None) -> str | None:
    """PostToolUse in real-site mode: remember the snapshot the model just saw; return a warning when a
    tab has landed somewhere that isn't allowed (by a click or a redirect)."""
    text = response_text(payload.get("tool_response"))
    secs = _sections(text)
    snap = secs.get("Latest page snapshot")
    listed = parse_pages(secs.get("Pages", []))
    if run_dir is not None and Path(run_dir).is_dir() and (snap or listed):
        from ea_world.state import file_lock, write_json_atomic

        with file_lock(Path(run_dir) / ".browser.lock"):
            data = load_pages(run_dir)
            if snap:
                _page, nodes, focused = parse_snapshot(snap)
                data["nodes"] = {**(data.get("nodes") or {}), **nodes}
                if len(data["nodes"]) > 20000:  # keep the file small on very long pages
                    data["nodes"] = dict(list(data["nodes"].items())[-20000:])
                data["focus"] = focused
            if listed:
                data["pages"] = listed
            write_json_atomic(Path(run_dir) / PAGES_FILE, data)
    for url in [*listed.values(), *NAVIGATED.findall(text)]:
        why = real_page_problem(url)
        if why:
            return FLAG_OFF_SITE.format(url=_short(url), why=why)
    return None


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
    real = "--real-sites" in (argv if argv is not None else sys.argv[1:])
    post = "--post" in (argv if argv is not None else sys.argv[1:])
    if post:
        return main_post(argv, stdin, stdout, real)
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
        if real:
            decision, reason = decide_real(payload, resolve_run_dir(run_dir_arg))
        else:
            decision, reason = decide(payload)
    except Exception as exc:  # never block the session because the guard itself failed
        error = f"{type(exc).__name__}: {exc}"
        if real:  # the real-site demo fails closed
            decision, reason = "deny", DENY_ERROR
            print(f"browser_guard: {error}; denying the call (real-site demo)", file=sys.stderr)
        else:
            decision, reason = "allow", None
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
    if real:
        record["mode"] = "real-sites"
    try:
        log_call(resolve_run_dir(run_dir_arg), record)
    except Exception as exc:
        print(f"browser_guard: couldn't write browser.jsonl: {exc}", file=sys.stderr)
    if decision == "deny" and reason:
        stdout.write(json.dumps(deny_output(reason)) + "\n")
        stdout.flush()
    return 0


def main_post(argv: list[str] | None, stdin: TextIO, stdout: TextIO, real: bool) -> int:
    """PostToolUse: record snapshots and flag tabs that left the allowed sites (real-site demo only)."""
    if not real:
        return 0
    try:
        parser = argparse.ArgumentParser(prog="browser_guard", add_help=False)
        parser.add_argument("--run-dir", default=None)
        args, _unknown = parser.parse_known_args(argv)
        raw = stdin.read()
        payload = json.loads(raw) if raw.strip() else None
        if not isinstance(payload, dict):
            raise ValueError("hook input is not a JSON object")
        run_dir = resolve_run_dir(args.run_dir)
        warning = record_result(payload, run_dir)
    except Exception as exc:
        print(f"browser_guard --post: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 0
    if warning:
        tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        try:
            log_call(run_dir, {"ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                               "session_id": payload.get("session_id"), "tool_use_id": payload.get("tool_use_id"),
                               "agent_id": payload.get("agent_id"), "tool": payload.get("tool_name"), "input": tool_input,
                               "url": None, "decision": "flag", "reason": warning, "mode": "real-sites", "hook": "post"})
        except Exception as exc:
            print(f"browser_guard --post: couldn't write browser.jsonl: {exc}", file=sys.stderr)
        stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": warning}}) + "\n")
        stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
