"""The stop check: writes need a later check; email needs check_email before send; at most two blocks a turn."""

import io
import json
import sys
from pathlib import Path

from ea_harness import stopcheck


def write_calls(run: Path, calls: list[dict]) -> None:
    run.mkdir(parents=True, exist_ok=True)
    with open(run / "calls.jsonl", "w") as f:
        for i, c in enumerate(calls, start=1):
            f.write(json.dumps({"seq": i, "ok": True, "side_effects": [], **c}) + "\n")


def create(eid="ev-n1"):
    return {"tool": "calendar_create", "object_id": eid, "side_effects": [{"kind": "event_created", "id": eid}]}


def check(tool, oid, ok=True):
    return {"tool": tool, "object_id": oid, "ok": ok}


def send(did="dr-1"):
    return {"tool": "email_send", "object_id": did, "args": {"draft_id": did}, "side_effects": [{"kind": "email_sent", "id": "msg-1"}]}


def test_unverified_write_blocks(tmp_path):
    write_calls(tmp_path, [create()])
    reason = stopcheck.evaluate(tmp_path, 0, "Booked.\nSTATUS: done")
    assert reason and "ev-n1" in reason and "check_event" in reason


def test_verified_write_passes(tmp_path):
    write_calls(tmp_path, [create(), check("check_event", "ev-n1")])
    assert stopcheck.evaluate(tmp_path, 0, "Booked.\nSTATUS: done") is None


def test_check_on_other_object_doesnt_count(tmp_path):
    write_calls(tmp_path, [create("ev-n1"), check("check_event", "ev-n2")])
    assert stopcheck.evaluate(tmp_path, 0, "STATUS: done")


def test_failed_check_call_doesnt_count(tmp_path):
    write_calls(tmp_path, [create(), check("check_event", "ev-n1", ok=False)])
    assert stopcheck.evaluate(tmp_path, 0, "STATUS: done")


def test_email_needs_check_before_send(tmp_path):
    write_calls(tmp_path, [send(), check("check_email", "dr-1")])
    assert "check_email" in stopcheck.evaluate(tmp_path, 0, "Sent.\nSTATUS: done")
    write_calls(tmp_path, [check("check_email", "dr-1"), send()])
    assert stopcheck.evaluate(tmp_path, 0, "Sent.\nSTATUS: done") is None


def test_writes_without_side_effects_ignored(tmp_path):
    write_calls(tmp_path, [{"tool": "calendar_create", "ok": False, "error": "Rate limited", "side_effects": []},
                           {"tool": "calendar_create", "idempotent_replay": True, "side_effects": []}])
    assert stopcheck.evaluate(tmp_path, 0, "STATUS: failed — rate limited") is None


def test_timeout_after_write_still_needs_check(tmp_path):
    write_calls(tmp_path, [{**create(), "ok": False, "fault": "timeout_after_write", "error": "Timed out"}])
    assert stopcheck.evaluate(tmp_path, 0, "STATUS: partial — timed out")


def test_only_this_turn(tmp_path):
    write_calls(tmp_path, [create("ev-n1"), {"tool": "clock_now"}])
    assert stopcheck.evaluate(tmp_path, since_seq=1, final_text="STATUS: done") is None


def test_status_line_rule(tmp_path):
    write_calls(tmp_path, [])
    assert stopcheck.evaluate(tmp_path, 0, "All set!") == "End your reply with a STATUS line."
    assert stopcheck.evaluate(tmp_path, 0, None) is None


def run_hook(tmp_path, monkeypatch, kind="stop", payload=None):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload or {"last_assistant_message": "Done.\nSTATUS: done"})))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    stopcheck.main(["--hook", kind, "--run-dir", str(tmp_path)])
    text = out.getvalue()
    return json.loads(text) if text else None


def test_hook_blocks_at_most_twice(tmp_path, monkeypatch):
    write_calls(tmp_path, [create()])
    (tmp_path / "turn_start").write_text("0")
    first = run_hook(tmp_path, monkeypatch)
    second = run_hook(tmp_path, monkeypatch)
    third = run_hook(tmp_path, monkeypatch)
    assert first["decision"] == "block" and second["decision"] == "block"
    assert third is None
    log = [json.loads(line) for line in (tmp_path / "stopcheck.jsonl").read_text().splitlines()]
    assert len(log) == 2 and log[0]["hook"] == "stop"


def test_new_turn_resets_counter(tmp_path, monkeypatch):
    write_calls(tmp_path, [create()])
    (tmp_path / "turn_start").write_text("0")
    run_hook(tmp_path, monkeypatch)
    run_hook(tmp_path, monkeypatch)
    write_calls(tmp_path, [create("ev-n1"), create("ev-n2")])
    (tmp_path / "turn_start").write_text("1")
    blocked = run_hook(tmp_path, monkeypatch)
    assert blocked and "ev-n2" in blocked["reason"]


def test_status_line_blocks_once(tmp_path, monkeypatch):
    write_calls(tmp_path, [])
    (tmp_path / "turn_start").write_text("0")
    p = {"last_assistant_message": "All set!"}
    assert run_hook(tmp_path, monkeypatch, payload=p)["reason"] == "End your reply with a STATUS line."
    assert run_hook(tmp_path, monkeypatch, payload=p) is None


def test_subagent_hook_ignores_status_line(tmp_path, monkeypatch):
    write_calls(tmp_path, [])
    assert run_hook(tmp_path, monkeypatch, kind="subagent", payload={"agent_type": "scheduler", "last_assistant_message": "Slots found."}) is None


def test_subagent_hook_blocks_unchecked_brief(tmp_path, monkeypatch):
    write_calls(tmp_path, [{"tool": "brief_save", "object_id": "br-1", "side_effects": [{"kind": "brief_saved", "id": "br-1"}]}])
    out = run_hook(tmp_path, monkeypatch, kind="subagent", payload={"agent_type": "briefer", "agent_id": "a1"})
    assert out and "check_brief" in out["reason"]


def test_hook_never_crashes(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    stopcheck.main(["--hook", "stop", "--run-dir", str(tmp_path / "missing")])
    assert out.getvalue() == ""
