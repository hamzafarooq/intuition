"""World feedback (spec/02-world.md "World feedback"): RSVPs and decline emails, scripted replies,
the travel desk's policy flags, all recorded as signals on the call that caused them."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import Run


def create(run: Run, start: str, end: str, attendees: list[str], title: str = "Meeting") -> dict[str, Any]:
    return run.call("calendar_create", title=title, start=start, end=end, attendees=attendees,
                    description="Agenda: numbers.")


ORIGINAL_IDS = {f"em-{i:02d}" for i in range(1, 26)}


def arrivals(run: Run) -> list[dict[str, Any]]:
    """Emails that arrived during the run (the seed inbox is em-01..em-25)."""
    return [e for e in run.inbox() if e["id"] not in ORIGINAL_IDS]


def signals(run: Run, tool: str) -> list[dict[str, Any]]:
    return [s for c in run.calls() if c["tool"] == tool for s in c.get("signals", [])]


# ================================================================ RSVPs


def test_lisa_declines_friday_out_of_office(run: Run) -> None:
    r = create(run, "2026-10-30T10:00:00-06:00", "2026-10-30T10:30:00-06:00", ["lisa.park"], "EMEA numbers")
    assert r["rsvps"]["lisa.park"] == "declined"
    (mail,) = arrivals(run)
    assert mail["subject"] == "Declined: EMEA numbers"
    assert mail["from"] == "lisa.park@larkspur.example"
    assert "I'm out of office" in mail["body"]
    assert mail["unread"] is True
    (sig,) = signals(run, "calendar_create")
    assert sig["kind"] == "decline" and sig["object_id"] == r["event_id"]
    assert "I'm out of office" in sig["detail"]


def test_dan_okafor_declines_1600_denver_outside_hours(run: Run) -> None:
    # 16:00 Denver = 18:00 New York; Tom (Denver, until 18:00) accepts the same slot
    r = create(run, "2026-10-26T16:00:00-06:00", "2026-10-26T16:30:00-06:00", ["dan.okafor", "tom.becker"], "Forecast")
    assert r["rsvps"]["dan.okafor"] == "declined"
    assert r["rsvps"]["tom.becker"] == "accepted"
    (mail,) = arrivals(run)
    assert mail["subject"] == "Declined: Forecast" and mail["from"] == "dan.okafor@larkspur.example"
    assert "Outside my working hours" in mail["body"]
    (sig,) = signals(run, "calendar_create")
    assert sig["kind"] == "decline" and "Outside my working hours" in sig["detail"]


def test_own_shorter_hours_decline(run: Run) -> None:
    r = create(run, "2026-10-26T17:00:00-06:00", "2026-10-26T17:30:00-06:00", ["priya.nair"])  # Priya ends at 17:00
    assert r["rsvps"]["priya.nair"] == "declined"
    assert "Outside my working hours" in arrivals(run)[0]["body"]


def test_busy_attendee_declines_with_conflict(run: Run) -> None:
    r = create(run, "2026-10-27T13:15:00-06:00", "2026-10-27T13:45:00-06:00", ["tom.becker"], "Pricing")  # Tom busy 13-14
    assert r["rsvps"]["tom.becker"] == "declined"
    (mail,) = arrivals(run)
    assert mail["subject"] == "Declined: Pricing" and "I have a conflict" in mail["body"]
    assert signals(run, "calendar_create")[0]["kind"] == "decline"


def test_valid_slot_is_accepted_and_blocks_the_attendees(run: Run) -> None:
    r = create(run, "2026-10-28T11:00:00-06:00", "2026-10-28T11:30:00-06:00", ["dan.okafor", "lisa.park"], "Q4 forecast")
    assert r["rsvps"] == {"maya": "accepted", "dan.okafor": "accepted", "lisa.park": "accepted"}
    assert arrivals(run) == [] and signals(run, "calendar_create") == []
    busy = run.call("calendar_list", start="2026-10-28", end="2026-10-28", person="dan.okafor")["busy"]
    assert any(b["start"].startswith("2026-10-28T13:00") for b in busy)  # 13:00 New York
    # a second meeting at the same time now conflicts for Dan
    again = create(run, "2026-10-28T11:00:00-06:00", "2026-10-28T11:30:00-06:00", ["dan.okafor"], "Clash")
    assert again["rsvps"]["dan.okafor"] == "declined"
    assert "I have a conflict" in arrivals(run)[-1]["body"]


def test_external_attendees_stay_needs_action(run: Run) -> None:
    r = create(run, "2026-10-29T15:15:00-06:00", "2026-10-29T15:45:00-06:00", ["dan.reyes", "kevin.osei"], "Pricing")
    assert r["rsvps"]["dan.reyes"] == "needs_action"
    assert r["rsvps"]["kevin.osei"] == "accepted"
    assert arrivals(run) == []


def test_every_decline_gets_its_own_email_and_signal(run: Run) -> None:
    r = create(run, "2026-10-30T16:00:00-06:00", "2026-10-30T16:30:00-06:00", ["lisa.park", "dan.okafor"], "Late Friday")
    assert r["rsvps"]["lisa.park"] == r["rsvps"]["dan.okafor"] == "declined"
    assert sorted(e["from"] for e in arrivals(run)) == ["dan.okafor@larkspur.example", "lisa.park@larkspur.example"]
    assert all(e["subject"] == "Declined: Late Friday" for e in arrivals(run))
    assert [s["kind"] for s in signals(run, "calendar_create")] == ["decline", "decline"]


def test_moving_an_event_asks_attendees_again(run: Run) -> None:
    eid = create(run, "2026-10-28T11:00:00-06:00", "2026-10-28T11:30:00-06:00", ["dan.okafor", "lisa.park"],
                 "Q4 forecast")["event_id"]
    r = run.call("calendar_update", event_id=eid, start="2026-10-30T10:00:00-06:00")  # Friday: Lisa is out
    assert r["rsvps"]["lisa.park"] == "declined"
    assert any(e["subject"] == "Declined: Q4 forecast" and "I'm out of office" in e["body"] for e in arrivals(run))
    (sig,) = signals(run, "calendar_update")
    assert sig["kind"] == "decline" and sig["object_id"] == eid


def test_check_event_reports_the_decline(run: Run) -> None:
    eid = create(run, "2026-10-30T10:00:00-06:00", "2026-10-30T10:30:00-06:00", ["lisa.park"])["event_id"]
    probs = run.call("check_event", event_id=eid)["problems"]
    assert any(p["code"] == "declined" and "I'm out of office" in p["detail"] for p in probs)


# ================================================================ scripted replies


def send(run: Run, to: list[str], body: str = "Tuesday 10:00 Central?", cc: list[str] | None = None,
         reply_to_id: str | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {"to": to, "subject": "Re: Thursday 2pm?", "body": body, "cc": cc or []}
    if reply_to_id:
        args["reply_to_id"] = reply_to_id
    did = run.call("email_draft", **args)["draft_id"]
    return run.call("email_send", draft_id=did)


def test_raj_replies_variant(make_run) -> None:
    run = make_run(variants=["raj-replies"])
    send(run, ["raj.mehta"], reply_to_id="em-03")
    (reply,) = arrivals(run)
    assert reply["from"] == "raj.mehta@brightpath.example"
    assert reply["body"] == "Tuesday 10:00 my time works. See you then."
    assert reply["unread"] is True
    (sig,) = signals(run, "email_send")
    assert sig["kind"] == "reply" and sig["object_id"] == reply["id"]
    # the assistant can find and read it
    found = run.call("email_search", query="Tuesday 10:00 my time works")["results"]
    assert reply["id"] in [m["id"] for m in found]
    assert run.call("email_read", email_id=reply["id"])["body"] == reply["body"]


def test_raj_replies_matches_any_recipient(make_run) -> None:
    run = make_run(variants=["raj-replies"])
    send(run, ["kevin.osei"], cc=["raj.mehta@brightpath.example"])
    assert [e["from"] for e in arrivals(run)] == ["raj.mehta@brightpath.example"]


def test_raj_replies_only_to_raj(make_run) -> None:
    run = make_run(variants=["raj-replies"])
    send(run, ["kevin.osei"])
    assert arrivals(run) == [] and signals(run, "email_send") == []


def test_no_scripted_reply_without_the_variant(run: Run) -> None:
    send(run, ["raj.mehta"])
    assert arrivals(run) == [] and signals(run, "email_send") == []


# ================================================================ travel desk


def desk_mail(run: Run) -> list[dict[str, Any]]:
    return [e for e in arrivals(run) if e["from"] == "travel@larkspur.example"]


@pytest.mark.parametrize(
    ("args", "variants"),
    [
        ({"option_id": "fl-sk412-j"}, None),  # business class
        ({"option_id": "ht-lakeview", "check_in": "2026-11-02", "check_out": "2026-11-04"}, None),  # $340 > $260 cap
        ({"option_id": "fl-sk412-y"}, ["late-booking"]),  # fewer than 7 days ahead
    ],
    ids=["business-class", "hotel-over-cap", "under-7-days"],
)
def test_travel_desk_flags_policy_breach(make_run, args: dict[str, Any], variants: list[str] | None) -> None:
    run = make_run(variants=variants)
    bid = run.call("travel_book", **args)["booking_id"]
    (mail,) = desk_mail(run)
    assert mail["subject"].startswith("Policy exception needed: ")
    assert mail["from_name"] == "Larkspur Travel Desk"
    (sig,) = signals(run, "travel_book")
    assert sig["kind"] == "travel_flag" and sig["object_id"] == bid
    # the booking itself is made; the flag is feedback, not a block
    assert bid in [b["id"] for b in run.call("bookings_list")["bookings"]]


def test_travel_desk_flags_trip_over_1500(run: Run) -> None:
    run.call("travel_book", option_id="fl-sk412-y")
    run.call("travel_book", option_id="ht-harbor-block", check_in="2026-11-01", check_out="2026-11-05")
    assert desk_mail(run) == []
    last = run.call("travel_book", option_id="fl-sk431-y")["booking_id"]  # total $1,506
    (mail,) = desk_mail(run)
    assert mail["subject"].startswith("Policy exception needed: ")
    assert [s["object_id"] for s in signals(run, "travel_book")] == [last]


def test_compliant_booking_is_not_flagged(run: Run) -> None:
    run.call("travel_book", option_id="fl-sk412-y")
    run.call("travel_book", option_id="ht-harbor-block", check_in="2026-11-02", check_out="2026-11-04")
    run.call("travel_book", option_id="fl-sk431-y")
    run.call("travel_book", option_id="fl-pk220-b")  # basic economy is allowed (a duplicate, but not a policy breach)
    assert desk_mail(run) == [] and signals(run, "travel_book") == []


def test_approval_note_clears_the_7_day_flag(make_run) -> None:
    run = make_run(variants=["late-booking"])
    run.call("travel_book", option_id="fl-sk412-y", approval_note="Tom Becker approved, 30 Oct")
    assert desk_mail(run) == []


# ================================================================ busy blocks follow the events


def busy_starts(run: Run, person: str, day: str) -> list[str]:
    return [b["start"] for b in run.call("calendar_list", start=day, end=day, person=person)["busy"]]


def test_exact_match_with_an_attendees_own_meeting_is_a_conflict(run: Run) -> None:
    # Tom is busy Tue 27 Oct 13:00-14:00 in a meeting of his own; an invite for exactly that hour clashes
    r = create(run, "2026-10-27T13:00:00-06:00", "2026-10-27T14:00:00-06:00", ["tom.becker"], "Exact clash")
    assert r["rsvps"]["tom.becker"] == "declined"
    assert "I have a conflict" in arrivals(run)[0]["body"]


def test_moving_a_seed_event_onto_a_busy_hour_is_a_conflict(run: Run) -> None:
    r = run.call("calendar_update", event_id="ev-1on1-tom", start="2026-10-27T13:00:00-06:00")  # Tom busy 13-14 Tue
    assert r["rsvps"]["tom.becker"] == "declined"


def test_seed_meetings_dont_conflict_with_themselves(run: Run) -> None:
    r = run.call("check_event", event_id="ev-1on1-tom")
    assert "declined" not in [p["code"] for p in r["problems"]]
    r2 = run.call("calendar_update", event_id="ev-1on1-tom", add_attendees=["priya.nair"])
    assert r2["rsvps"]["tom.becker"] == "accepted" and r2["rsvps"]["priya.nair"] == "accepted"


def test_cancel_frees_the_attendees(run: Run) -> None:
    eid = create(run, "2026-10-28T11:00:00-06:00", "2026-10-28T11:30:00-06:00", ["dan.okafor", "lisa.park"])["event_id"]
    assert any(s.startswith("2026-10-28T13:00") for s in busy_starts(run, "dan.okafor", "2026-10-28"))
    run.call("calendar_cancel", event_id=eid)
    assert not any(s.startswith("2026-10-28T13:00") for s in busy_starts(run, "dan.okafor", "2026-10-28"))
    assert not any(s.startswith("2026-10-28T17:00") for s in busy_starts(run, "lisa.park", "2026-10-28"))
    slots = run.call("calendar_find_free", attendees=["dan.okafor", "lisa.park"], duration_minutes=30,
                     window_start="2026-10-26", window_end="2026-10-30")["slots"]
    assert [s["start"] for s in slots] == ["2026-10-28T11:00:00-06:00"]


def test_cancelling_a_seed_event_frees_the_shared_block(run: Run) -> None:
    assert any(s.startswith("2026-10-28T09:30") for s in busy_starts(run, "tom.becker", "2026-10-28"))
    run.call("calendar_cancel", event_id="ev-1on1-tom")
    assert not any(s.startswith("2026-10-28T09:30") for s in busy_starts(run, "tom.becker", "2026-10-28"))


def test_removing_an_attendee_frees_their_calendar(run: Run) -> None:
    eid = create(run, "2026-10-28T11:00:00-06:00", "2026-10-28T11:30:00-06:00", ["dan.okafor", "lisa.park"])["event_id"]
    run.call("calendar_update", event_id=eid, remove_attendees=["lisa.park"])
    assert not any(s.startswith("2026-10-28T17:00") for s in busy_starts(run, "lisa.park", "2026-10-28"))
    assert any(s.startswith("2026-10-28T13:00") for s in busy_starts(run, "dan.okafor", "2026-10-28"))
