"""The Claude Code adapter on recorded stream-json (captured from real `claude -p` runs, 2026-10-05)."""

import asyncio
import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from ea_harness.base import RunConfig
from ea_harness.claude_code import ClaudeCodeHarness, StreamMapper, _Tail
from ea_harness.trace import TraceWriter, parse_status, read_trace

FIX = Path(__file__).parent / "fixtures" / "stream"


def load(name: str, file: str = "stream.jsonl") -> list[dict]:
    return [json.loads(line) for line in (FIX / name / file).read_text().splitlines() if line.strip()]


def replay(tmp_path: Path, name: str) -> tuple[StreamMapper, list[dict], ClaudeCodeHarness]:
    run = tmp_path / "run"
    run.mkdir()
    for f in ("calls.jsonl", "approvals.jsonl", "stopcheck.jsonl"):
        if (FIX / name / f).exists():
            shutil.copy(FIX / name / f, run / f)
    trace = TraceWriter(run / "trace.jsonl", {"case_id": "S01", "trial": 1})
    trace.turn = 1
    mapper = StreamMapper(trace, run)
    for ev in load(name):
        mapper.feed(ev)
    h = ClaudeCodeHarness()
    h.run_dir, h.trace = run, trace
    h.tails = {n: _Tail(run / f"{n}.jsonl") for n in ("approvals", "stopcheck", "site", "browser")}
    h._merge_logs()
    h._finish(mapper, "end_turn", "", 1.0)
    return mapper, read_trace(run / "trace.jsonl"), h


def test_event_mapping_skill_delegate_and_tools(tmp_path):
    mapper, events, _ = replay(tmp_path, "s01-live")
    types = [e["type"] for e in events]
    assert "skill_loaded" in types
    assert any(e["type"] == "skill_loaded" and e["skill"] == "scheduling" for e in events)
    delegates = [e for e in events if e["type"] == "delegate"]
    assert delegates and delegates[0]["to"] == "scheduler" and delegates[0]["task"]
    tools = {e["tool"] for e in events if e["type"] == "tool_call"}
    assert {"clock_now", "contacts_lookup", "calendar_find_free", "calendar_create", "check_event"} <= tools
    assert not any(t.startswith("mcp__") for t in tools), "prefix must be stripped"
    assert "ToolSearch" not in tools, "ToolSearch is harness plumbing"


def test_subagent_attribution(tmp_path):
    _, events, _ = replay(tmp_path, "s01-live")
    delegate = next(e for e in events if e["type"] == "delegate")
    sub = [e for e in events if e["agent"] == "scheduler"]
    assert sub, "scheduler events must be attributed"
    assert all(e["parent_call_id"] == delegate["call_id"] for e in sub)
    assert any(e["type"] == "tool_call" and e["tool"] == "calendar_find_free" for e in sub)
    handoff = [e for e in events if e["type"] == "handoff_result"]
    assert handoff and handoff[0]["to"] == "scheduler" and not handoff[0]["text"].startswith("[Subagent")


def test_every_tool_call_has_one_result(tmp_path):
    _, events, _ = replay(tmp_path, "s01-live")
    calls = [e["call_id"] for e in events if e["type"] == "tool_call"]
    results = [e["call_id"] for e in events if e["type"] == "tool_result" and e["tool"] != "Agent"]
    assert sorted(calls) == sorted(results)
    assert len(set(calls)) == len(calls)


def test_checks_signals_and_session(tmp_path):
    mapper, events, _ = replay(tmp_path, "s01-live")
    checks = [e for e in events if e["type"] == "check"]
    assert checks and checks[0]["verifier"] == "check_event" and checks[0]["ok"] is True
    assert checks[0]["object_id"].startswith("ev-")
    assert mapper.session_id and len(mapper.session_id) > 10
    assert mapper.init and mapper.init["subtype"] == "init"


def test_approvals_merged(tmp_path):
    _, events, _ = replay(tmp_path, "s01-live")
    req = [e for e in events if e["type"] == "approval_request"]
    resp = [e for e in events if e["type"] == "approval_response"]
    assert req and resp
    assert req[0]["tool"] == "calendar_create" and req[0]["summary"]
    assert resp[0]["decision"] == "allow" and resp[0]["by"] == "script"


def test_status_usage_turn_end(tmp_path):
    _, events, _ = replay(tmp_path, "s01-live")
    status = [e for e in events if e["type"] == "status"][-1]
    assert status["status"] == "done"
    usage = [e for e in events if e["type"] == "usage"][-1]
    assert usage["cost_usd"] > 0 and usage["output_tokens"] > 0 and usage["num_turns"] > 0
    assert events[-1]["type"] == "turn_end" and events[-1]["stopped_by"] == "end_turn"


def test_stop_check_block_merged(tmp_path):
    _, events, _ = replay(tmp_path, "stopcheck-live")
    blocks = [e for e in events if e["type"] == "stop_check_block"]
    assert len(blocks) == 1 and "check_event" in blocks[0]["reason"]
    # The assistant then ran the check and finished.
    assert any(e["type"] == "check" for e in events)


def test_parse_status_rules():
    assert parse_status("Done.\n\nSTATUS: done") == ("done", "")
    assert parse_status("x\nSTATUS: waiting — reply yes to go ahead") == ("waiting", "reply yes to go ahead")
    assert parse_status("x\nSTATUS: partial - no hotel") == ("partial", "no hotel")
    assert parse_status("no status here") == ("missing", "")
    assert parse_status("STATUS: done\nmore\nSTATUS: failed — tool error") == ("failed", "tool error")


def _finish_with(tmp_path, result_event):
    run = tmp_path / "r"
    run.mkdir()
    trace = TraceWriter(run / "trace.jsonl")
    m = StreamMapper(trace, run)
    m.feed(result_event)
    h = ClaudeCodeHarness()
    h.run_dir, h.trace = run, trace
    return h._finish(m, "end_turn", "", 2.0)


def test_usage_limit_message(tmp_path):
    r = _finish_with(tmp_path, {"type": "result", "subtype": "error_during_execution", "is_error": True,
                                "result": "Claude usage limit reached. Your limit will reset at 5pm (America/Denver).",
                                "session_id": "s1", "usage": {}, "total_cost_usd": 0})
    assert r.stopped_by == "usage_limit"


def test_max_turns(tmp_path):
    r = _finish_with(tmp_path, {"type": "result", "subtype": "error_max_turns", "is_error": True, "result": "",
                                "session_id": "s1", "usage": {}, "num_turns": 40})
    assert r.stopped_by == "max_turns"


FAKE_CLAUDE = r'''#!{python}
import json, os, sys, shutil
fix = {fix!r}
run = os.environ["EA_RUN_DIR"]
args = sys.argv[1:]
with open(os.path.join(run, "fake_args.json"), "a") as f:
    f.write(json.dumps(args) + "\n")
for name in ("calls.jsonl", "approvals.jsonl"):
    with open(os.path.join(fix, name)) as src, open(os.path.join(run, name), "a") as dst:
        dst.write(src.read())
with open(os.path.join(fix, "stream.jsonl")) as s:
    for line in s:
        sys.stdout.write(line)
        sys.stdout.flush()
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "claude"
    script.write_text(FAKE_CLAUDE.format(python=sys.executable, fix=str(FIX / "s01-live")))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return script


def test_send_through_subprocess(tmp_path, fake_claude):
    """The whole send() path with a fake claude that replays the recording."""
    run = tmp_path / "trial"
    assistant = tmp_path / "assistant"
    assistant.mkdir()
    seen = []

    async def go():
        h = ClaudeCodeHarness()
        await h.start(run, RunConfig(assistant_dir=assistant, on_event=seen.append, model="opus"))
        r1 = await h.send("Find 30 minutes with Dan and Lisa this week to review the Q4 forecast.")
        r2 = await h.send("Thanks.")
        return h, r1, r2

    h, r1, r2 = asyncio.run(go())
    assert r1.status == "done" and r1.stopped_by == "end_turn"
    args = [json.loads(line) for line in (run / "fake_args.json").read_text().splitlines()]
    assert "--resume" not in args[0]
    assert args[1][args[1].index("--resume") + 1] == h.session_id
    for flag in ("--permission-prompt-tool", "--strict-mcp-config", "--setting-sources", "--allowedTools"):
        assert flag in args[0]
    assert args[0][args[0].index("--permission-prompt-tool") + 1] == "mcp__ea-world__approval_prompt"
    mcp = json.loads((run / "mcp.json").read_text())
    env = mcp["mcpServers"]["ea-world"]["env"]
    assert env["EA_RUN_DIR"] == str(run.resolve()) and env["EA_APPROVAL_MODE"] == "script"
    assert (run / "state" / "calendars.json").exists(), "start() initialises the world"
    trace = read_trace(run / "trace.jsonl")
    assert [e["turn"] for e in trace if e["type"] == "user"] == [1, 2]
    assert seen and seen[0]["type"] == "user", "events stream live through on_event"
    assert any(e["type"] == "approval_request" for e in trace)
