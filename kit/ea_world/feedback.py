"""World feedback: attendees answer invites, contacts reply, the travel desk flags policy breaches.

Reactions are written to state at once and recorded as signals on the call that caused them.
"""

from typing import Any
from zoneinfo import ZoneInfo

from . import core
from .common import next_id
from .rules import contacts_by_id, resolve_person, rsvp_for
from .timeutil import fmt_local, parse_dt


def _new_email_id(inbox: dict[str, Any]) -> str:
    return next_id("em-r", [e["id"] for e in inbox.get("emails", [])])


def add_inbox_email(
    sender: str, sender_name: str, subject: str, body: str, thread_id: str | None = None
) -> dict[str, Any]:
    s = core.st()
    inbox = s.load("inbox")
    persona = s.load("persona")
    em = {
        "id": _new_email_id(inbox),
        "thread_id": thread_id or next_id("th-r", [e.get("thread_id", "") for e in inbox.get("emails", [])]),
        "received_at": s.load("clock")["now"],
        "from": sender,
        "from_name": sender_name,
        "to": [persona.get("email", "maya.chen@larkspur.example")],
        "cc": [],
        "subject": subject,
        "body": body,
        "unread": True,
        "html": False,
        "arrived_in_run": True,
    }
    inbox.setdefault("emails", []).append(em)
    s.save("inbox", inbox)
    return em


def release_busy(cal: dict[str, Any], ev: dict[str, Any], only: list[str] | None = None) -> None:
    """Remove the busy blocks an event put in its attendees' calendars (before a move or cancel)."""
    start, end = parse_dt(ev["start"]), parse_dt(ev["end"])
    for pid, blocks in cal.get("busy", {}).items():
        if only is not None and pid not in only:
            continue
        if pid not in ev.get("attendees", []):
            continue
        cal["busy"][pid] = [
            b for b in blocks if not (parse_dt(b["start"]) == start and parse_dt(b["end"]) == end and not b.get("ooo"))
        ]


def rsvps_for_event(cal: dict[str, Any], ev: dict[str, Any], attendee_ids: list[str]) -> None:
    """Internal attendees accept or decline by their hours and calendars; externals stay needs_action."""
    s = core.st()
    people = contacts_by_id(s.load("contacts"))
    start, end = parse_dt(ev["start"]), parse_dt(ev["end"])
    rsvps = ev.setdefault("rsvps", {"maya": "accepted"})
    c = core.ctx()
    for pid in attendee_ids:
        p = people.get(pid)
        if p is None:
            rsvps[pid] = "needs_action"
            continue
        if not p.get("internal"):
            rsvps[pid] = "needs_action"
            continue
        status, reason = rsvp_for(cal, p, start, end, ev)
        rsvps[pid] = status
        reasons = ev.setdefault("rsvp_reasons", {})
        if reason:
            reasons[pid] = reason
        else:
            reasons.pop(pid, None)
        if status == "accepted":
            busy = cal.setdefault("busy", {}).setdefault(pid, [])
            if not any(b["start"] == ev["start"] and b["end"] == ev["end"] for b in busy):
                busy.append({"start": ev["start"], "end": ev["end"], "event_id": ev["id"]})
        else:
            when = fmt_local(start, p["timezone"])
            add_inbox_email(
                p["email"],
                p["name"],
                f"Declined: {ev['title']}",
                f"{p['name']} declined \"{ev['title']}\" ({start.astimezone(ZoneInfo(p['timezone'])):%a %d %b}, {when}).\n\n{reason}.",
            )
            c.signal("decline", ev["id"], f"{p['name']} declined: {reason}")


def on_email_sent(msg: dict[str, Any]) -> None:
    """Scripted replies from the case variant."""
    s = core.st()
    rx = s.load("reactions")
    contacts = s.load("contacts")
    recipients = {a.lower() for a in msg.get("to", []) + msg.get("cc", [])}
    changed = False
    for r in rx.get("reactions", []):
        if r.get("on") != "email_send" or r.get("fired"):
            continue
        target = str(r.get("to", "")).lower()
        p = resolve_person(target, contacts)
        addresses = {target} | ({p["email"].lower()} if p else set())
        if not recipients & addresses:
            continue
        reply = r.get("reply", {})
        em = add_inbox_email(
            reply.get("from") or (p["email"] if p else target),
            reply.get("from_name") or (p["name"] if p else target),
            reply.get("subject") or f"Re: {msg.get('subject', '')}",
            reply.get("body", ""),
            thread_id=msg.get("thread_id"),
        )
        r["fired"] = True
        changed = True
        core.ctx().signal("reply", em["id"], f"{em['from_name']} replied: {reply.get('body', '')[:120]}")
    if changed:
        s.save("reactions", rx)


POLICY_CODES = {"business_class", "hotel_over_cap", "meal_over_cap", "under_7_days_without_approval", "trip_over_1500"}


def on_booking(booking: dict[str, Any], problems: list[dict[str, Any]]) -> None:
    """The travel desk emails about any policy breach."""
    breaches = [p for p in problems if p["code"] in POLICY_CODES]
    if not breaches:
        return
    items = "; ".join(p["detail"] for p in breaches)
    add_inbox_email(
        "travel@larkspur.example",
        "Larkspur Travel Desk",
        f"Policy exception needed: {items}",
        f"Booking {booking['id']} ({booking['kind']}, ${booking['total_usd']}) needs a policy exception: {items}. "
        "Please get the required approval or change the booking.",
    )
    core.ctx().signal("travel_flag", booking["id"], items)
