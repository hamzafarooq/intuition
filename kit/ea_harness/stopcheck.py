"""The stop check: the harness won't accept "done" until the checks have run.

As a Claude Code Stop/SubagentStop hook (`python -m ea_harness.stopcheck --hook stop`), it reads the
hook JSON on stdin, looks at the tool server's call log for this turn, and blocks the stop when a
write has no later verifier call on the same object, or the reply has no STATUS line. It blocks at
most twice a turn. Graders use `evaluate()` too (`checked_before_done`).
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .trace import parse_status

WRITE_TO_CHECK = {
    "calendar_create": "check_event", "calendar_update": "check_event",
    "email_send": "check_email",          # check_email must run on the draft BEFORE send
    "travel_book": "check_booking", "restaurant_book": "check_booking",
    "brief_save": "check_brief", "deck_create": "check_deck",
}  # fmt: skip
MAX_BLOCKS_PER_TURN = 2


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
    except FileNotFoundError:
        pass
    return out


def _write_id(call: dict[str, Any]) -> str | None:
    """The object a write created or changed (the id its check must name)."""
    if call["tool"] == "email_send":
        return call.get("object_id") or call.get("args", {}).get("draft_id")
    if call.get("object_id"):
        return call["object_id"]
    for eff in call.get("side_effects") or []:
        return eff.get("id")
    return None


def unverified_writes(calls: list[dict[str, Any]], since_seq: int = 0) -> list[dict[str, Any]]:
    """Writes after `since_seq` with no matching successful check (email: before the send)."""
    missing = []
    for i, c in enumerate(calls):
        if c.get("seq", 0) <= since_seq or c.get("tool") not in WRITE_TO_CHECK:
            continue
        if not c.get("side_effects"):  # failed before writing, replayed, or changed nothing
            continue
        check = WRITE_TO_CHECK[c["tool"]]
        obj = _write_id(c)
        if c["tool"] == "email_send":
            window = calls[:i]
        else:
            window = calls[i + 1 :]
        ok = any(w.get("tool") == check and w.get("ok") and w.get("object_id") == obj for w in window)
        if not ok:
            missing.append({"tool": c["tool"], "object_id": obj, "check": check, "seq": c.get("seq")})
    return missing


def evaluate(run_dir: Path, since_seq: int = 0, final_text: str | None = None) -> str | None:
    """A reason to send the assistant back, or None when it may finish."""
    calls = _read_jsonl(Path(run_dir) / "calls.jsonl")
    missing = unverified_writes(calls, since_seq)
    if missing:
        m = missing[0]
        if m["tool"] == "email_send":
            return (
                f"You sent draft {m['object_id']} with email_send without running check_email on it first. "
                "Run check_email now, read the result, tell Maya about any problem, then reply."
            )
        return (
            f"You changed {m['object_id']} with {m['tool']} but haven't run {m['check']} on it. "
            "Run it, read the result, then reply."
        )
    if final_text is not None and parse_status(final_text)[0] == "missing":
        return "End your reply with a STATUS line."
    return None


def resolve_run_dir(arg: str | None) -> Path | None:
    if arg:
        return Path(arg)
    if os.environ.get("EA_RUN_DIR"):
        return Path(os.environ["EA_RUN_DIR"])
    try:
        from ea_world.state import read_current

        return read_current()
    except Exception:
        return None


def _turn_start(run_dir: Path) -> int:
    try:
        return int((run_dir / "turn_start").read_text().strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0


def _bump_counter(run_dir: Path, key: str) -> int:
    p = run_dir / "stopcheck_count.json"
    try:
        counts = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        counts = {}
    counts[key] = counts.get(key, 0) + 1
    p.write_text(json.dumps(counts), encoding="utf-8")
    return counts[key]


def _count(run_dir: Path, key: str) -> int:
    try:
        return json.loads((run_dir / "stopcheck_count.json").read_text(encoding="utf-8")).get(key, 0)
    except (FileNotFoundError, ValueError):
        return 0


def hook(kind: str, payload: dict[str, Any], run_dir: Path) -> dict[str, Any] | None:
    """The hook's decision: a block dict, or None to let the agent stop."""
    since = _turn_start(run_dir)
    key = f"{kind}:{since}:{payload.get('agent_id') or 'main'}"
    if _count(run_dir, key) >= MAX_BLOCKS_PER_TURN:
        return None
    final_text = payload.get("last_assistant_message") if kind == "stop" else None
    if final_text is None and kind == "stop" and payload.get("transcript_path"):
        final_text = _last_text_from_transcript(Path(payload["transcript_path"]))
    reason = evaluate(run_dir, since, final_text)
    if kind == "subagent" and reason == "End your reply with a STATUS line.":
        reason = None
    if not reason:
        return None
    if reason == "End your reply with a STATUS line." and any(
        r.get("reason") == reason and r.get("turn_start") == since for r in _read_jsonl(run_dir / "stopcheck.jsonl")
    ):
        return None  # the status-line rule blocks once a turn
    _bump_counter(run_dir, key)
    with open(run_dir / "stopcheck.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().astimezone().isoformat(timespec="milliseconds"), "hook": kind,
                            "agent": payload.get("agent_type") or "main", "turn_start": since, "reason": reason}) + "\n")
    return {"decision": "block", "reason": reason}


def _last_text_from_transcript(path: Path) -> str | None:
    last = None
    for rec in _read_jsonl(path):
        msg = rec.get("message") or {}
        if rec.get("type") == "assistant" and isinstance(msg.get("content"), list):
            texts = [b.get("text", "") for b in msg["content"] if b.get("type") == "text"]
            if texts:
                last = "\n".join(texts)
    return last


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m ea_harness.stopcheck")
    parser.add_argument("--hook", choices=["stop", "subagent"], default="stop")
    parser.add_argument("--run-dir", default=None)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    try:
        run_dir = resolve_run_dir(args.run_dir)
        if run_dir is None or not run_dir.exists():
            return
        decision = hook(args.hook, payload, run_dir)
        if decision:
            sys.stdout.write(json.dumps(decision))
    except Exception as exc:  # a broken hook must never wedge the assistant
        print(f"stopcheck error: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
