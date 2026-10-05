"""Calendar tools: list, get, find free time, create, update, cancel."""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import core, feedback
from .common import cap, maybe_person, next_id
from .core import tool
from .rules import (
    buffer_problems,
    busy_blocks,
    conflicts,
    contacts_by_id,
    hours_problems,
    iter_starts,
    ooo_on,
    overlaps,
)
from .rules import EARLIEST, LATEST
from .timeutil import fmt_local, hhmm, local_iso, parse_dt, parse_window_bound

MAYA_TZ = timezone(timedelta(hours=-6))  # Denver


def _cal() -> dict[str, Any]:
    return core.st().load("calendars")


def _find_event(cal: dict[str, Any], event_id: str) -> dict[str, Any]:
    for ev in cal.get("maya", []):
        if ev["id"] == event_id:
            return ev
    raise ToolError(f"Unknown event id: {event_id}")


def _attendee_ids(values: list[str]) -> list[str]:
    """Contact ids for the given ids or addresses; unknown values are kept as given."""
    out: list[str] = []
    for v in values or []:
        p = maybe_person(v)
        ident = p["id"] if p else v.strip()
        if ident and ident not in out:
            out.append(ident)
    return out


def event_view(ev: dict[str, Any]) -> dict[str, Any]:
    people = contacts_by_id(core.st().load("contacts"))
    rsvps = ev.get("rsvps", {})
    return {
        "id": ev["id"],
        "title": ev["title"],
        "start": ev["start"],
        "end": ev["end"],
        "all_day": ev.get("all_day", False),
        "attendees": [
            {"id": a, "name": people.get(a, {}).get("name", a), "rsvp": rsvps.get(a, "needs_action")}
            for a in ev.get("attendees", [])
        ],
        "description": ev.get("description", ""),
        "location": ev.get("location", ""),
        "private": ev.get("private", False),
        "tentative": ev.get("tentative", False),
        "status": ev.get("status", "active"),
    }


@tool("calendar")
def calendar_list(
    start: Annotated[str, Field(description="Window start: ISO 8601 with offset, or a date YYYY-MM-DD")],
    end: Annotated[str, Field(description="Window end: ISO 8601 with offset, or a date YYYY-MM-DD (inclusive)")],
    person: Annotated[str, Field(description="Contact id; 'maya' (default) gives full events")] = "maya",
) -> dict[str, Any]:
    """List calendar entries in a window. For Maya: her full events (title, times, attendees with RSVPs,
    description, private and tentative flags). For other internal people: busy blocks only. External
    people's calendars aren't visible."""
    tz = core.st().timezone()
    ws = parse_window_bound(start, "start", tz)
    we = parse_window_bound(end, "end", tz, end=True)
    cal = _cal()
    pid = "maya" if person in ("", "me", "maya") else person
    p = maybe_person(pid)
    if p is None:
        raise ToolError(f"Unknown contact id: {person}. Use contacts_lookup to find people.")
    core.ctx().object_id = p["id"]
    if p["id"] == "maya":
        events = []
        for ev in cal.get("maya", []):
            if ev.get("status", "active") != "active":
                continue
            s, e = parse_dt(ev["start"]), parse_dt(ev["end"])
            if overlaps(s, e, ws, we):
                events.append(event_view(ev))
        events.sort(key=lambda v: v["start"])
        return {"person": "maya", "timezone": p["timezone"], "events": events}
    if not p.get("internal"):
        return {"person": p["id"], "busy": [], "note": "External calendars aren't visible; availability is unknown."}
    blocks = []
    for b in busy_blocks(cal, p["id"]):
        if overlaps(b["start"], b["end"], ws, we):
            item = {"start": local_iso(b["start"], p["timezone"]), "end": local_iso(b["end"], p["timezone"]), "status": "busy"}
            if b["ooo"]:
                item["status"] = "out of office"
            blocks.append(item)
    blocks.sort(key=lambda b: b["start"])
    return {"person": p["id"], "timezone": p["timezone"], "busy": blocks}


@tool("calendar")
def calendar_get(event_id: Annotated[str, Field(description="Event id, e.g. ev-pipeline")]) -> dict[str, Any]:
    """Get one of Maya's events in full."""
    ev = _find_event(_cal(), event_id)
    core.ctx().object_id = event_id
    return event_view(ev)


def _hours_ok(person: dict[str, Any], start: datetime, end: datetime) -> bool:
    """Rule 1 for one attendee."""
    wh = person.get("working_hours") or {"start": "09:00", "end": "17:30"}
    s, e = start.astimezone(MAYA_TZ), end.astimezone(MAYA_TZ)
    if s.weekday() >= 5 or s.date() != e.date():
        return False
    return s.time() >= max(hhmm(wh["start"]), EARLIEST) and e.time() <= min(hhmm(wh["end"]), LATEST)


def _slot_ok(cal: dict[str, Any], people: list[dict[str, Any]], start: datetime, end: datetime) -> bool:
    for p in people:
        if not _hours_ok(p, start, end):
            return False
        if p["id"] == "maya" or p.get("internal"):
            if ooo_on(cal, p["id"], start, end) or conflicts(cal, p["id"], start, end):
                return False
    return not buffer_problems(cal, start, end)


@tool("calendar")
def calendar_find_free(
    attendees: Annotated[list[str], Field(description="Contact ids (or emails) of the people to meet; Maya is always included")],
    duration_minutes: Annotated[int, Field(description="Meeting length in minutes", ge=5, le=600)],
    window_start: Annotated[str, Field(description="Search from: ISO 8601 with offset, or a date YYYY-MM-DD")],
    window_end: Annotated[str, Field(description="Search until: ISO 8601 with offset, or a date YYYY-MM-DD (inclusive)")],
    step_minutes: Annotated[int, Field(description="Spacing of candidate start times", ge=5, le=60)] = 15,
) -> dict[str, Any]:
    """Finds free time."""
    s = core.st()
    tz = s.timezone()
    people_by_id = contacts_by_id(s.load("contacts"))
    ids = ["maya"]
    unknown = []
    for a in attendees or []:
        p = maybe_person(a)
        if p is None:
            unknown.append(a)
        elif p["id"] not in ids:
            ids.append(p["id"])
    if unknown:
        raise ToolError(f"Unknown contact id: {', '.join(unknown)}. Use contacts_lookup first.")
    people = [people_by_id[i] for i in ids]
    ws = parse_window_bound(window_start, "window_start", tz)
    we = parse_window_bound(window_end, "window_end", tz, end=True)
    now = s.now()
    if ws < now:
        ws = now
    if we <= ws:
        raise ToolError("The window ends before it starts (or is entirely in the past).")
    if we - ws > timedelta(days=21):
        raise ToolError("Search at most 21 days at a time.")
    cal = _cal()
    dur = timedelta(minutes=duration_minutes)
    step = timedelta(minutes=step_minutes)
    found = [t for t in iter_starts(ws, we, dur, step) if _slot_ok(cal, people, t, t + dur)]
    # Show up to 3 starts a day (10 in all), plus every day's full ranges of valid starts.
    shown: list[datetime] = []
    per_day: dict[str, int] = {}
    for t in found:
        day = local_iso(t, tz)[:10]
        if per_day.get(day, 0) < 3 and len(shown) < cap(10):
            shown.append(t)
            per_day[day] = per_day.get(day, 0) + 1
    ranges = []
    for t in found:
        if ranges and t - ranges[-1][1] == step:
            ranges[-1][1] = t
        else:
            ranges.append([t, t])
    externals = [p for p in people if p["id"] != "maya" and not p.get("internal")]
    notes = []
    for p in externals:
        notes.append(f"{p['name']} is outside Larkspur: free/busy unknown; only their working hours were applied.")
    if len(found) > len(shown):
        notes.append(f"{len(found)} valid starts in all; `ranges` lists them by first and last start.")
    if not found:
        notes.append("No slot satisfies every attendee's hours and Maya's rules in this window.")
    core.ctx().object_id = None
    return {
        "slots": [
            {
                "start": local_iso(t, tz),
                "end": local_iso(t + dur, tz),
                "local_times": {p["id"]: fmt_local(t, p["timezone"]) for p in people},
            }
            for t in shown
        ],
        "ranges": [
            {"first_start": local_iso(a, tz), "last_start": local_iso(b, tz), "duration_minutes": duration_minutes}
            for a, b in ranges
        ],
        "notes": notes,
    }


def _normalize_times(start: str, end: str) -> tuple[datetime, datetime]:
    s, e = parse_dt(start, "start"), parse_dt(end, "end")
    if e <= s:
        raise ToolError("end must be after start.")
    return s, e


@tool("calendar", write=True)
def calendar_create(
    title: Annotated[str, Field(description="Event title")],
    start: Annotated[str, Field(description="Start, ISO 8601 with offset")],
    end: Annotated[str, Field(description="End, ISO 8601 with offset")],
    attendees: Annotated[list[str], Field(description="Contact ids (or emails) to invite; Maya is added automatically")],
    description: Annotated[str, Field(description="Description or agenda (external meetings need an agenda)")] = "",
    idempotency_key: Annotated[str | None, Field(description="Optional key; a repeat call with the same key does nothing new")] = None,
) -> dict[str, Any]:
    """Create an event on Maya's calendar and invite the attendees. Internal attendees answer at once
    (accept or decline); the RSVPs are in the result."""
    s_dt, e_dt = _normalize_times(start, end)
    s = core.st()
    tz = s.timezone()
    cal = _cal()
    ids = ["maya"] + [a for a in _attendee_ids(attendees) if a != "maya"]
    people = contacts_by_id(s.load("contacts"))
    ev = {
        "id": next_id("ev-n", [e["id"] for e in cal.get("maya", [])]),
        "title": title.strip() or "Meeting",
        "start": local_iso(s_dt, tz),
        "end": local_iso(e_dt, tz),
        "attendees": ids,
        "rsvps": {"maya": "accepted"},
        "organizer": "maya",
        "description": description or "",
        "location": "",
        "private": False,
        "tentative": False,
        "all_day": False,
        "external": any(a in people and not people[a].get("internal") for a in ids),
        "status": "active",
        "recurring": False,
        "created_in_run": True,
    }
    cal.setdefault("maya", []).append(ev)
    feedback.rsvps_for_event(cal, ev, [a for a in ids if a != "maya"])
    s.save("calendars", cal)
    c = core.ctx()
    c.object_id = ev["id"]
    c.effect("event_created", ev["id"])
    return {"event_id": ev["id"], "start": ev["start"], "end": ev["end"], "rsvps": ev["rsvps"]}


@tool("calendar", write=True)
def calendar_update(
    event_id: Annotated[str, Field(description="The event to change")],
    start: Annotated[str | None, Field(description="New start, ISO 8601 with offset")] = None,
    end: Annotated[str | None, Field(description="New end, ISO 8601 with offset")] = None,
    title: Annotated[str | None, Field(description="New title")] = None,
    description: Annotated[str | None, Field(description="New description")] = None,
    add_attendees: Annotated[list[str] | None, Field(description="Contact ids (or emails) to add")] = None,
    remove_attendees: Annotated[list[str] | None, Field(description="Contact ids (or emails) to remove")] = None,
    idempotency_key: Annotated[str | None, Field(description="Optional key; a repeat call with the same key does nothing new")] = None,
) -> dict[str, Any]:
    """Move or change one of Maya's events. Attendees affected by the change answer again."""
    s = core.st()
    tz = s.timezone()
    cal = _cal()
    ev = _find_event(cal, event_id)
    if ev.get("status") == "cancelled":
        raise ToolError(f"Event {event_id} is cancelled.")
    changed: list[str] = []
    old_interval = (ev["start"], ev["end"])
    if start or end:
        s_dt = parse_dt(start, "start") if start else parse_dt(ev["start"])
        if end:
            e_dt = parse_dt(end, "end")
        else:
            e_dt = s_dt + (parse_dt(ev["end"]) - parse_dt(ev["start"]))
        if e_dt <= s_dt:
            raise ToolError("end must be after start.")
        if (local_iso(s_dt, tz), local_iso(e_dt, tz)) != old_interval:
            feedback.release_busy(cal, ev)
            ev["start"], ev["end"] = local_iso(s_dt, tz), local_iso(e_dt, tz)
            ev["moved_in_run"] = True  # seed busy blocks at the new time are real conflicts
            changed += ["start", "end"]
    if title is not None and title != ev["title"]:
        ev["title"] = title
        changed.append("title")
    if description is not None and description != ev.get("description", ""):
        ev["description"] = description
        changed.append("description")
    added = []
    for a in _attendee_ids(add_attendees or []):
        if a not in ev["attendees"]:
            ev["attendees"].append(a)
            added.append(a)
    if added:
        changed.append("attendees")
    removed = []
    for a in _attendee_ids(remove_attendees or []):
        if a in ev["attendees"] and a != "maya":
            feedback.release_busy(cal, ev, only=[a])  # while they're still an attendee
            ev["attendees"].remove(a)
            ev.get("rsvps", {}).pop(a, None)
            removed.append(a)
    if removed and "attendees" not in changed:
        changed.append("attendees")
    people = contacts_by_id(s.load("contacts"))
    ev["external"] = any(a in people and not people[a].get("internal") for a in ev["attendees"])
    asked = [a for a in ev["attendees"] if a != "maya"] if "start" in changed else added
    feedback.rsvps_for_event(cal, ev, asked)
    s.save("calendars", cal)
    c = core.ctx()
    c.object_id = event_id
    if changed:
        c.effect("event_updated", event_id)
    return {"event_id": event_id, "changed": changed, "start": ev["start"], "end": ev["end"], "rsvps": ev.get("rsvps", {})}


@tool("calendar", write=True)
def calendar_cancel(
    event_id: Annotated[str, Field(description="The event to cancel")],
    idempotency_key: Annotated[str | None, Field(description="Optional key; a repeat call with the same key does nothing new")] = None,
) -> dict[str, Any]:
    """Cancel one of Maya's events. Attendees are told."""
    s = core.st()
    cal = _cal()
    ev = _find_event(cal, event_id)
    if ev.get("status") == "cancelled":
        raise ToolError(f"Event {event_id} is already cancelled.")
    ev["status"] = "cancelled"
    feedback.release_busy(cal, ev)
    s.save("calendars", cal)
    c = core.ctx()
    c.object_id = event_id
    c.effect("event_cancelled", event_id)
    return {"event_id": event_id, "status": "cancelled"}
