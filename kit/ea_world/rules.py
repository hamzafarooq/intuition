"""House rules 1 and 2 as code, shared by calendar_find_free, check_event, world feedback and graders.

Rule 1: inside every attendee's working hours in their own time zone, Monday to Friday, and never
before 09:00 or after 17:30 local time for anyone.
Rule 2: at least 15 minutes between any two of Maya's meetings.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .timeutil import hhmm, parse_dt

EARLIEST = time(9, 0)
LATEST = time(17, 30)
BUFFER = timedelta(minutes=15)


@dataclass
class Problem:
    code: str
    detail: str
    person: str | None = None
    event_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.person:
            d["person"] = self.person
        if self.event_id:
            d["event_id"] = self.event_id
        return d


def contacts_by_id(contacts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in contacts.get("contacts", [])}


def contacts_by_email(contacts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["email"].lower(): c for c in contacts.get("contacts", [])}


def resolve_person(value: str, contacts: dict[str, Any]) -> dict[str, Any] | None:
    """A contact by id or email address."""
    v = (value or "").strip().lower()
    by_id = {k.lower(): c for k, c in contacts_by_id(contacts).items()}
    if v in by_id:
        return by_id[v]
    return contacts_by_email(contacts).get(v)


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def event_blocks(ev: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    """The busy intervals an event occupies (all-day events may list busy_blocks)."""
    if ev.get("busy_blocks"):
        return [(parse_dt(b["start"]), parse_dt(b["end"])) for b in ev["busy_blocks"]]
    return [(parse_dt(ev["start"]), parse_dt(ev["end"]))]


def maya_events(cal: dict[str, Any], exclude: set[str] | None = None) -> list[dict[str, Any]]:
    exclude = exclude or set()
    return [e for e in cal.get("maya", []) if e.get("status", "active") == "active" and e["id"] not in exclude]


def busy_blocks(
    cal: dict[str, Any], person: str, exclude_event: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Busy blocks for a person. Maya's come from her events; others' from the `busy` map.

    `exclude_event` drops the blocks that represent that event itself (so an event never conflicts
    with its own entry in an attendee's calendar).
    """
    if person == "maya":
        out = []
        for ev in maya_events(cal, {exclude_event["id"]} if exclude_event else None):
            for s, e in event_blocks(ev):
                out.append({"start": s, "end": e, "event_id": ev["id"], "ooo": False})
        return out
    own = None
    if exclude_event is not None:
        own = (parse_dt(exclude_event["start"]), parse_dt(exclude_event["end"]))
    out = []
    for b in cal.get("busy", {}).get(person, []):
        s, e = parse_dt(b["start"]), parse_dt(b["end"])
        if own is not None and (s, e) == own:
            continue
        out.append({"start": s, "end": e, "event_id": None, "ooo": bool(b.get("ooo"))})
    return out


def local_window(person: dict[str, Any], day_local: datetime) -> tuple[datetime, datetime] | None:
    """The person's allowed meeting window on that local date (rule 1), or None on weekends."""
    if day_local.weekday() >= 5:
        return None
    tz = ZoneInfo(person["timezone"])
    wh = person.get("working_hours") or {"start": "09:00", "end": "17:30"}
    start_t = max(hhmm(wh["start"]), EARLIEST)
    end_t = min(hhmm(wh["end"]), LATEST)
    d = day_local.date()
    return datetime.combine(d, start_t, tzinfo=tz), datetime.combine(d, end_t, tzinfo=tz)


def hours_problems(person: dict[str, Any], start: datetime, end: datetime) -> list[Problem]:
    """Rule 1 for one person: their own hours, their own zone, and the 09:00–17:30 limits."""
    tz = ZoneInfo(person["timezone"])
    ls, le = start.astimezone(tz), end.astimezone(tz)
    pid = person["id"]
    probs: list[Problem] = []
    wh = person.get("working_hours") or {"start": "09:00", "end": "17:30"}
    midnight = datetime.combine(ls.date(), time(0, 0), tzinfo=tz)
    start_m = (ls - midnight).total_seconds() / 60
    end_m = (le - midnight).total_seconds() / 60  # > 1440 when the slot runs past midnight

    def minutes(t: time) -> int:
        return t.hour * 60 + t.minute

    ws, we = minutes(hhmm(wh["start"])), minutes(hhmm(wh["end"]))
    if ls.weekday() >= 5 or start_m < ws or end_m > we:
        probs.append(
            Problem(
                "outside_working_hours",
                f"{person['name']}: {ls:%a %H:%M}–{le:%H:%M} local is outside {wh['start']}–{wh['end']} "
                f"({person['timezone']}, Mon–Fri)",
                person=pid,
            )
        )
    if start_m < minutes(EARLIEST) or end_m > minutes(LATEST):
        probs.append(
            Problem(
                "before_9_or_after_1730",
                f"{person['name']}: {ls:%H:%M}–{le:%H:%M} local is before 09:00 or after 17:30",
                person=pid,
            )
        )
    return probs


def ooo_on(cal: dict[str, Any], person: str, start: datetime, end: datetime) -> bool:
    for b in busy_blocks(cal, person):
        if b["ooo"] and overlaps(start, end, b["start"], b["end"]):
            return True
    return False


def conflicts(
    cal: dict[str, Any], person: str, start: datetime, end: datetime, exclude_event: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    return [
        b
        for b in busy_blocks(cal, person, exclude_event)
        if not b["ooo"] and overlaps(start, end, b["start"], b["end"])
    ]


def buffer_problems(
    cal: dict[str, Any], start: datetime, end: datetime, exclude_event: dict[str, Any] | None = None
) -> list[Problem]:
    """Rule 2: 15 minutes between Maya's meetings (overlaps are reported separately)."""
    probs = []
    for b in busy_blocks(cal, "maya", exclude_event):
        if overlaps(start, end, b["start"], b["end"]):
            continue
        if overlaps(start - BUFFER, end + BUFFER, b["start"], b["end"]):
            probs.append(Problem("buffer_under_15", f"Less than 15 minutes from {b['event_id']}", event_id=b["event_id"]))
    return probs


def overlap_problems(
    cal: dict[str, Any], start: datetime, end: datetime, exclude_event: dict[str, Any] | None = None
) -> list[Problem]:
    return [
        Problem("overlap", f"Overlaps Maya's event {b['event_id']}", event_id=b["event_id"])
        for b in busy_blocks(cal, "maya", exclude_event)
        if overlaps(start, end, b["start"], b["end"])
    ]


def slot_ok(
    cal: dict[str, Any],
    contacts: dict[str, Any],
    attendees: list[str],
    start: datetime,
    end: datetime,
    exclude_event: dict[str, Any] | None = None,
) -> bool:
    """True when the slot satisfies rules 1–2 for Maya and every attendee.

    Internal attendees' calendars are checked; external attendees only by their working hours.
    """
    people = contacts_by_id(contacts)
    ids = ["maya"] + [a for a in attendees if a != "maya"]
    for pid in ids:
        p = people.get(pid)
        if p is None:
            return False
        if hours_problems(p, start, end):
            return False
        if p.get("internal", False) or pid == "maya":
            if ooo_on(cal, pid, start, end) or conflicts(cal, pid, start, end, exclude_event):
                return False
    return not buffer_problems(cal, start, end, exclude_event)


def rsvp_for(
    cal: dict[str, Any], person: dict[str, Any], start: datetime, end: datetime, event: dict[str, Any] | None
) -> tuple[str, str]:
    """How an internal attendee answers an invite: (status, reason)."""
    pid = person["id"]
    if ooo_on(cal, pid, start, end):
        return "declined", "I'm out of office"
    if hours_problems(person, start, end):
        return "declined", "Outside my working hours"
    if conflicts(cal, pid, start, end, event):
        return "declined", "I have a conflict"
    return "accepted", ""


def iter_starts(window_start: datetime, window_end: datetime, duration: timedelta, step: timedelta):
    """Candidate starts aligned to `step` (in UTC minutes, which aligns every whole-hour zone)."""
    epoch = datetime(2000, 1, 1, tzinfo=ZoneInfo("UTC"))
    secs = int((window_start - epoch).total_seconds())
    step_s = int(step.total_seconds())
    rem = secs % step_s
    t = window_start + timedelta(seconds=(step_s - rem) % step_s)
    while t + duration <= window_end:
        yield t
        t += step
