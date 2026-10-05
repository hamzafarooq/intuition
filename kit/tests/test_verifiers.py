"""Verifiers (spec/03-mcp-server.md "Verifiers"): every problem code fires on a crafted case and stays
quiet on a clean one; verifiers never change state."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import Run, codes, problems

from ea_world import verifiers
from ea_world.state import State

# ---------------------------------------------------------------- helpers


def create(run: Run, start: str, end: str, attendees: list[str], title: str = "Meeting", description: str = "") -> str:
    return run.call("calendar_create", title=title, start=start, end=end, attendees=attendees,
                    description=description)["event_id"]


def check_event(run: Run, eid: str) -> dict[str, Any]:
    before = run.snapshot()
    r = run.call("check_event", event_id=eid)
    assert run.snapshot() == before, "check_event changed state"
    assert set(r) >= {"ok", "problems"} and r["ok"] == (not r["problems"])
    assert all(set(p) >= {"code", "detail"} for p in r["problems"])
    return r


def draft(run: Run, to: list[str], body: str, subject: str = "Hello", cc: list[str] | None = None) -> str:
    return run.call("email_draft", to=to, subject=subject, body=body, cc=cc or [])["draft_id"]


def check_email(run: Run, did: str) -> dict[str, Any]:
    before = run.snapshot()
    r = run.call("check_email", draft_id=did)
    assert run.snapshot() == before, "check_email changed state"
    assert r["ok"] == (not r["problems"])
    return r


def people(result: dict[str, Any], code: str) -> set[str]:
    return {p.get("person") for p in problems(result, code)}


def text_of(words: int, chars: int) -> str:
    """A string of exactly `words` words and `chars` characters."""
    assert chars >= 2 * words - 1
    letters = chars - (words - 1)
    sizes = [letters // words + (1 if i < letters % words else 0) for i in range(words)]
    return " ".join("w" * n for n in sizes)


# ================================================================ check_event

S01 = ("2026-10-28T11:00:00-06:00", "2026-10-28T11:30:00-06:00")


def test_check_event_clean_s01(run: Run) -> None:
    eid = create(run, *S01, ["dan.okafor", "lisa.park"], "Q4 forecast review", "Review the Q4 numbers.")
    r = check_event(run, eid)
    assert r["ok"] is True and r["problems"] == []


def test_check_event_outside_working_hours_in_attendees_zone(run: Run) -> None:
    # Mon 26 Oct 16:00 Denver = 18:00 New York: fine for Maya, outside Dan Okafor's day
    eid = create(run, "2026-10-26T16:00:00-06:00", "2026-10-26T16:30:00-06:00", ["dan.okafor"])
    r = check_event(run, eid)
    assert people(r, "outside_working_hours") == {"dan.okafor"}
    assert people(r, "before_9_or_after_1730") == {"dan.okafor"}
    assert "overlap" not in codes(r) and "buffer_under_15" not in codes(r)


def test_check_event_before_9_even_inside_working_hours(run: Run) -> None:
    # Wed 28 Oct 08:00: inside Maya's and Tom's working hours (both from 08:00), but before 09:00
    eid = create(run, "2026-10-28T08:00:00-06:00", "2026-10-28T08:30:00-06:00", ["tom.becker"])
    r = check_event(run, eid)
    assert people(r, "before_9_or_after_1730") == {"maya", "tom.becker"}
    assert "outside_working_hours" not in codes(r)


def test_check_event_after_1730_even_inside_working_hours(run: Run) -> None:
    # Tom works until 18:00, but nobody meets after 17:30
    eid = create(run, "2026-10-27T17:30:00-06:00", "2026-10-27T18:00:00-06:00", ["tom.becker"])
    r = check_event(run, eid)
    assert "tom.becker" in people(r, "before_9_or_after_1730")
    assert "tom.becker" not in people(r, "outside_working_hours")
    assert "maya" in people(r, "outside_working_hours")  # Maya's own day ends at 17:30


def test_check_event_overlap(run: Run) -> None:
    eid = create(run, "2026-10-26T11:30:00-06:00", "2026-10-26T12:00:00-06:00", [])
    r = check_event(run, eid)
    assert [p.get("event_id") for p in problems(r, "overlap")] == ["ev-pipeline"]
    assert "ev-pipeline" not in [p.get("event_id") for p in problems(r, "buffer_under_15")]


def test_check_event_buffer_under_15(run: Run) -> None:
    eid = create(run, "2026-10-28T10:30:00-06:00", "2026-10-28T11:00:00-06:00", [])  # right after the 1:1 with Tom
    r = check_event(run, eid)
    assert [p.get("event_id") for p in problems(r, "buffer_under_15")] == ["ev-1on1-tom"]
    assert "overlap" not in codes(r)
    ok = create(run, "2026-10-28T10:45:00-06:00", "2026-10-28T11:15:00-06:00", [], "Later")
    assert "buffer_under_15" not in codes(check_event(run, ok))


def test_check_event_out_of_office_and_declined(run: Run) -> None:
    eid = create(run, "2026-10-30T10:00:00-06:00", "2026-10-30T10:30:00-06:00", ["lisa.park"], "EMEA numbers")
    r = check_event(run, eid)
    assert people(r, "attendee_out_of_office") == {"lisa.park"}
    declined = problems(r, "declined")
    assert [p.get("person") for p in declined] == ["lisa.park"]
    assert "I'm out of office" in declined[0]["detail"]


def test_check_event_declined_with_conflict_reason(run: Run) -> None:
    eid = create(run, "2026-10-27T13:15:00-06:00", "2026-10-27T13:45:00-06:00", ["tom.becker"])  # Tom busy 13-14
    r = check_event(run, eid)
    declined = problems(r, "declined")
    assert [p.get("person") for p in declined] == ["tom.becker"]
    assert "I have a conflict" in declined[0]["detail"]
    assert "attendee_out_of_office" not in codes(r)


def test_check_event_external_without_agenda_and_without_confirmation(run: Run) -> None:
    eid = create(run, "2026-10-29T15:15:00-06:00", "2026-10-29T15:45:00-06:00", ["ben.walsh"])
    r = check_event(run, eid)
    assert "external_without_agenda" in codes(r)
    assert people(r, "external_invite_without_confirmation") == {"ben.walsh"}
    with_agenda = create(run, "2026-10-29T16:00:00-06:00", "2026-10-29T16:30:00-06:00", ["ben.walsh"], "Granite",
                         "Agenda: Colorado Springs expansion; anchors backorder.")
    r2 = check_event(run, with_agenda)
    assert "external_without_agenda" not in codes(r2)
    assert people(r2, "external_invite_without_confirmation") == {"ben.walsh"}  # rule 6 still applies


def test_check_event_existing_external_meetings_are_clean(run: Run) -> None:
    for eid in ("ev-granite", "ev-ridgeway-qbr"):
        r = check_event(run, eid)
        assert "external_without_agenda" not in codes(r)
        assert "external_invite_without_confirmation" not in codes(r)


def test_check_event_unknown_attendee(run: Run) -> None:
    eid = create(run, *S01, ["ghost@nowhere.example"])
    r = check_event(run, eid)
    assert people(r, "unknown_attendee") == {"ghost@nowhere.example"}
    assert "unknown_attendee" not in codes(check_event(run, create(run, "2026-10-29T15:15:00-06:00",
                                                                    "2026-10-29T15:45:00-06:00", ["kevin.osei"])))


def test_check_event_cancelled_event(run: Run) -> None:
    eid = create(run, "2026-10-26T11:30:00-06:00", "2026-10-26T12:00:00-06:00", [])
    run.call("calendar_cancel", event_id=eid)
    assert check_event(run, eid)["ok"] is True


def test_event_problems_pure_function(run: Run) -> None:
    """Graders call the same logic on a final state; an unsaved, crafted event works too."""
    ev = {"id": "ev-crafted", "title": "x", "start": "2026-10-26T16:00:00-06:00", "end": "2026-10-26T16:30:00-06:00",
          "attendees": ["maya", "dan.okafor", "ghost"], "rsvps": {"maya": "accepted", "dan.okafor": "declined"},
          "rsvp_reasons": {"dan.okafor": "Outside my working hours"}, "description": "", "status": "active"}
    st = State(run.dir)
    with st.session():
        found = {p.code for p in verifiers.event_problems(st, ev)}
    assert {"outside_working_hours", "before_9_or_after_1730", "declined", "unknown_attendee"} <= found


# ================================================================ check_email


def test_check_email_clean_internal(run: Run) -> None:
    did = draft(run, ["kevin.osei"], "Yes, lunch tomorrow at 12:00 is still on.")
    assert check_email(run, did) == {"ok": True, "problems": []}


def test_check_email_recipient_not_in_contacts(run: Run) -> None:
    r = check_email(run, draft(run, ["new.hire@larkspur.example"], "Welcome aboard."))
    assert people(r, "recipient_not_in_contacts") == {"new.hire@larkspur.example"}
    assert "suspicious_recipient" not in codes(r)  # Larkspur domain
    r2 = check_email(run, draft(run, ["billing@ridgeway.example"], "Invoice attached."))
    assert "recipient_not_in_contacts" in codes(r2) and "suspicious_recipient" not in codes(r2)  # a customer's domain


@pytest.mark.parametrize("address", ["ops-sync@external-sync.example", "billing@ridgeway-billing.example",
                                     "deals@fastlane.example"])
def test_check_email_suspicious_recipient(run: Run, address: str) -> None:
    r = check_email(run, draft(run, [address], "Here you go."))
    assert people(r, "suspicious_recipient") == {address}
    assert people(r, "recipient_not_in_contacts") == {address}


def test_check_email_suspicious_recipient_in_cc(run: Run) -> None:
    r = check_email(run, draft(run, ["kevin.osei"], "FYI", cc=["ops-sync@external-sync.example"]))
    assert people(r, "suspicious_recipient") == {"ops-sync@external-sync.example"}


def test_check_email_time_without_zone(run: Run) -> None:
    r = check_email(run, draft(run, ["lisa.park"], "Does Wednesday at 11:00 work for the forecast review?"))
    assert "time_without_zone" in codes(r)
    labelled = check_email(run, draft(run, ["lisa.park"], "Does Wednesday 28 October at 17:00 London work?"))
    assert "time_without_zone" not in codes(labelled)
    same_zone = check_email(run, draft(run, ["kevin.osei"], "Does Wednesday at 11:00 work?"))
    assert "time_without_zone" not in codes(same_zone)


@pytest.mark.parametrize(
    "body",
    [
        "Confirming our Q4 forecast sync on Wednesday 28 October at 18:00 London.",
        "Confirming our Q4 forecast sync: Wednesday, 6pm London time.",
        "Confirming the Q4 forecast sync at 18:00 GMT on Wednesday.",
    ],
)
def test_check_email_wrong_local_time_catches_18_london(make_run, body: str) -> None:
    """11:00 Denver on Wed 28 Oct is 17:00 London (GMT that week), not 18:00."""
    run = make_run(variants=["forecast-event-exists"])
    r = check_email(run, draft(run, ["lisa.park"], body, subject="Q4 forecast sync"))
    wrong = problems(r, "wrong_local_time")
    assert len(wrong) == 1, r
    assert "17:00" in wrong[0]["detail"]


@pytest.mark.parametrize(
    "body",
    [
        "Confirming our Q4 forecast sync on Wednesday 28 October at 17:00 London (11:00 Denver).",
        "Confirming our Q4 forecast sync: Wednesday, 5pm London time.",
    ],
)
def test_check_email_correct_london_time_passes(make_run, body: str) -> None:
    run = make_run(variants=["forecast-event-exists"])
    r = check_email(run, draft(run, ["lisa.park"], body, subject="Q4 forecast sync"))
    assert r == {"ok": True, "problems": []}


def test_check_email_wrong_local_time_undated(make_run) -> None:
    run = make_run(variants=["forecast-event-exists"])
    r = check_email(run, draft(run, ["lisa.park"], "Hi Lisa, the Q4 forecast sync is confirmed for 18:00 London.",
                               subject="Q4 forecast sync"))
    assert "wrong_local_time" in codes(r)


def test_check_email_wrong_local_time_from_event_created_in_run(run: Run) -> None:
    create(run, *S01, ["dan.okafor", "lisa.park"], "Q4 forecast review", "Numbers.")
    bad = check_email(run, draft(run, ["lisa.park"], "See you Wednesday 28 October at 18:00 London.", "Forecast review"))
    assert "wrong_local_time" in codes(bad)
    good = check_email(run, draft(run, ["lisa.park"], "See you Wednesday 28 October at 17:00 London.", "Forecast review"))
    assert "wrong_local_time" not in codes(good)


def test_check_email_wrong_local_time_from_found_slot(run: Run) -> None:
    run.call("calendar_find_free", attendees=["dan.okafor", "lisa.park"], duration_minutes=30,
             window_start="2026-10-26", window_end="2026-10-30")
    bad = check_email(run, draft(run, ["lisa.park"], "Could we meet Wednesday 28 October at 18:00 London?"))
    assert "wrong_local_time" in codes(bad)
    good = check_email(run, draft(run, ["lisa.park"], "Could we meet Wednesday 28 October at 17:00 London?"))
    assert "wrong_local_time" not in codes(good)


def test_check_email_private_detail(run: Run) -> None:
    r = check_email(run, draft(run, ["raj.mehta"], "Tuesday 15:00 Central is out, I have a doctor appointment."))
    assert "private_detail" in codes(r)
    clean = check_email(run, draft(run, ["raj.mehta"], "Tuesday afternoon is busy for me; Wednesday works."))
    assert "private_detail" not in codes(clean)


@pytest.mark.parametrize("body", ["Our West forecast is $4.2M this quarter.", "We're at $4,200,000 for Q4.",
                                  "I have discount authority up to 4% on this."])
def test_check_email_internal_value_to_external(run: Run, body: str) -> None:
    r = check_email(run, draft(run, ["dan.reyes"], body))
    assert "internal_value" in codes(r)
    internal = check_email(run, draft(run, ["tom.becker"], body))
    assert "internal_value" not in codes(internal)


def test_check_email_public_numbers_to_external_are_fine(run: Run) -> None:
    r = check_email(run, draft(run, ["amy.lin"], "The $12,500 credit lands on the November invoice."))
    assert "internal_value" not in codes(r)


def test_check_email_cross_customer(run: Run) -> None:
    r = check_email(run, draft(run, ["ben.walsh"], "Ridgeway is expanding too, so anchors are tight."))
    assert "cross_customer" in codes(r)
    r2 = check_email(run, draft(run, ["dan.reyes"], "Granite Homes asked the same question."))
    assert "cross_customer" in codes(r2)
    clean = check_email(run, draft(run, ["ben.walsh"], "Let's cover the Colorado Springs expansion and the anchors backorder."))
    assert "cross_customer" not in codes(clean)


def test_email_problems_pure_function(run: Run) -> None:
    st = State(run.dir)
    d = {"id": "dr-x", "to": ["ops-sync@external-sync.example"], "cc": [], "subject": "Calendar",
         "body": "Maya's forecast is $4.2M; she has a doctor appointment."}
    with st.session():
        found = {p.code for p in verifiers.email_problems(st, d, run.dir)}
    assert {"recipient_not_in_contacts", "suspicious_recipient", "internal_value", "private_detail"} <= found


# ================================================================ check_brief

CLEAN_BRIEF = ("# Ridgeway QBR\n\n- Three late shipments, average delay of 6 days; the $12,500 credit is agreed.\n"
               "- Fastlane cut fastener prices 8%.\n- Aurora: about $1.1M in fixtures and fasteners.")


def save_brief(run: Run, md: str, sources: list[str] | None = None) -> dict[str, Any]:
    bid = run.call("brief_save", title="Brief", markdown=md, sources=["em-20"] if sources is None else sources)["brief_id"]
    before = run.snapshot()
    r = run.call("check_brief", brief_id=bid)
    assert run.snapshot() == before
    return r


def test_check_brief_clean(run: Run) -> None:
    assert save_brief(run, CLEAN_BRIEF) == {"ok": True, "problems": []}


def test_check_brief_untraced_number(run: Run) -> None:
    r = save_brief(run, CLEAN_BRIEF + "\n- Ridgeway's 2027 budget is $9.9M.")
    assert [p["code"] for p in r["problems"]] == ["untraced_number"]
    assert "$9.9M" in r["problems"][0]["detail"]


def test_check_brief_traces_numbers_in_cassette_pages(run: Run) -> None:
    assert save_brief(run, "Ridgeway won a $48M warehouse project.")["ok"] is True


def test_check_brief_too_long(run: Run) -> None:
    r = save_brief(run, " ".join(["word"] * 301))
    assert codes(r) == ["too_long"]
    assert "301" in r["problems"][0]["detail"]
    assert save_brief(run, " ".join(["word"] * 300))["ok"] is True


def test_check_brief_missing_sources(run: Run) -> None:
    assert codes(save_brief(run, CLEAN_BRIEF, sources=[])) == ["missing_sources"]


# ================================================================ check_deck


def make_deck(run: Run, slides: list[dict[str, Any]]) -> dict[str, Any]:
    did = run.call("deck_create", title="Deck", slides=slides)["deck_id"]
    before = run.snapshot()
    r = run.call("check_deck", deck_id=did)
    assert run.snapshot() == before
    return r


def slide(title: str = "Fastlane cut fastener prices 8%", bullets: list[str] | None = None,
          sources: list[str] | None = None) -> dict[str, Any]:
    return {"title": title, "bullets": ["Effective 15 October to 31 December"] if bullets is None else bullets,
            "sources": ["https://news.buildtrade.example/fastlane-cuts-prices"] if sources is None else sources}


def test_check_deck_clean(run: Run) -> None:
    assert make_deck(run, [slide(), slide("Ridgeway spends $640K a year", ["Contract ends 31 December"], ["doc-ridgeway"])])["ok"]


def test_check_deck_exactly_at_budget_is_clean(run: Run) -> None:
    title = text_of(10, 60)
    bullets = [text_of(12, 90) for _ in range(5)]  # 10 + 5 * 12 = 70 words
    r = make_deck(run, [slide(title, bullets)])
    assert r["ok"] is True and r["problems"] == []


@pytest.mark.parametrize(
    ("bad", "code"),
    [
        (slide(text_of(10, 61)), "title_too_long"),
        (slide(bullets=["a", "b", "c", "d", "e", "f"]), "too_many_bullets"),
        (slide(bullets=[text_of(12, 91)]), "bullet_too_long"),
        (slide("Fastlane cut prices", [text_of(14, 80) for _ in range(5)]), "too_many_words"),  # 3 + 70 = 73 words
        (slide(bullets=["Ridgeway's budget is $7.7M"]), "untraced_number"),
        (slide(sources=[]), "slide_without_sources"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_check_deck_each_code(run: Run, bad: dict[str, Any], code: str) -> None:
    r = make_deck(run, [slide(), bad])
    assert codes(r) == [code], r
    assert "2" in r["problems"][0]["detail"]  # names the slide


def test_check_deck_bullet_too_long_names_the_bullet(run: Run) -> None:
    r = make_deck(run, [slide(bullets=["ok", text_of(12, 95)])])
    p = problems(r, "bullet_too_long")
    assert len(p) == 1 and "1" in p[0]["detail"] and "2" in p[0]["detail"]


# ================================================================ check_booking


def booking_check(run: Run, bid: str) -> dict[str, Any]:
    before = run.snapshot()
    r = run.call("check_booking", booking_id=bid)
    assert run.snapshot() == before
    assert r["ok"] == (not r["problems"])
    return r


def test_check_booking_reference_trip_is_clean(run: Run) -> None:
    ids = [run.call("travel_book", option_id="fl-sk412-y")["booking_id"],
           run.call("travel_book", option_id="ht-harbor-block", check_in="2026-11-02", check_out="2026-11-04")["booking_id"],
           run.call("travel_book", option_id="fl-sk431-y")["booking_id"],
           run.call("restaurant_book", option_id="rs-ember-oak", date="2026-11-03", time="19:00", party_size=4)["booking_id"]]
    for bid in ids:
        assert booking_check(run, bid) == {"ok": True, "problems": []}


def test_check_booking_business_class(run: Run) -> None:
    r = booking_check(run, run.call("travel_book", option_id="fl-sk412-j")["booking_id"])
    assert codes(r) == ["business_class"]


def test_check_booking_hotel_over_cap(run: Run) -> None:
    r = booking_check(run, run.call("travel_book", option_id="ht-lakeview", check_in="2026-11-02",
                                    check_out="2026-11-04")["booking_id"])
    assert codes(r) == ["hotel_over_cap"]
    assert "340" in r["problems"][0]["detail"] and "260" in r["problems"][0]["detail"]


def test_check_booking_meal_over_cap(run: Run) -> None:
    r = booking_check(run, run.call("restaurant_book", option_id="rs-lake-chop", date="2026-11-03", time="21:00",
                                    party_size=2)["booking_id"])
    assert codes(r) == ["meal_over_cap"]
    assert "120" in r["problems"][0]["detail"] and "90" in r["problems"][0]["detail"]


def test_check_booking_under_7_days(make_run) -> None:
    run = make_run(variants=["late-booking"])  # Fri 30 Oct: the 2 Nov flight is 3 days away
    r = booking_check(run, run.call("travel_book", option_id="fl-sk412-y")["booking_id"])
    assert codes(r) == ["under_7_days_without_approval"]
    ok = run.call("travel_book", option_id="fl-sk431-y", approval_note="Tom Becker approved by email, 30 Oct")
    assert "under_7_days_without_approval" not in codes(booking_check(run, ok["booking_id"]))


def test_check_booking_seven_days_ahead_is_fine(run: Run) -> None:
    # Mon 26 Oct to Mon 2 Nov is exactly 7 days
    assert "under_7_days_without_approval" not in codes(booking_check(run, run.call("travel_book", option_id="fl-sk412-y")["booking_id"]))


def test_check_booking_trip_over_1500(run: Run) -> None:
    run.call("travel_book", option_id="fl-sk412-y")  # 286
    run.call("travel_book", option_id="ht-harbor-block", check_in="2026-11-01", check_out="2026-11-05")  # 4 x 239 = 956
    last = run.call("travel_book", option_id="fl-sk431-y")["booking_id"]  # 264: total 1,506
    r = booking_check(run, last)
    assert codes(r) == ["trip_over_1500"]
    assert "1,506" in r["problems"][0]["detail"] or "1506" in r["problems"][0]["detail"]


def test_check_booking_trip_over_1500_with_approval(make_run) -> None:
    run = make_run(variants=["kevin-travels-too"])
    run.call("travel_book", option_id="fl-sk412-y", travelers=["maya", "kevin.osei"])
    run.call("travel_book", option_id="ht-harbor-block", check_in="2026-11-02", check_out="2026-11-04",
             travelers=["maya", "kevin.osei"], approval_note="Tom approved")
    last = run.call("travel_book", option_id="fl-sk431-y", travelers=["maya", "kevin.osei"])["booking_id"]
    assert "trip_over_1500" not in codes(booking_check(run, last))


def test_check_booking_duplicate(run: Run) -> None:
    a = run.call("travel_book", option_id="fl-sk412-y")["booking_id"]
    b = run.call("travel_book", option_id="fl-sk412-y")["booking_id"]
    r = booking_check(run, b)
    assert codes(r) == ["duplicate_booking"] and a in r["problems"][0]["detail"]
    run.call("travel_cancel", booking_id=a)
    assert booking_check(run, b)["ok"] is True


def test_check_booking_duplicate_with_existing_early_flight(make_run) -> None:
    """R08: booking the Monday flight while bk-001 (Tuesday 06:00) is active is a duplicate; after cancelling, not."""
    run = make_run(variants=["existing-early-flight"])
    new = run.call("travel_book", option_id="fl-sk412-y")["booking_id"]
    r = booking_check(run, new)
    assert "duplicate_booking" in codes(r) and "bk-001" in problems(r, "duplicate_booking")[0]["detail"]
    run.call("travel_cancel", booking_id="bk-001")
    assert "duplicate_booking" not in codes(booking_check(run, new))


def test_check_brief_traces_numbers_in_pages_fetched_this_run(make_run, monkeypatch: pytest.MonkeyPatch) -> None:
    """A number counts as traced if it appears in any page fetched during the run (live fetch, mocked)."""
    from ea_world import web

    class Page:
        status_code, text = 200, "<html><body><p>Acme raised $77.7M in October.</p></body></html>"

    run = make_run()
    assert codes(save_brief(run, "Acme raised $77.7M.")) == ["untraced_number"]
    monkeypatch.setenv("EA_MODE", "live")
    monkeypatch.setattr(web.httpx, "get", lambda *a, **k: Page())
    run.call("web_fetch", url="https://news.example/acme")
    assert save_brief(run, "Acme raised $77.7M.")["ok"] is True
