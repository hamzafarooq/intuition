"""The instructor-only real-site search demo (ea_harness/real_sites.py): the guard's real-site mode, the
overlay and app switch, and that evals refuse it. Offline: the browser's open tabs are faked."""

from __future__ import annotations

import asyncio
import io
import json
import shutil
from pathlib import Path

import pytest

from ea_evals import runner as R
from ea_evals import variants as V
from ea_harness import browser_guard as bg
from ea_harness import real_sites as RS
from ea_harness.overlay import allowed_tools, build_overlay, mcp_config

KIT = Path(__file__).resolve().parents[1]
MOCK_REASON = "The booking browser only visits the workshop's booking sites."

SEARCH_URLS = [
    "https://www.expedia.com/Flights",
    "https://www.expedia.com/",
    "https://www.expedia.com/Flights-Search?trip=oneway&leg1=from:Denver,to:Chicago,departure:11/4/2026TANYT"
    "&passengers=adults:1&options=cabinclass:economy&mode=search",
    "https://www.expedia.com/Hotel-Search?destination=Chicago&startDate=2026-11-02&endDate=2026-11-04&adults=1",
    "https://www.google.com/travel/flights",
    "https://www.google.com/travel/flights?q=Flights%20from%20DEN%20to%20ORD%20on%202026-11-04",
    "https://www.google.com/travel/flights/search?tfs=CBwQAhoeEgoyMDI2LTExLTA0agcIARIDREVOcgcIARIDT1JE&hl=en",
    "https://www.google.com/travel/hotels/Chicago?q=hotels%20in%20chicago&checkin=2026-11-02&checkout=2026-11-04",
    "https://google.com/travel/hotels",
]
BLOCKED_URLS = [
    # not on the allowlist
    "https://www.kayak.com/flights", "https://www.united.com/en/us", "https://accounts.google.com/ServiceLogin",
    "https://www.google.com/search?q=flights", "https://www.google.com/", "https://www.expedia.com.evil.example/",
    "https://evil.example/?u=https://www.expedia.com/", "https://www.expedia.com@evil.example/",
    "http://www.expedia.com/Flights", "https://www.expedia.com:8443/", "javascript:alert(1)", "file:///etc/passwd",
    # sign-in, account, cart, checkout, payment, booking
    "https://www.expedia.com/login", "https://www.expedia.com/user/signin?ckoflag=0", "https://www.expedia.com/trips",
    "https://www.expedia.com/account/profile", "https://www.expedia.com/Checkout/V1/flights?tripid=1",
    "https://www.expedia.com/Cart", "https://www.expedia.com/payment?x=1", "https://www.expedia.com/BookingConfirmation",
    "https://www.expedia.com/Flights-Search?mode=search&next=checkout",
    "https://www.expedia.com/Hotel-Search?destination=Chicago#/reservation",
    "https://www.expedia.com/trip-details?id=1", "https://www.google.com/travel/flights/booking?tfs=abc",
    "https://www.google.com/travel/flights/search?returnUrl=%2Fsignin",
]


def call(tool: str, **tool_input) -> dict:
    return {"session_id": "s", "tool_use_id": "t", "cwd": "/tmp/assistant", "hook_event_name": "PreToolUse",
            "tool_name": f"mcp__browser__{tool}", "tool_input": tool_input}


EXPEDIA_SNAPSHOT = """## Latest page snapshot
uid=3_0 RootWebArea "Cheap Flights | Expedia" url="https://www.expedia.com/Flights"
  uid=3_1 button "Leaving from"
  uid=3_2 combobox "Going to" focusable
  uid=3_3 textbox "Email address"
  uid=3_4 button "Search" focusable
  uid=3_5 button "Sign in"
  uid=3_6 link "Book now: Denver to Chicago $189" url="https://www.expedia.com/Flights-Search?mode=search"
  uid=3_7 button "Select and continue to traveller details"
  uid=3_8 link "Deals" url="https://www.expedia.com/login?next=deals"
  uid=3_9 textbox "Departing" value="Nov 4"
  uid=3_10 button "Say "hi""
  uid=3_11 button "Reserve now, pay later" focusable focused
"""
SKYWAY_SNAPSHOT = """## Latest page snapshot
uid=4_0 RootWebArea "Skyway" url="http://skyway.localhost:8766/passenger?flight=SK412"
  uid=4_1 textbox "Full name"
  uid=4_2 textbox "Email"
  uid=4_3 button "Book and review"
"""


@pytest.fixture
def run(tmp_path, monkeypatch):
    d = tmp_path / "run"
    d.mkdir()
    (d / "turn_start").write_text("0")
    monkeypatch.setattr(bg, "open_pages", lambda port=RS.PORT: ["https://www.expedia.com/Flights", "about:blank"])
    return d


def see(run: Path, text: str, tool: str = "take_snapshot") -> str | None:
    """What the PostToolUse hook does with a tool result that carries a snapshot."""
    return bg.record_result({"tool_name": f"mcp__browser__{tool}", "tool_input": {"pageId": 2},
                             "tool_response": [{"type": "text", "text": text}]}, run)


# ---------------------------------------------------------------- addresses


@pytest.mark.parametrize("url", SEARCH_URLS)
def test_search_and_results_pages_are_allowed(url, run):
    assert RS.check_url(url) is None, url
    assert bg.decide_real(call("new_page", url=url), run) == ("allow", None)
    assert bg.decide_real(call("navigate_page", pageId=2, type="url", url=url), run) == ("allow", None)


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_foreign_and_commit_pages_are_blocked(url, run):
    assert RS.check_url(url), url
    d, reason = bg.decide_real(call("navigate_page", pageId=2, type="url", url=url), run)
    assert d == "deny" and reason.startswith("Real-site demo:"), url


def test_mock_sites_stay_allowed_in_real_mode(run):
    assert bg.decide_real(call("new_page", url="http://skyway.localhost:8766/"), run) == ("allow", None)
    assert bg.decide_real(call("navigate_page", pageId=1, type="back"), run) == ("allow", None)
    see(run, SKYWAY_SNAPSHOT)
    assert bg.decide_real(call("fill", pageId=1, uid="4_1", value="Sarah Smith"), run) == ("allow", None)
    assert bg.decide_real(call("fill", pageId=1, uid="4_2", value="sarah.smith@larkspur.example"), run) == ("allow", None)
    assert bg.decide_real(call("click", pageId=1, uid="4_3"), run) == ("allow", None)


def test_mode_off_is_unchanged():
    """Without --real-sites the guard is the ordinary mock-only guard: real sites are refused as before."""
    for url in SEARCH_URLS:
        assert bg.decide(call("new_page", url=url)) == ("deny", MOCK_REASON)
    out = io.StringIO()
    bg.main([], stdin=io.StringIO(json.dumps(call("new_page", url=SEARCH_URLS[0]))), stdout=out)
    assert json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecisionReason"] == MOCK_REASON
    assert bg.main(["--post"], stdin=io.StringIO("{}"), stdout=(o := io.StringIO())) == 0 and o.getvalue() == ""


# ---------------------------------------------------------------- clicks, fills and keys


def test_commit_clicks_are_blocked(run):
    see(run, EXPEDIA_SNAPSHOT)
    for uid in ("3_5", "3_6", "3_7", "3_11"):  # Sign in, Book now, Select and continue, Reserve
        d, reason = bg.decide_real(call("click", pageId=2, uid=uid), run)
        assert d == "deny" and "booking, signing in" in reason, uid
    d, reason = bg.decide_real(call("click", pageId=2, uid="3_8"), run)  # a link to a sign-in page
    assert d == "deny" and "login" in reason
    for uid in ("3_1", "3_2", "3_4", "3_10"):  # Leaving from, Going to, Search, a name with quotes
        assert bg.decide_real(call("click", pageId=2, uid=uid), run) == ("allow", None), uid
    assert bg.decide_real(call("hover", pageId=2, uid="3_5"), run) == ("allow", None)


def test_personal_data_fills_are_blocked(run):
    see(run, EXPEDIA_SNAPSHOT)
    d, reason = bg.decide_real(call("fill", pageId=2, uid="3_3", value="chicago"), run)
    assert d == "deny" and "Email address" in reason
    for value in ("Sarah Smith", "sarah.smith@larkspur.example", "someone@example.com", "+1 (303) 555-0142",
                  "4111 1111 1111 1111", "Ben Walsh to Chicago", "dan.okafor@larkspur.example"):
        d, reason = bg.decide_real(call("fill", pageId=2, uid="3_2", value=value), run)
        assert d == "deny" and "personal details" in reason, value
    for value in ("Chicago", "Chicago (ORD - O'Hare Intl.)", "2026-11-04", "11/4/2026", "1 adult", "Denver"):
        assert bg.decide_real(call("fill", pageId=2, uid="3_2", value=value), run) == ("allow", None), value
    d, _ = bg.decide_real(call("fill_form", pageId=2, elements=[{"uid": "3_2", "value": "Chicago"},
                                                                  {"uid": "3_9", "value": "Sarah Smith"}]), run)
    assert d == "deny"
    assert bg.decide_real(call("fill_form", pageId=2, elements=[{"uid": "3_2", "value": "Chicago"},
                                                                 {"uid": "3_9", "value": "Nov 4"}]), run) == ("allow", None)


def test_unknown_or_stale_uids_are_blocked(run):
    d, reason = bg.decide_real(call("click", pageId=2, uid="3_4"), run)
    assert d == "deny" and "snapshot" in reason
    see(run, EXPEDIA_SNAPSHOT)
    assert bg.decide_real(call("click", pageId=2, uid="3_4"), run) == ("allow", None)
    (run / "turn_start").write_text("1")  # a new turn: a new browser server, so old uids mean nothing
    import os

    os.utime(run / "turn_start", ns=(1, 1))
    assert bg.decide_real(call("click", pageId=2, uid="3_4"), run)[0] == "deny"


def test_keys(run):
    see(run, EXPEDIA_SNAPSHOT)  # focus is on "Reserve now, pay later"
    assert bg.decide_real(call("press_key", pageId=2, key="Tab"), run) == ("allow", None)
    d, reason = bg.decide_real(call("press_key", pageId=2, key="Enter"), run)
    assert d == "deny" and "Reserve" in reason
    see(run, EXPEDIA_SNAPSHOT.replace(" focusable focused", "").replace('"Going to" focusable', '"Going to" focused'))
    assert bg.decide_real(call("press_key", pageId=2, key="Enter"), run) == ("allow", None)
    for key in ("a", "Control+A", "Meta+V", "M"):
        assert bg.decide_real(call("press_key", pageId=2, key=key), run)[0] == "deny", key


def test_a_tab_on_a_blocked_page_stops_interaction(run, monkeypatch):
    see(run, EXPEDIA_SNAPSHOT)
    monkeypatch.setattr(bg, "open_pages", lambda port=RS.PORT: ["https://www.expedia.com/Checkout/V1/flights"])
    d, reason = bg.decide_real(call("click", pageId=2, uid="3_4"), run)
    assert d == "deny" and "Go back" in reason
    assert bg.decide_real(call("navigate_page", pageId=2, type="back"), run) == ("allow", None)
    assert bg.decide_real(call("close_page", pageId=2), run) == ("allow", None)
    assert bg.decide_real(call("take_snapshot", pageId=2), run) == ("allow", None)
    monkeypatch.setattr(bg, "open_pages", lambda port=RS.PORT: None)  # the clean browser isn't answering
    assert bg.decide_real(call("click", pageId=2, uid="3_4"), run)[0] == "deny"


def test_tools_and_snapshot_files(run):
    for tool in ("type_text", "evaluate_script", "drag", "upload_file", "list_network_requests"):
        assert bg.decide_real(call(tool, pageId=2, text="Sarah"), run)[0] == "deny", tool
    assert bg.decide_real(call("take_snapshot", pageId=2, filePath="outputs/browser/s.txt"), run)[0] == "deny"
    assert bg.decide_real(call("navigate_page", pageId=2, type="url", url=SEARCH_URLS[0], initScript="x"), run)[0] == "deny"
    assert set(RS.REAL_TOOLS).isdisjoint(RS.DENIED_TOOLS)


# ---------------------------------------------------------------- the hooks end to end


def test_post_hook_records_snapshots_and_flags_off_site_tabs(run):
    assert see(run, EXPEDIA_SNAPSHOT) is None
    pages = json.loads((run / bg.PAGES_FILE).read_text())
    assert pages["nodes"]["3_5"]["name"] == "Sign in" and pages["nodes"]["3_10"]["name"] == 'Say "hi"'
    assert pages["nodes"]["3_6"]["url"].startswith("https://www.expedia.com/") and pages["focus"] == "3_11"
    assert pages["nodes"]["3_2"]["page"] == "https://www.expedia.com/Flights"
    warning = see(run, "Successfully clicked on the element\nPage navigated to https://www.united.com/en/us.", "click")
    assert warning and "united.com" in warning
    listed = "## Pages\n1: about:blank\n2: Sign in (https://www.expedia.com/user/signin) [selected]"
    assert "signin" in see(run, listed, "list_pages")
    assert see(run, "## Pages\n1: Skyway (http://skyway.localhost:8766/) [selected]", "list_pages") is None


def test_hooks_through_main(run, capsys):
    snap = {**call("take_snapshot", pageId=2), "hook_event_name": "PostToolUse",
            "tool_response": [{"type": "text", "text": EXPEDIA_SNAPSHOT}]}
    out = io.StringIO()
    assert bg.main(["--post", "--real-sites", "--run-dir", str(run)], stdin=io.StringIO(json.dumps(snap)), stdout=out) == 0
    assert out.getvalue() == ""
    out = io.StringIO()
    assert bg.main(["--real-sites", "--run-dir", str(run)], stdin=io.StringIO(json.dumps(call("click", pageId=2, uid="3_5"))),
                   stdout=out) == 0
    decision = json.loads(out.getvalue())["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny" and "Sign in" in decision["permissionDecisionReason"]
    out = io.StringIO()
    bg.main(["--real-sites", "--run-dir", str(run)], stdin=io.StringIO(json.dumps(call("click", pageId=2, uid="3_4"))), stdout=out)
    assert out.getvalue() == ""
    off = {**call("click", pageId=2, uid="3_4"), "hook_event_name": "PostToolUse",
           "tool_response": [{"type": "text", "text": "Page navigated to https://www.expedia.com/Checkout/V1/x."}]}
    out = io.StringIO()
    bg.main(["--post", "--real-sites", "--run-dir", str(run)], stdin=io.StringIO(json.dumps(off)), stdout=out)
    assert "Checkout" in json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
    log = [json.loads(line) for line in (run / "browser.jsonl").read_text().splitlines()]
    assert [r["decision"] for r in log] == ["deny", "allow", "flag"] and all(r["mode"] == "real-sites" for r in log)


def test_real_mode_fails_closed(run, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(bg, "decide_real", boom)
    out = io.StringIO()
    bg.main(["--real-sites", "--run-dir", str(run)], stdin=io.StringIO(json.dumps(call("new_page", url=SEARCH_URLS[0]))),
            stdout=out)
    assert json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_adapter_records_blocks_and_flags_in_the_trace(run):
    from ea_harness.claude_code import ClaudeCodeHarness, _Tail
    from ea_harness.trace import TraceWriter

    h = ClaudeCodeHarness()
    h.run_dir, h.config = run, None
    h.trace = TraceWriter(run / "trace.jsonl", {})
    h.tails = {n: _Tail(run / f"{n}.jsonl") for n in ("approvals", "stopcheck", "site", "browser")}
    see(run, EXPEDIA_SNAPSHOT)
    bg.main(["--real-sites", "--run-dir", str(run)], stdin=io.StringIO(json.dumps(call("click", pageId=2, uid="3_5"))),
            stdout=io.StringIO())
    h._merge_logs()
    steps = [json.loads(line) for line in (run / "trace.jsonl").read_text().splitlines()]
    assert [s["action"] for s in steps if s["type"] == "browser_step"] == ["blocked"]


# ---------------------------------------------------------------- overlay, permissions and MCP


def test_overlay_real_sites(tmp_path):
    d = build_overlay(tmp_path / "r", {}, real_sites=True)
    s = json.loads((d / ".claude/settings.json").read_text())
    pre, post = s["hooks"]["PreToolUse"][0], s["hooks"]["PostToolUse"][0]
    assert pre["matcher"] == post["matcher"] == "mcp__browser__.*"
    assert "browser_guard --real-sites --run-dir" in pre["hooks"][0]["command"]
    assert "browser_guard --post --real-sites --run-dir" in post["hooks"][0]["command"]
    browser_allow = [r for r in s["permissions"]["allow"] if r.startswith("mcp__browser__")]
    assert browser_allow == RS.permission_allow() and not any("*" in r for r in browser_allow)
    assert set(RS.permission_deny()) <= set(s["permissions"]["deny"])
    assert allowed_tools(True, real_sites=True)[-len(RS.REAL_TOOLS):] == RS.permission_allow()
    cfg = mcp_config(tmp_path, {"PATH": "/bin", "HOME": "/tmp"}, True, real_sites=True)
    assert cfg["mcpServers"]["browser"]["args"][-1] == f"http://127.0.0.1:{RS.PORT}" != "http://127.0.0.1:9222"
    travel = (d / ".claude/agents/travel.md").read_text()
    assert "Real-site search (instructor demo)" in travel and "<!--" not in travel
    for site in RS.SITES:
        assert site.start in travel
    assert "Real-site search" in (d / "CLAUDE.md").read_text()
    assert "Real-site search" in (d / ".claude/skills/travel-booking/SKILL.md").read_text()


@pytest.mark.parametrize("browser", [False, True])
def test_overlay_without_the_demo_has_none_of_it(tmp_path, monkeypatch, browser):
    monkeypatch.setenv("EA_REAL_SITES", "on")  # the switch alone changes nothing outside the app
    d = build_overlay(tmp_path / "r", {}, browser=browser)
    s = json.loads((d / ".claude/settings.json").read_text())
    text = "\n".join(p.read_text() for p in [d / "CLAUDE.md", *(d / ".claude").rglob("*.md")])
    assert "Real-site" not in text and "realsites" not in text and "expedia" not in text.lower()
    assert "PostToolUse" not in s.get("hooks", {})
    assert "--real-sites" not in json.dumps(s)
    assert "9222" in json.dumps(mcp_config(tmp_path, {}, True)) and "9223" not in json.dumps(mcp_config(tmp_path, {}, True))
    assert "mcp__browser__type_text" not in s["permissions"]["deny"]


def test_source_assistant_blocks_nest_inside_browser_blocks():
    """An overlay with browser mode off (even one built by older code) strips the real-site text."""
    for p in [KIT / "assistant/CLAUDE.md", *(KIT / "assistant/.claude").rglob("*.md")]:
        text = p.read_text()
        stripped = bg.re.sub(r"<!-- browser:start -->.*?<!-- browser:end -->", "", text, flags=bg.re.S)
        assert "realsites" not in stripped and "Real-site" not in stripped, p


# ---------------------------------------------------------------- evals refuse it


def test_variant_browser_real_is_refused():
    with pytest.raises(ValueError, match="instructor-only real-site search demo"):
        V.parse(["browser=real"])
    with pytest.raises(ValueError, match="demo"):
        V.parse(["skills=off,browser=real"])
    assert V.parse(["browser=on"]) == {"browser": "on"}


def test_runner_refuses_real_sites_before_starting(tmp_path, monkeypatch):
    from ea_evals.cases import load_all
    from ea_evals.costs import CostMeter
    from ea_evals.judge import FakeJudge

    monkeypatch.setattr(R, "results_dir", lambda: tmp_path / "results")

    def no_harness():
        raise AssertionError("no trial may start")

    r = R.Runner(R.RunOptions(run_id="t-real", parallel=1, rejudge_fraction=0), harness_factory=no_harness,
                 judge=FakeJudge(), meter=CostMeter(max_openai_usd=1.0), sim_use_model=False, out=lambda s: None)
    specs = r.plan_from([(load_all()["R01"], {"browser": "real"})], 1)
    with pytest.raises(ValueError, match="evals refuse it"):
        asyncio.run(r.run(specs))
    with pytest.raises(ValueError, match="evals refuse it"):
        asyncio.run(r.run_trial(specs[0]))
    assert not list((tmp_path / "results").rglob("trace.jsonl"))


# ---------------------------------------------------------------- the app switch


def _app(tmp_path, monkeypatch, real: bool):
    from starlette.testclient import TestClient

    from ea_app.fake import FakeHarness
    from ea_app.server import create_app

    monkeypatch.setenv("EA_KIT_ROOT", str(tmp_path))
    monkeypatch.setenv("EA_WORLD_DIR", str(KIT / "world"))
    shutil.copytree(KIT / "assistant", tmp_path / "assistant")
    app = create_app(lambda name: FakeHarness(delay=0, poll=0.02), real_sites=real,
                     claude={"installed": True, "logged_in": True, "version": "test", "message": ""},
                     browser={"available": True, "debug_port_open": True, "installed": [], "default": True, "message": ""})
    return TestClient(app)


def test_app_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("EA_REAL_SITES", "on")  # the env alone doesn't turn on an app built without the switch
    with _app(tmp_path, monkeypatch, real=False) as c:
        assert c.get("/api/config").json()["real_sites"]["enabled"] is False
        r = c.post("/api/sessions", json={"harness": "claude_code", "browser": True, "real_sites": True})
        assert r.status_code == 400 and "instructor demo" in r.json()["error"]
        r = c.post("/api/sessions", json={"harness": "claude_code", "browser": True})
        assert r.status_code == 201 and r.json()["options"]["real_sites"] is False


def test_app_with_the_switch(tmp_path, monkeypatch):
    with _app(tmp_path, monkeypatch, real=True) as c:
        cfg = c.get("/api/config").json()["real_sites"]
        assert cfg["enabled"] is True and cfg["label"] == RS.LABEL and cfg["port"] == RS.PORT
        r = c.post("/api/sessions", json={"harness": "claude_code", "browser": True})
        assert r.status_code == 201 and r.json()["options"]["real_sites"] is True
        settings = json.loads((Path(r.json()["run_dir"]) / "assistant/.claude/settings.json").read_text())
        assert "--real-sites" in settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        assert c.post("/api/sessions", json={"harness": "claude_code", "browser": False}).json()["options"]["real_sites"] is False
        assert c.post("/api/sessions", json={"harness": "fake", "browser": True}).json()["options"]["real_sites"] is False
        page = c.get("/").text
        assert 'id="real-badge"' in page and "Real-site search (instructor demo)" in page
