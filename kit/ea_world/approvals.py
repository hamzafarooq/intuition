"""The approval policy and the `approval_prompt` tool Claude Code calls through --permission-prompt-tool.

One policy, three places: the house rules (the model's behaviour), `.claude/settings.json` (the
harness gate), and this module (the answer the gate gets). A gated call is authorized when:

  (a) it's calendar_create with only internal attendees and Maya asked for that meeting;
  (b) Maya's latest message is an explicit yes, given after the assistant showed the action;
  (c) the tool is pre-authorized by Maya's request ("just send it, no need to check").
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .core import canonical_json, idempotency_key
from .rules import resolve_person
from .state import State, append_jsonl, read_json, write_json_atomic

log = logging.getLogger("ea_world.approvals")

PREFIX = "mcp__ea-world__"
GATED = {
    "calendar_create", "calendar_update", "calendar_cancel", "email_send",
    "travel_book", "travel_cancel", "restaurant_book",
}  # fmt: skip
HARMLESS_BUILTINS = {"ToolSearch", "TodoWrite", "Skill", "Agent", "Task"}
BROWSER_ALLOWED = {
    "new_page", "navigate_page", "select_page", "list_pages", "take_snapshot", "click", "fill",
    "fill_form", "press_key", "hover", "wait_for", "take_screenshot", "close_page",
}  # fmt: skip
EXPLICIT_YES = re.compile(r"^\s*(yes|yep|go ahead|send it|book it|do it|approved?)\b", re.IGNORECASE)
POLL_SECONDS = 0.5
APP_TIMEOUT_SECONDS = 600


def bare(tool_name: str) -> str:
    return tool_name[len(PREFIX) :] if tool_name.startswith(PREFIX) else tool_name


def is_explicit_yes(text: str | None) -> bool:
    return bool(text and EXPLICIT_YES.match(text))


def summarize(tool: str, args: dict[str, Any], run_dir: Path) -> str:
    """One human-readable line for the approval card and the trace."""
    st = State(run_dir)
    try:
        if tool == "calendar_create":
            who = ", ".join(args.get("attendees", []))
            return f"Create \"{args.get('title', '')}\" {args.get('start', '')} – {args.get('end', '')} with {who}"
        if tool == "calendar_update":
            parts = [f"{k}={v}" for k, v in args.items() if k not in ("event_id", "idempotency_key")]
            return f"Change {args.get('event_id')}: " + ", ".join(parts)
        if tool == "calendar_cancel":
            return f"Cancel event {args.get('event_id')}"
        if tool == "email_send":
            for d in st.load("drafts").get("drafts", []):
                if d["id"] == args.get("draft_id"):
                    body = re.sub(r"\s+", " ", d.get("body", ""))[:140]
                    return f"Send to {', '.join(d.get('to', []))}: \"{d.get('subject', '')}\" — {body}"
            return f"Send draft {args.get('draft_id')}"
        if tool in ("travel_book", "restaurant_book"):
            ref = args.get("hold_id") or args.get("option_id")
            extra = " ".join(f"{k}={v}" for k, v in args.items() if k in ("check_in", "check_out", "date", "time", "party_size"))
            return f"Book {ref} {extra}".strip()
        if tool == "travel_cancel":
            return f"Cancel booking {args.get('booking_id')}"
    except Exception:  # summaries must never break approvals
        log.exception("summary failed")
    return f"{tool} {canonical_json(args)[:160]}"


def context(run_dir: Path) -> dict[str, Any]:
    return read_json(Path(run_dir) / "approval_context.json", {}) or {}


def decide_script(tool: str, args: dict[str, Any], ctx: dict[str, Any], run_dir: Path) -> tuple[bool, str]:
    """The scripted approval policy used by the eval runner."""
    if ctx.get("gate") == "off":
        return True, "gate=off: every call is allowed"
    if tool in (ctx.get("pre_authorized") or []):
        return True, "pre-authorized by Maya's request"
    if ctx.get("explicit_yes"):
        return True, "Maya's latest message is an explicit yes"
    if tool == "calendar_create" and ctx.get("fill_mode") == "create":
        contacts = State(run_dir).load("contacts")
        people = [resolve_person(a, contacts) for a in args.get("attendees", []) if a != "maya"]
        if people and all(p is not None and p.get("internal") for p in people):
            return True, "internal meeting Maya asked for"
    return False, "Maya hasn't approved this."


def _allow(args: dict[str, Any]) -> str:
    return json.dumps({"behavior": "allow", "updatedInput": args})


def _deny(message: str) -> str:
    return json.dumps({"behavior": "deny", "message": message})


def handle(tool_name: str, tool_input: dict[str, Any], tool_use_id: str | None, run_dir: Path) -> str:
    """Answer one permission request. Returns the JSON text Claude Code expects."""
    run_dir = Path(run_dir)
    tool = bare(tool_name)
    args = dict(tool_input or {})
    mode = os.environ.get("EA_APPROVAL_MODE", "script").strip() or "script"
    call_id = tool_use_id or f"ap-{int(time.time() * 1000)}"
    summary = summarize(tool, args, run_dir)
    log_path = run_dir / "approvals.jsonl"
    request = {"kind": "request", "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
               "call_id": call_id, "tool": tool, "full_name": tool_name, "input": args, "summary": summary, "mode": mode}
    append_jsonl(log_path, request)

    def respond(decision: str, by: str, reason: str) -> str:
        append_jsonl(log_path, {"kind": "response", "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                                "call_id": call_id, "tool": tool, "decision": decision, "by": by, "reason": reason})
        if decision == "allow":
            out = dict(args)
            if os.environ.get("EA_IDEMPOTENCY", "off") == "auto" and tool in GATED and not out.get("idempotency_key"):
                out["idempotency_key"] = idempotency_key(tool, args)
            return _allow(out)
        return _deny(reason)

    # Calls that aren't ea-world tools: harmless plumbing is allowed, the rest denied.
    if not tool_name.startswith(PREFIX):
        if tool_name in HARMLESS_BUILTINS:
            return respond("allow", "script", "harness plumbing")
        if tool_name.startswith("mcp__browser__") and tool_name[len("mcp__browser__") :] in BROWSER_ALLOWED:
            return respond("allow", "script", "allowed browser tool")
        return respond("deny", "script", "Maya's assistant works only through the ea-world tools.")

    if mode == "allow_all":
        return respond("allow", "script", "allow_all")
    if mode == "record_and_deny":
        return respond("deny", "script", "Recorded for the tool-use suite; not executed.")
    if tool not in GATED:
        return respond("allow", "script", "not a gated tool")
    ctx = context(run_dir)
    if mode == "script":
        ok, reason = decide_script(tool, args, ctx, run_dir)
        return respond("allow" if ok else "deny", "script", reason)
    if mode == "app":
        if ctx.get("auto_approve") and ctx.get("explicit_yes"):
            return respond("allow", "app", "Approved by your 'yes'")
        pending = run_dir / "approvals" / "pending" / f"{call_id}.json"
        decided = run_dir / "approvals" / "decided" / f"{call_id}.json"
        write_json_atomic(pending, request)
        deadline = time.monotonic() + APP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if decided.exists():
                d = read_json(decided, {}) or {}
                pending.unlink(missing_ok=True)
                if d.get("decision") == "allow":
                    return respond("allow", d.get("by", "human"), d.get("reason", "Approved in the app"))
                return respond("deny", d.get("by", "human"), d.get("reason", "Maya denied this in the app."))
            time.sleep(POLL_SECONDS)
        pending.unlink(missing_ok=True)
        return respond("deny", "app", "No answer within 10 minutes.")
    return respond("deny", "script", f"Unknown approval mode '{mode}'")
