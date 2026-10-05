"""Component-suite graders offline: answer normalization, first-tool selection and arguments, skill triggers."""

import pytest

from ea_evals.components import args_match, first_tool_call, grade_skill, grade_tool_use, load_suite, normalize_answer


@pytest.mark.parametrize(
    "raw,kind,want",
    [
        ("2026-11-03", "date", "2026-11-03"),
        ("Tuesday, 3 November 2026.", "date", "2026-11-03"),
        ("11/03/2026", "date", "2026-11-03"),
        ("17:00", "time", "17:00"),
        ("5pm London", "time", "17:00"),
        ("1:00 PM", "time", "13:00"),
        ("`13:00:00`", "time", "13:00"),
        ("wednesday", "weekday", "Wednesday"),
        ("Wed", "weekday", "Wednesday"),
        ("6 hours", "number", "6"),
        ("2.0", "number", "2"),
        ("Let me think.\n\n7", "number", "7"),
    ],
)
def test_normalize(raw, kind, want):
    assert normalize_answer(raw, kind) == want


def test_every_llm_dates_answer_is_already_normalized():
    suite = load_suite("llm_dates")
    for item in suite["items"]:
        assert normalize_answer(item["answer"], item["kind"]) == item["answer"], item["id"]


def ev(seq, type_, **kw):
    return {"seq": seq, "type": type_, "agent": "main", **kw}


def test_first_tool_prefers_the_first_attempt():
    events = [ev(1, "skill_loaded", skill="scheduling"), ev(2, "tool_call", tool="clock_now", input={}),
              ev(3, "tool_call", tool="calendar_list", input={"start": "2026-10-28"})]
    assert first_tool_call(events)["tool"] == "clock_now"
    denied = [ev(1, "approval_request", tool="calendar_find_free", input={"attendees": ["lisa.park"]}), ev(2, "tool_call", tool="calendar_find_free", input={})]
    assert first_tool_call(denied)["input"] == {"attendees": ["lisa.park"]}


def test_args_semantics():
    assert args_match({"query": "Raj Thursday"}, {"any_of": [{"query": "raj"}, {"query": "zzz"}]}, "email_search")
    assert not args_match({"query": "Kevin"}, {"any_of": [{"query": "raj"}, {"query": "thursday"}]}, "email_search")
    assert args_match({"start": "2026-10-28T00:00:00-06:00"}, {"start": "@instant:2026-10-28"}, "calendar_list")
    assert not args_match({"start": "2026-10-29T00:00:00-06:00"}, {"start": "@instant:2026-10-28"}, "calendar_list")
    assert args_match({"attendees": ["dan.okafor", "lisa.park"]}, {"attendees": "lisa\\.park"}, "calendar_find_free")
    assert args_match({}, {"when_tool": {"calendar_find_free": {"duration_minutes": "^30$"}}}, "contacts_lookup")
    assert not args_match({"duration_minutes": 45}, {"when_tool": {"calendar_find_free": {"duration_minutes": "^30$"}}}, "calendar_find_free")
    assert args_match({"unread_only": True}, {"unread_only": "^true$"}, "email_search")


def test_grade_tool_use_items():
    items = {i["id"]: i for i in load_suite("tool_use")["items"]}
    good = grade_tool_use(items["tu-03"], [ev(1, "tool_call", tool="contacts_lookup", input={"query": "Dan"})])
    assert good["selection"] and good["arguments"]
    wrong_tool = grade_tool_use(items["tu-12"], [ev(1, "tool_call", tool="calendar_create", input={})])
    assert not wrong_tool["selection"] and not wrong_tool["arguments"]
    no_tool = grade_tool_use(items["tu-23"], [])
    assert no_tool["selection"]


def test_grade_skill():
    loaded_first = [ev(1, "skill_loaded", skill="scheduling"), ev(2, "tool_call", tool="clock_now", input={})]
    assert grade_skill("scheduling", loaded_first)["triggered"]
    late = [ev(1, "tool_call", tool="clock_now", input={}), ev(2, "skill_loaded", skill="scheduling")]
    g = grade_skill("scheduling", late)
    assert not g["triggered"] and g["loaded_at_all"]
    assert not grade_skill("meeting-deck", loaded_first)["loaded_at_all"]
