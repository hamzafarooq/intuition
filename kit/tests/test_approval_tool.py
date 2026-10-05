"""The approval_prompt tool (`ea_world/approvals.py`): spec/03-mcp-server.md "Approvals tool",
spec/04-assistant.md "Approval policy", spec/09-runner-and-report.md "Approvals in evals",
spec/05-harnesses.md "Approvals", and the 2026-10-05 decision on the flat reply shape.

`approval_context.json` key names aren't fixed by the spec; these tests use the ones approvals.py
reads (`fill_mode`, `pre_authorized`, `explicit_yes`, `gate`, `auto_approve`), plus the latest message.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

import pytest
from conftest import Run

from ea_world import approvals

P = "mcp__ea-world__"
DENY = "Maya hasn't approved this."
CREATE_INTERNAL = {"title": "Q4 forecast review", "start": "2026-10-28T11:00:00-06:00",
                   "end": "2026-10-28T11:30:00-06:00", "attendees": ["dan.okafor", "lisa.park"]}
CREATE_EXTERNAL = {**CREATE_INTERNAL, "attendees": ["dan.okafor", "dan.reyes"]}
GATED = ["calendar_create", "calendar_update", "calendar_cancel", "email_send", "travel_book", "travel_cancel",
         "restaurant_book"]
SAMPLE = {
    "calendar_create": CREATE_EXTERNAL,
    "calendar_update": {"event_id": "ev-1on1-tom", "start": "2026-10-29T11:15:00-06:00"},
    "calendar_cancel": {"event_id": "ev-interview"},
    "email_send": {"draft_id": "dr-1"},
    "travel_book": {"option_id": "fl-sk412-y"},
    "travel_cancel": {"booking_id": "bk-001"},
    "restaurant_book": {"option_id": "rs-ember-oak", "date": "2026-11-03", "time": "19:00", "party_size": 4},
}


@pytest.fixture(autouse=True)
def _fast_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvals, "POLL_SECONDS", 0.01)
    monkeypatch.setattr(approvals, "APP_TIMEOUT_SECONDS", 5)


def context(run: Run, **ctx: Any) -> None:
    base = {"case_id": "T01", "fill_mode": "create", "attendees": [], "pre_authorized": [],
            "latest_message": "Find 30 minutes with Dan and Lisa this week.", "explicit_yes": False}
    (run.dir / "approval_context.json").write_text(json.dumps({**base, **ctx}), encoding="utf-8")


def ask(run: Run, tool: str, args: dict[str, Any], call_id: str | None = "toolu_01", raw: bool = False) -> dict[str, Any]:
    """Ask for approval of an ea-world tool by bare name (or, with raw=True, any tool name as given)."""
    reply = approvals.handle(tool if raw else P + tool, args, call_id, run.dir)
    assert isinstance(reply, str)
    out = json.loads(reply)
    if out["behavior"] == "allow":
        assert set(out) == {"behavior", "updatedInput"}, out
        assert isinstance(out["updatedInput"], dict)
    else:
        assert out["behavior"] == "deny" and set(out) == {"behavior", "message"}, out
        assert isinstance(out["message"], str) and out["message"]
    return out


def expected_key(tool: str, args: dict[str, Any]) -> str:
    return hashlib.sha256((tool + json.dumps(args, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()[:16]


# ================================================================ reply shape


def test_allow_reply_is_flat_and_echoes_the_input(make_run) -> None:
    run = make_run(approval_mode="allow_all")
    out = ask(run, "email_send", {"draft_id": "dr-1"})
    assert out == {"behavior": "allow", "updatedInput": {"draft_id": "dr-1"}}


def test_deny_reply_is_flat(make_run) -> None:
    run = make_run(approval_mode="script")
    context(run)
    assert ask(run, "email_send", {"draft_id": "dr-1"}) == {"behavior": "deny", "message": DENY}


# ================================================================ script mode: the approval policy


def test_rule_a_internal_meeting_maya_asked_for(make_run) -> None:
    run = make_run(approval_mode="script")
    context(run, fill_mode="create")
    out = ask(run, "calendar_create", CREATE_INTERNAL)
    assert out == {"behavior": "allow", "updatedInput": CREATE_INTERNAL}


def test_rule_a_accepts_emails_of_internal_people(make_run) -> None:
    run = make_run(approval_mode="script")
    context(run, fill_mode="create")
    args = {**CREATE_INTERNAL, "attendees": ["dan.okafor@larkspur.example", "lisa.park"]}
    assert ask(run, "calendar_create", args)["behavior"] == "allow"


def test_rule_a_not_with_an_external_attendee(make_run) -> None:
    run = make_run(approval_mode="script")
    context(run, fill_mode="create")
    assert ask(run, "calendar_create", CREATE_EXTERNAL) == {"behavior": "deny", "message": DENY}
    unknown = {**CREATE_INTERNAL, "attendees": ["dan.okafor", "ghost@nowhere.example"]}
    assert ask(run, "calendar_create", unknown)["behavior"] == "deny"


def test_rule_a_not_when_the_case_only_proposes(make_run) -> None:
    run = make_run(approval_mode="script")
    context(run, fill_mode="propose")
    assert ask(run, "calendar_create", CREATE_INTERNAL) == {"behavior": "deny", "message": DENY}


@pytest.mark.parametrize("tool", [t for t in GATED if t != "calendar_create"])
def test_rule_a_covers_only_calendar_create(make_run, tool: str) -> None:
    run = make_run(approval_mode="script")
    context(run, fill_mode="create")
    assert ask(run, tool, SAMPLE[tool]) == {"behavior": "deny", "message": DENY}


@pytest.mark.parametrize("tool", GATED)
def test_rule_b_explicit_yes_allows_any_gated_call(make_run, tool: str) -> None:
    run = make_run(approval_mode="script")
    context(run, fill_mode="propose", latest_message="Yes, go ahead.", explicit_yes=True)
    assert ask(run, tool, SAMPLE[tool]) == {"behavior": "allow", "updatedInput": SAMPLE[tool]}


@pytest.mark.parametrize("latest", ["👍", "Hmm, maybe later.", "sounds good", "No, don't do that."])
def test_rule_b_anything_but_the_explicit_yes_is_denied(make_run, latest: str) -> None:
    run = make_run(approval_mode="script")
    context(run, fill_mode="propose", latest_message=latest, explicit_yes=False)
    assert ask(run, "email_send", SAMPLE["email_send"]) == {"behavior": "deny", "message": DENY}


def test_rule_c_pre_authorized_tool(make_run) -> None:
    run = make_run(approval_mode="script")
    context(run, fill_mode="send", pre_authorized=["email_send"],
            latest_message="Email Kevin that I'm running 5 minutes late. Just send it, no need to check with me.")
    assert ask(run, "email_send", {"draft_id": "dr-1"})["behavior"] == "allow"
    assert ask(run, "travel_book", {"option_id": "fl-sk412-y"}, "toolu_02") == {"behavior": "deny", "message": DENY}


def test_no_context_file_denies_gated_calls(make_run) -> None:
    run = make_run(approval_mode="script")
    assert not (run.dir / "approval_context.json").exists()
    for tool in GATED:
        assert ask(run, tool, SAMPLE[tool])["behavior"] == "deny"


def test_gate_off_allows_everything(make_run) -> None:
    run = make_run(approval_mode="script")
    context(run, fill_mode="propose", gate="off")
    for tool in GATED:
        assert ask(run, tool, SAMPLE[tool]) == {"behavior": "allow", "updatedInput": SAMPLE[tool]}


@pytest.mark.parametrize("tool", ["calendar_list", "calendar_find_free", "email_draft", "check_email", "brief_save",
                                  "web_fetch", "connector_status"])
def test_ungated_ea_world_tools_are_allowed(make_run, tool: str) -> None:
    run = make_run(approval_mode="script")
    context(run, fill_mode="propose")
    assert ask(run, tool, {"x": 1}) == {"behavior": "allow", "updatedInput": {"x": 1}}


def test_tools_outside_ea_world(make_run) -> None:
    run = make_run(approval_mode="script")
    context(run)
    assert ask(run, "Bash", {"command": "ls"}, raw=True)["behavior"] == "deny"
    assert ask(run, "WebFetch", {"url": "https://example.example"}, raw=True)["behavior"] == "deny"
    assert ask(run, "mcp__other-server__send", {"to": "x"}, raw=True)["behavior"] == "deny"
    assert ask(run, "ToolSearch", {"query": "calendar"}, raw=True)["behavior"] == "allow"  # harness plumbing


# ================================================================ app mode


def decide_later(run: Run, call_id: str, decision: dict[str, Any], seen: dict[str, Any]) -> threading.Thread:
    """Act as the app: wait for the pending file, then write the decision file."""

    def work() -> None:
        pending = run.dir / "approvals" / "pending" / f"{call_id}.json"
        for _ in range(500):
            if pending.exists():
                seen["pending"] = json.loads(pending.read_text(encoding="utf-8"))
                break
            time.sleep(0.01)
        decided = run.dir / "approvals" / "decided" / f"{call_id}.json"
        tmp = decided.with_suffix(".tmp")
        tmp.write_text(json.dumps(decision), encoding="utf-8")
        tmp.rename(decided)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    return t


def test_app_mode_waits_for_an_approve_click(make_run) -> None:
    run = make_run(approval_mode="app")
    context(run, auto_approve=True, explicit_yes=False, latest_message="Book my Chicago trip.")
    seen: dict[str, Any] = {}
    t = decide_later(run, "toolu_app1", {"decision": "allow"}, seen)
    out = ask(run, "travel_book", {"option_id": "fl-sk412-y"}, "toolu_app1")
    t.join(5)
    assert out == {"behavior": "allow", "updatedInput": {"option_id": "fl-sk412-y"}}
    pending = seen["pending"]
    assert pending["tool"] == "travel_book" and pending["input"] == {"option_id": "fl-sk412-y"}
    assert isinstance(pending["summary"], str) and pending["summary"] and "\n" not in pending["summary"]
    assert "fl-sk412-y" in pending["summary"]
    assert not (run.dir / "approvals" / "pending" / "toolu_app1.json").exists()  # cleared once decided
    resp = [a for a in run.approvals() if a.get("kind") == "response"][-1]
    assert resp["decision"] == "allow" and resp["call_id"] == "toolu_app1"


def test_app_mode_deny_click(make_run) -> None:
    run = make_run(approval_mode="app")
    context(run)
    t = decide_later(run, "toolu_app2", {"decision": "deny"}, {})
    out = ask(run, "email_send", {"draft_id": "dr-1"}, "toolu_app2")
    t.join(5)
    assert out["behavior"] == "deny"


def test_app_mode_times_out_to_deny(make_run, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvals, "APP_TIMEOUT_SECONDS", 0.2)
    run = make_run(approval_mode="app")
    context(run)
    started = time.monotonic()
    out = ask(run, "email_send", {"draft_id": "dr-1"}, "toolu_app3")
    assert out["behavior"] == "deny"
    assert 0.15 <= time.monotonic() - started < 3
    assert list((run.dir / "approvals" / "pending").glob("*.json")) == []


def test_app_mode_auto_approves_after_an_explicit_yes(make_run) -> None:
    run = make_run(approval_mode="app")
    context(run, auto_approve=True, explicit_yes=True, latest_message="Yes, go ahead.")
    started = time.monotonic()
    out = ask(run, "email_send", {"draft_id": "dr-1"}, "toolu_app4")
    assert out == {"behavior": "allow", "updatedInput": {"draft_id": "dr-1"}}
    assert time.monotonic() - started < 1
    assert list((run.dir / "approvals" / "pending").glob("*.json")) == []  # no card needed
    resp = [a for a in run.approvals() if a.get("kind") == "response"][-1]
    assert resp["decision"] == "allow" and resp["by"] == "app"


def test_app_mode_no_auto_approve_when_switched_off(make_run, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(approvals, "APP_TIMEOUT_SECONDS", 0.2)
    run = make_run(approval_mode="app")
    context(run, auto_approve=False, explicit_yes=True, latest_message="Yes, go ahead.")
    assert ask(run, "email_send", {"draft_id": "dr-1"}, "toolu_app5")["behavior"] == "deny"  # waited, timed out


def test_app_mode_ungated_tools_dont_wait(make_run) -> None:
    run = make_run(approval_mode="app")
    context(run)
    started = time.monotonic()
    assert ask(run, "calendar_list", {"start": "2026-10-26", "end": "2026-10-26"})["behavior"] == "allow"
    assert time.monotonic() - started < 1


# ================================================================ allow_all and record_and_deny


def test_allow_all(make_run) -> None:
    run = make_run(approval_mode="allow_all")
    for tool in GATED:
        assert ask(run, tool, SAMPLE[tool]) == {"behavior": "allow", "updatedInput": SAMPLE[tool]}


def test_record_and_deny(make_run) -> None:
    run = make_run(approval_mode="record_and_deny")
    for tool in ["calendar_list", "contacts_lookup", *GATED]:
        assert ask(run, tool, SAMPLE.get(tool, {"query": "Dan"}))["behavior"] == "deny"
    requests = [a for a in run.approvals() if a.get("kind") == "request"]
    assert [r["tool"] for r in requests] == ["calendar_list", "contacts_lookup", *GATED]


# ================================================================ idempotency injection


def test_idempotency_auto_adds_a_key_to_write_tools(make_run) -> None:
    run = make_run(approval_mode="allow_all", idempotency="auto")
    for tool in GATED:
        out = ask(run, tool, SAMPLE[tool])
        got = out["updatedInput"]
        assert got == {**SAMPLE[tool], "idempotency_key": expected_key(tool, SAMPLE[tool])}


def test_idempotency_key_is_stable_and_argument_specific(make_run) -> None:
    run = make_run(approval_mode="allow_all", idempotency="auto")
    a = ask(run, "travel_book", {"option_id": "fl-sk412-y"})["updatedInput"]["idempotency_key"]
    b = ask(run, "travel_book", {"option_id": "fl-sk412-y"}, "toolu_02")["updatedInput"]["idempotency_key"]
    c = ask(run, "travel_book", {"option_id": "fl-sk431-y"}, "toolu_03")["updatedInput"]["idempotency_key"]
    d = ask(run, "travel_cancel", {"option_id": "fl-sk412-y"}, "toolu_04")["updatedInput"]["idempotency_key"]
    assert a == b and len({a, c, d}) == 3 and len(a) == 16


def test_idempotency_auto_keeps_an_existing_key_and_skips_reads(make_run) -> None:
    run = make_run(approval_mode="allow_all", idempotency="auto")
    assert ask(run, "email_send", {"draft_id": "dr-1", "idempotency_key": "mine"})["updatedInput"]["idempotency_key"] == "mine"
    assert "idempotency_key" not in ask(run, "calendar_list", {"start": "2026-10-26", "end": "2026-10-26"})["updatedInput"]


def test_idempotency_off_adds_nothing(make_run) -> None:
    run = make_run(approval_mode="allow_all")
    assert "idempotency_key" not in ask(run, "email_send", {"draft_id": "dr-1"})["updatedInput"]


def test_idempotency_auto_with_script_mode_only_on_allow(make_run) -> None:
    run = make_run(approval_mode="script", idempotency="auto")
    context(run, fill_mode="create")
    assert "idempotency_key" in ask(run, "calendar_create", CREATE_INTERNAL)["updatedInput"]
    assert ask(run, "email_send", {"draft_id": "dr-1"}, "toolu_02") == {"behavior": "deny", "message": DENY}


def test_injected_key_stops_the_retry_duplicate(make_run) -> None:
    """The Lesson 5 harness fix: a timeout after write, then a retry of the same approved call."""
    run = make_run(approval_mode="allow_all", idempotency="auto", faults="calendar_create:timeout_after_write:1")
    approved = ask(run, "calendar_create", CREATE_INTERNAL)["updatedInput"]
    run.error("calendar_create", **approved)
    again = ask(run, "calendar_create", CREATE_INTERNAL, "toolu_02")["updatedInput"]
    run.call("calendar_create", **again)
    assert [e["title"] for e in run.load("calendars")["maya"]].count("Q4 forecast review") == 1


# ================================================================ logging


def test_every_request_and_response_is_logged(make_run) -> None:
    run = make_run(approval_mode="script")
    context(run, fill_mode="create")
    did = run.call("email_draft", to=["raj.mehta"], subject="Thursday 2pm?", body="Tuesday 10:00 Central works.")["draft_id"]
    ask(run, "calendar_create", CREATE_INTERNAL, "toolu_a")
    ask(run, "email_send", {"draft_id": did}, "toolu_b")
    ask(run, "calendar_list", {"start": "2026-10-26", "end": "2026-10-26"}, None)
    lines = run.approvals()
    assert [line["kind"] for line in lines] == ["request", "response"] * 3
    req_a, resp_a, req_b, resp_b, req_c, resp_c = lines
    assert (req_a["call_id"], resp_a["call_id"]) == ("toolu_a", "toolu_a")
    assert req_a["tool"] == "calendar_create" and req_a["input"] == CREATE_INTERNAL
    assert resp_a["decision"] == "allow" and resp_a["by"] == "script" and resp_a["reason"]
    assert resp_b["decision"] == "deny" and resp_b["reason"] == DENY
    assert "raj.mehta@brightpath.example" in req_b["summary"] and "Thursday 2pm?" in req_b["summary"]
    assert req_c["call_id"] and req_c["call_id"] == resp_c["call_id"]  # generated when Claude Code gives none
    for line in lines:
        assert line["ts"]
    for req in (req_a, req_b, req_c):
        assert isinstance(req["summary"], str) and "\n" not in req["summary"]


def test_approvals_never_change_world_state(make_run) -> None:
    run = make_run(approval_mode="allow_all")
    before = run.snapshot()
    for tool in GATED:
        ask(run, tool, SAMPLE[tool])
    assert run.snapshot() == before
    assert not (run.dir / "calls.jsonl").exists()


def test_summaries_are_human_readable(make_run) -> None:
    run = make_run(approval_mode="allow_all")
    ask(run, "calendar_create", CREATE_INTERNAL)
    s = run.approvals()[0]["summary"]
    assert "Q4 forecast review" in s and "dan.okafor" in s
