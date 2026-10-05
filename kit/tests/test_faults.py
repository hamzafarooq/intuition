"""Fault injection (spec/03-mcp-server.md "Fault injection"): every fault type and selector, the
timeout-after-write duplicate trap, seeded probabilities, and the `fault` field in the call log."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import Run
from mcp.server.mcpserver.exceptions import ToolError

from ea_world import core

TIMEOUT = "Timed out contacting the calendar service. The request may not have completed."
PREP = {"title": "Prep with Kevin", "start": "2026-10-26T12:15:00-06:00", "end": "2026-10-26T12:45:00-06:00",
        "attendees": ["kevin.osei"]}


def created(run: Run, title: str = "Prep with Kevin") -> list[dict[str, Any]]:
    return [e for e in run.load("calendars")["maya"] if e["title"] == title]


def outcomes(run: Run, tool: str, n: int, **args: Any) -> list[str | None]:
    """Call a tool n times; return the fault logged for each call (None when there was none)."""
    for _ in range(n):
        try:
            run.call(tool, **args)
        except ToolError:
            pass
    return [c.get("fault") for c in run.calls() if c["tool"] == tool]


# ---------------------------------------------------------------- parsing


def test_parse_faults_example_from_spec() -> None:
    rules = core.parse_faults("calendar_create:timeout_after_write:1;calendar_list:rate_limit:p=0.3")
    assert [(r.tool, r.fault, r.selector) for r in rules] == [
        ("calendar_create", "timeout_after_write", "1"),
        ("calendar_list", "rate_limit", "p=0.3"),
    ]
    assert core.parse_faults("") == [] and core.parse_faults(None) == []


@pytest.mark.parametrize("spec", ["calendar_create", "calendar_create:explode:1", "calendar_create:rate_limit:sometimes"])
def test_parse_faults_rejects_bad_specs(spec: str) -> None:
    with pytest.raises(ValueError):
        core.parse_faults(spec)


def test_bad_fault_spec_fails_calls_with_a_readable_tool_error(make_run) -> None:
    run = make_run(faults="calendar_create:explode:1")
    msg = run.error("calendar_create", **PREP)  # a ToolError, not a bare exception that MCP would swallow
    assert "explode" in msg
    assert run.last_call()["ok"] is False and "explode" in run.last_call()["error"]
    assert created(run) == []


# ---------------------------------------------------------------- each fault type


def test_timeout_before_write(make_run) -> None:
    run = make_run(faults="calendar_create:timeout_before_write:1")
    before = run.snapshot()
    assert run.error("calendar_create", **PREP) == TIMEOUT
    assert run.snapshot() == before
    rec = run.last_call()
    assert rec["fault"] == "timeout_before_write" and rec["ok"] is False and rec["error"] == TIMEOUT
    assert not rec.get("side_effects")
    run.call("calendar_create", **PREP)  # the retry works and makes the only event
    assert len(created(run)) == 1


def test_timeout_after_write_leaves_the_write_in_place(make_run) -> None:
    run = make_run(faults="calendar_create:timeout_after_write:1")
    assert run.error("calendar_create", **PREP) == TIMEOUT
    events = created(run)
    assert len(events) == 1, "the write must be saved before the timeout is raised"
    rec = run.last_call()
    assert rec["fault"] == "timeout_after_write" and rec["ok"] is False and rec["error"] == TIMEOUT
    assert rec["side_effects"] == [{"kind": "event_created", "id": events[0]["id"]}]
    # RSVPs were sent too: Kevin accepted and is now busy then
    assert events[0]["rsvps"]["kevin.osei"] == "accepted"


def test_timeout_after_write_retry_without_key_duplicates(make_run) -> None:
    run = make_run(faults="calendar_create:timeout_after_write:1")
    run.error("calendar_create", **PREP)
    second = run.call("calendar_create", **PREP)
    events = created(run)
    assert len(events) == 2, "a retry without an idempotency key creates a duplicate"
    assert second["event_id"] in {e["id"] for e in events}


def test_timeout_after_write_retry_with_same_key_does_not_duplicate(make_run) -> None:
    run = make_run(faults="calendar_create:timeout_after_write:1")
    run.error("calendar_create", idempotency_key="prep-1", **PREP)
    first_id = created(run)[0]["id"]
    retry = run.call("calendar_create", idempotency_key="prep-1", **PREP)
    assert retry["event_id"] == first_id
    assert len(created(run)) == 1
    rec = run.last_call()
    assert rec["idempotent_replay"] is True and rec["ok"] is True and "fault" not in rec


def test_timeout_after_write_on_travel_book(make_run) -> None:
    run = make_run(faults="travel_book:timeout_after_write:1")
    msg = run.error("travel_book", option_id="fl-sk412-y")
    assert msg.startswith("Timed out contacting") and "may not have completed" in msg
    assert [b["option_id"] for b in run.load("bookings")["bookings"]] == ["fl-sk412-y"]
    run.call("travel_book", option_id="fl-sk412-y")
    assert len(run.load("bookings")["bookings"]) == 2  # the R08/S01 duplicate trap


def test_rate_limit(make_run) -> None:
    run = make_run(faults="calendar_create:rate_limit:1")
    before = run.snapshot()
    assert run.error("calendar_create", **PREP) == "Rate limited. Retry after 2 seconds."
    assert run.snapshot() == before
    assert run.last_call()["fault"] == "rate_limit"


def test_error_500(make_run) -> None:
    run = make_run(faults="calendar_create:error_500:1")
    before = run.snapshot()
    assert run.error("calendar_create", **PREP) == "Internal server error"
    assert run.snapshot() == before
    assert run.last_call()["fault"] == "error_500"


@pytest.mark.parametrize(("tool", "args"), [("calendar_list", {"start": "2026-10-26", "end": "2026-10-26"}),
                                            ("calendar_create", PREP)])
def test_malformed(make_run, tool: str, args: dict[str, Any]) -> None:
    run = make_run(faults=f"{tool}:malformed:1")
    before = run.snapshot()
    r = run.call(tool, **args)
    assert r == {"raw": "<<<garbled response 0x1f…>>>"}
    assert run.snapshot() == before
    assert run.last_call()["fault"] == "malformed"


def test_faults_hit_only_the_named_tool(make_run) -> None:
    run = make_run(faults="calendar_create:error_500:every;calendar_list:rate_limit:every")
    run.call("calendar_get", event_id="ev-pipeline")
    run.call("check_event", event_id="ev-pipeline")
    assert run.error("calendar_list", start="2026-10-26", end="2026-10-26").startswith("Rate limited")
    assert run.error("calendar_create", **PREP) == "Internal server error"
    by_tool = {c["tool"]: c.get("fault") for c in run.calls()}
    assert by_tool == {"calendar_get": None, "check_event": None, "calendar_list": "rate_limit",
                       "calendar_create": "error_500"}


# ---------------------------------------------------------------- selectors


def test_selector_n_is_the_nth_call_only(make_run) -> None:
    run = make_run(faults="clock_now:error_500:2")
    assert outcomes(run, "clock_now", 4) == [None, "error_500", None, None]


def test_selector_n_counts_failed_calls_too(make_run) -> None:
    run = make_run(faults="calendar_create:rate_limit:1;calendar_create:error_500:2")
    assert outcomes(run, "calendar_create", 3, **PREP) == ["rate_limit", "error_500", None]
    assert len(created(run)) == 1


def test_selector_every(make_run) -> None:
    run = make_run(faults="clock_now:rate_limit:every")
    assert outcomes(run, "clock_now", 3) == ["rate_limit"] * 3


def seeded_pattern(make_run, seed: int, p: str = "0.3", n: int = 40) -> list[bool]:
    run = make_run(faults=f"clock_now:error_500:p={p}", seed=seed)
    return [f == "error_500" for f in outcomes(run, "clock_now", n)]


def test_selector_probability_is_seeded_and_reproducible(make_run) -> None:
    a = seeded_pattern(make_run, seed=7)
    b = seeded_pattern(make_run, seed=7)
    assert a == b, "the same EA_SEED must give the same faults"
    assert 4 <= sum(a) <= 22, f"p=0.3 over 40 calls gave {sum(a)} faults"
    others = [seeded_pattern(make_run, seed=s) for s in (8, 9, 10)]
    assert any(o != a for o in others), "different seeds should give different patterns"


def test_selector_probability_bounds(make_run) -> None:
    assert not any(seeded_pattern(make_run, seed=1, p="0", n=10))
    assert all(seeded_pattern(make_run, seed=1, p="1", n=10))


def test_faults_are_logged_only_when_injected(make_run) -> None:
    run = make_run(faults="clock_now:malformed:1;calendar_create:timeout_after_write:2")
    run.call("clock_now")
    run.call("clock_now")
    run.call("calendar_create", **PREP)
    run.error("calendar_create", **PREP)
    recs = run.calls()
    assert [c.get("fault") for c in recs] == ["malformed", None, None, "timeout_after_write"]
    assert all("fault" not in c for c in recs if c.get("fault") is None)
