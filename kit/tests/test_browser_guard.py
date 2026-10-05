"""The booking browser's PreToolUse guard (`ea_harness.browser_guard`): which URLs and tools it lets
through, the deny output Claude Code expects, the browser.jsonl log, and that it never crashes."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ea_harness import browser_guard as bg

KIT = Path(__file__).resolve().parents[1]
REASON = "The booking browser only visits the workshop's booking sites."

ALLOWED = [
    "http://skyway.localhost:8766",
    "http://skyway.localhost:8766/",
    "http://stays.localhost:8766/results?city=Chicago&check_in=2026-11-02&check_out=2026-11-04&rooms=1",
    "http://tables.localhost:8766/booking/bk-003",
    "https://skyway.localhost:8766/review/HOLD-7K2P",
    "HTTP://SKYWAY.LOCALHOST:8766/",
    "  http://stays.localhost:8766/  ",
    "http://localhost:8766/skyway",
    "http://localhost:8766/skyway/",
    "http://localhost:8766/stays/holds/HOLD-AB12",
    "http://localhost:8766/tables?x=1",
    "http://127.0.0.1:8766/tables/results?city=Chicago",
    "http://skyway.localhost:8766/#top",
]

DENIED = [
    "https://example.com",
    "http://example.com/skyway",
    "http://skyway.localhost.evil.example:8766/",
    "http://skyway.localhost:8766.evil.example/",
    "http://evil.example/?u=http://skyway.localhost:8766",
    "http://skyway.localhost:8766@evil.example/",
    "http://evil.example@skyway.localhost:8766/",
    "http://evil.example\\@skyway.localhost:8766/",
    "http://skyway.localhost/",
    "http://skyway.localhost:8767/",
    "http://skyway.localhost:80/",
    "https://skyway.localhost/",
    "http://localhost:8766/",
    "http://localhost:8766/skywayx",
    "http://localhost:8766/admin",
    "http://localhost:9222/json",
    "http://127.0.0.1:8766/",
    "http://[::1]:8766/skyway/",
    "http://0.0.0.0:8766/skyway/",
    "http://localhost:8766/skyway/../admin",
    "http://localhost:8766/skyway/%2e%2e/admin",
    "http://evil.localhost:8766/",
    "http://skyway.localhost.:8766/",
    "http://sky way.localhost:8766/",
    "http://skyway.localhost:8766/\nhttp://evil.example",
    "javascript:alert(1)",
    "javascript://skyway.localhost:8766/%0aalert(1)",
    "data:text/html,<h1>hi</h1>",
    "file:///etc/passwd",
    "chrome://settings",
    "about:blank",
    "view-source:http://skyway.localhost:8766/",
    "ftp://skyway.localhost:8766/",
    "//skyway.localhost:8766/",
    "skyway.localhost:8766/",
    "/booking/bk-001",
    "",
    "http://skyway.localhost:8766/" + "a" * 5000,
]


def run_guard(payload, tmp_path: Path, raw: str | None = None) -> tuple[dict | None, list[dict]]:
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    out = io.StringIO()
    stdin = io.StringIO(raw if raw is not None else json.dumps(payload))
    assert bg.main(["--run-dir", str(run)], stdin=stdin, stdout=out) == 0
    text = out.getvalue().strip()
    log = run / "browser.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return (json.loads(text) if text else None), records


def call(tool: str, **tool_input) -> dict:
    return {"session_id": "sess-1", "tool_use_id": "toolu_1", "cwd": "/tmp/assistant", "hook_event_name": "PreToolUse",
            "tool_name": f"mcp__browser__{tool}", "tool_input": tool_input}


@pytest.mark.parametrize("url", ALLOWED)
def test_booking_site_urls_are_allowed(url):
    assert bg.url_allowed(url), url
    assert bg.decide(call("new_page", url=url)) == ("allow", None)
    assert bg.decide(call("navigate_page", pageId=1, type="url", url=url)) == ("allow", None)


@pytest.mark.parametrize("url", DENIED)
def test_other_urls_are_denied(url):
    assert not bg.url_allowed(url), url
    assert bg.decide(call("new_page", url=url)) == ("deny", REASON)
    assert bg.decide(call("navigate_page", pageId=1, type="url", url=url)) == ("deny", REASON)


def test_non_string_urls_are_denied():
    for url in (None, 8766, ["http://skyway.localhost:8766/"], {"href": "x"}):
        assert bg.decide(call("navigate_page", pageId=1, url=url))[0] == "deny"


@pytest.mark.parametrize("nav", ["back", "forward", "reload"])
def test_history_navigation_without_a_url_is_allowed(nav):
    assert bg.decide(call("navigate_page", pageId=1, type=nav)) == ("allow", None)
    assert bg.decide(call("navigate_page", pageId=1, type=nav, ignoreCache=True)) == ("allow", None)
    # a url smuggled into a history navigation is still checked
    assert bg.decide(call("navigate_page", pageId=1, type=nav, url="https://example.com"))[0] == "deny"


def test_navigation_without_a_target_is_denied():
    assert bg.decide(call("navigate_page", pageId=1)) == ("deny", REASON)
    assert bg.decide(call("navigate_page", pageId=1, type="url")) == ("deny", REASON)
    assert bg.decide(call("new_page")) == ("deny", REASON)


def test_init_script_is_denied_even_on_an_allowed_site():
    d, reason = bg.decide(call("navigate_page", pageId=1, type="url", url="http://skyway.localhost:8766/",
                               initScript="location='https://example.com'"))
    assert d == "deny" and "script" in reason


def test_any_url_in_the_input_is_checked():
    assert bg.decide(call("wait_for", text=["Confirmed"])) == ("allow", None)
    assert bg.decide(call("click", uid="1_2", url="https://example.com")) == ("deny", REASON)
    assert bg.decide(call("fill_form", elements=[{"uid": "1", "value": "x", "url": "file:///etc/passwd"}])) == ("deny", REASON)
    # the guard also checks url fields if it's ever wired to a non-browser tool
    assert bg.decide({"tool_name": "WebFetch", "tool_input": {"url": "https://example.com"}}) == ("deny", REASON)
    assert bg.decide({"tool_name": "WebFetch", "tool_input": {"url": "http://skyway.localhost:8766/"}}) == ("allow", None)


@pytest.mark.parametrize("tool", sorted(bg.ALLOWED_TOOLS))
def test_allowed_browser_tools_pass(tool):
    payload = call(tool, pageId=1, url="http://tables.localhost:8766/") if tool in ("new_page", "navigate_page") else call(tool, pageId=1, uid="1_1")
    assert bg.decide(payload) == ("allow", None)


@pytest.mark.parametrize("tool", ["evaluate_script", "upload_file", "handle_dialog", "emulate", "lighthouse_audit",
                                  "performance_start_trace", "performance_stop_trace", "performance_analyze_insight",
                                  "type_text", "drag", "resize_page", "take_heapsnapshot", "list_network_requests"])
def test_other_browser_tools_are_denied(tool):
    d, reason = bg.decide(call(tool, pageId=1, function="() => 1"))
    assert d == "deny" and "isn't available" in reason


def test_screenshots_only_save_under_outputs_browser(tmp_path):
    good = str(tmp_path / "runs" / "r1" / "outputs" / "browser" / "skyway-review.png")
    assert bg.decide(call("take_screenshot", pageId=1, filePath=good)) == ("allow", None)
    assert bg.decide(call("take_screenshot", pageId=1)) == ("allow", None)  # attached to the reply, not saved
    assert bg.decide({**call("take_screenshot", pageId=1, filePath="outputs/browser/a.jpeg"), "cwd": str(tmp_path)})[0] == "allow"
    assert bg.decide(call("take_snapshot", pageId=1, filePath=str(tmp_path / "outputs/browser/snap.txt")))[0] == "allow"
    for bad in ("/tmp/x.png", str(tmp_path / "outputs" / "browser" / "x.py"), str(tmp_path / "outputs/browser/../../.claude/settings.json"),
                str(tmp_path / "outputs" / "x.png"), "~/Desktop/x.png"):
        assert bg.decide(call("take_screenshot", pageId=1, filePath=bad))[0] == "deny", bad
    assert bg.decide(call("take_snapshot", pageId=1, filePath=str(tmp_path / "CLAUDE.md")))[0] == "deny"


def test_deny_prints_the_pre_tool_use_decision_and_logs(tmp_path):
    out, records = run_guard(call("new_page", url="https://example.com"), tmp_path)
    assert out == {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                          "permissionDecisionReason": REASON}}
    (rec,) = records
    assert rec["decision"] == "deny" and rec["reason"] == REASON and rec["url"] == "https://example.com"
    assert rec["tool"] == "mcp__browser__new_page" and rec["session_id"] == "sess-1" and rec["tool_use_id"] == "toolu_1"
    assert rec["input"] == {"url": "https://example.com"} and rec["ts"]


def test_allow_prints_nothing_and_logs(tmp_path):
    out, records = run_guard(call("navigate_page", pageId=2, type="url", url="http://skyway.localhost:8766/"), tmp_path)
    assert out is None  # silence: Claude Code's own permission rules still decide
    out, records = run_guard(call("take_snapshot", pageId=2), tmp_path)
    assert out is None
    assert [(r["tool"], r["decision"]) for r in records] == [("mcp__browser__navigate_page", "allow"),
                                                             ("mcp__browser__take_snapshot", "allow")]
    assert records[0]["url"] == "http://skyway.localhost:8766/" and records[1]["url"] is None


@pytest.mark.parametrize("raw", ["", "not json", "[1, 2]", "null", '{"tool_name": 5, "tool_input": "x"}', "{\"tool_name\": "])
def test_malformed_input_never_crashes(tmp_path, raw, capsys):
    out, records = run_guard(None, tmp_path, raw=raw)
    assert out is None or out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert len(records) == 1


def test_internal_errors_allow_and_report(tmp_path, monkeypatch, capsys):
    def boom(payload):
        raise RuntimeError("bug")

    monkeypatch.setattr(bg, "decide", boom)
    out, records = run_guard(call("new_page", url="https://example.com"), tmp_path)
    assert out is None
    assert records[0]["decision"] == "allow" and "bug" in records[0]["error"]
    assert "bug" in capsys.readouterr().err


def test_run_dir_comes_from_env_then_current(tmp_path, monkeypatch):
    run = tmp_path / "envrun"
    run.mkdir()
    monkeypatch.setenv("EA_RUN_DIR", str(run))
    assert bg.main([], stdin=io.StringIO(json.dumps(call("list_pages"))), stdout=io.StringIO()) == 0
    assert (run / "browser.jsonl").exists()
    monkeypatch.delenv("EA_RUN_DIR")
    kit = tmp_path / "kit"
    cur = tmp_path / "currun"
    cur.mkdir()
    (kit / "runs").mkdir(parents=True)
    (kit / "runs" / "CURRENT").write_text(str(cur) + "\n")
    monkeypatch.setenv("EA_KIT_ROOT", str(kit))
    bg.main([], stdin=io.StringIO(json.dumps(call("list_pages"))), stdout=io.StringIO())
    assert (cur / "browser.jsonl").exists()
    # no run dir anywhere: still fine, nothing logged
    (kit / "runs" / "CURRENT").unlink()
    out = io.StringIO()
    assert bg.main([], stdin=io.StringIO(json.dumps(call("new_page", url="https://example.com"))), stdout=out) == 0
    assert json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_runs_as_a_module(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "EA_RUN_DIR"}
    p = subprocess.run([sys.executable, "-m", "ea_harness.browser_guard", "--run-dir", str(run)], cwd=KIT, env=env,
                       input=json.dumps(call("new_page", url="file:///etc/passwd")), capture_output=True, text=True,
                       timeout=60)
    assert p.returncode == 0
    assert json.loads(p.stdout)["hookSpecificOutput"]["permissionDecisionReason"] == REASON
    p = subprocess.run([sys.executable, "-m", "ea_harness.browser_guard", "--run-dir", str(run)], cwd=KIT, env=env,
                       input="garbage", capture_output=True, text=True, timeout=60)
    assert p.returncode == 0 and p.stdout == "" and "allowing" in p.stderr
    assert len((run / "browser.jsonl").read_text().splitlines()) == 2
