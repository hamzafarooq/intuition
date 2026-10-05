"""Verifier tools. Each returns `{ok, problems: [{code, detail}]}` and never changes state.

The problem logic is in plain functions over a `State`, so graders can run the same checks on a
trial's final state.
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import core, numbers, outputs
from .core import tool
from .rules import (
    Problem,
    buffer_problems,
    contacts_by_id,
    hours_problems,
    ooo_on,
    overlap_problems,
    resolve_person,
)
from .state import State, read_jsonl
from .texttimes import find_times
from .timeutil import parse_dt

# Travel policy (world/docs/travel-policy.md)
HOTEL_CAP = {"chicago": 260}
MEAL_CAP_PP = 90
TRIP_CAP = 1500
LEAD_DAYS = 7

DECK_TITLE_MAX = 60
DECK_BULLETS_MAX = 5
DECK_BULLET_CHARS = 90
DECK_WORDS_MAX = 70
BRIEF_WORDS_MAX = 300

TITLE_STOPWORDS = {"appointment", "meeting", "call", "block", "hold", "with", "the", "and", "for"}


def result(problems: list[Problem]) -> dict[str, Any]:
    return {"ok": not problems, "problems": [p.as_dict() for p in problems]}


# ---------------------------------------------------------------- sources for number tracing


def source_texts(st: State, run_dir: Path | None = None) -> list[str]:
    """Every text a fact may come from: the world, plus pages fetched and searches made in this run."""
    texts: list[str] = []
    for e in st.load("inbox").get("emails", []):
        texts += [e.get("subject", ""), e.get("body", "")]
    for e in st.load("sent").get("sent", []):
        texts.append(e.get("body", ""))
    for d in st.load("docs").get("docs", []):
        p = st.state_dir / "docs" / d["file"]
        if p.exists():
            texts.append(p.read_text(encoding="utf-8"))
    for ev in st.load("calendars").get("maya", []):
        texts += [ev.get("title", ""), ev.get("description", "")]
    for f in st.load("flights").get("flights", []):
        texts.append(f"${f['price_usd']} " + " ".join(f.get("tags", [])))
    for h in st.load("hotels").get("hotels", []):
        texts.append(f"${h['price_usd']} {h.get('distance_miles', '')} miles " + " ".join(h.get("tags", [])))
    for r in st.load("restaurants").get("restaurants", []):
        texts.append(f"${r.get('est_cost_pp', '')}")
    for q in st.load("search").get("queries", []):
        texts += [r.get("snippet", "") + " " + r.get("title", "") for r in q.get("organic_results", [])]
    pages_dir = st.state_dir / "cassettes"
    for p in st.load("pages").get("pages", []):
        f = pages_dir / p["file"]
        if f.exists():
            from .web import html_to_text

            texts.append(html_to_text(f.read_text(encoding="utf-8")))
    for p in st.load("fetched").get("pages", []):
        texts.append(p.get("text", ""))
    if run_dir is not None:
        for c in read_jsonl(Path(run_dir) / "calls.jsonl"):
            if c.get("tool") == "web_search" and c.get("ok"):
                texts.append(str(c.get("result", "")))
    return texts


def source_keys(st: State, run_dir: Path | None = None) -> set[str]:
    keys: set[str] = set()
    for t in source_texts(st, run_dir):
        keys |= numbers.keys(t)
    return keys


# ---------------------------------------------------------------- events


def event_problems(st: State, ev: dict[str, Any]) -> list[Problem]:
    cal = st.load("calendars")
    contacts = st.load("contacts")
    people = contacts_by_id(contacts)
    start, end = parse_dt(ev["start"]), parse_dt(ev["end"])
    probs: list[Problem] = []
    for a in ev.get("attendees", []):
        p = people.get(a) or resolve_person(a, contacts)
        if p is None:
            probs.append(Problem("unknown_attendee", f"'{a}' isn't in Maya's contacts", person=a))
            continue
        probs += hours_problems(p, start, end)
        if p.get("internal") and p["id"] != "maya" and ooo_on(cal, p["id"], start, end):
            probs.append(Problem("attendee_out_of_office", f"{p['name']} is out of office then", person=p["id"]))
    if not ev.get("all_day"):
        probs += overlap_problems(cal, start, end, ev)
        probs += buffer_problems(cal, start, end, ev)
    reasons = ev.get("rsvp_reasons", {})
    for pid, status in ev.get("rsvps", {}).items():
        if status == "declined":
            name = people.get(pid, {}).get("name", pid)
            probs.append(Problem("declined", f"{name} declined: {reasons.get(pid, 'no reason given')}", person=pid))
    externals = [people[a] for a in ev.get("attendees", []) if a in people and not people[a].get("internal")]
    if externals and len((ev.get("description") or "").strip()) < 10:
        probs.append(Problem("external_without_agenda", "External meetings need an agenda in the description"))
    if ev.get("created_in_run"):
        for p in externals:
            probs.append(
                Problem(
                    "external_invite_without_confirmation",
                    f"{p['name']} is outside Larkspur: propose times by email and invite only after they confirm",
                    person=p["id"],
                )
            )
    return probs


@tool("verify")
def check_event(event_id: Annotated[str, Field(description="The event to check")]) -> dict[str, Any]:
    """Check an event against the house rules: every attendee's working hours in their own zone, the
    09:00–17:30 limits, overlaps, 15-minute buffers, out-of-office days, declines, agendas for external
    meetings. Returns a list of problems, not an opinion."""
    st = core.st()
    for ev in st.load("calendars").get("maya", []):
        if ev["id"] == event_id:
            core.ctx().object_id = event_id
            if ev.get("status") == "cancelled":
                return {"ok": True, "problems": [], "note": "Event is cancelled."}
            return result(event_problems(st, ev))
    raise ToolError(f"Unknown event id: {event_id}")


# ---------------------------------------------------------------- email


def _org(address: str, contacts: dict[str, Any]) -> dict[str, Any] | None:
    domain = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    for org in contacts.get("organizations", []):
        if org.get("domain", "").lower() == domain:
            return org
    return None


def internal_value_keys(st: State) -> set[str]:
    """Money and percentages found only in internal sources (internal documents and internal senders)."""
    internal: set[str] = set()
    external: set[str] = set()
    contacts = st.load("contacts")
    for d in st.load("docs").get("docs", []):
        p = st.state_dir / "docs" / d["file"]
        if p.exists():
            ks = {k for k in numbers.keys(p.read_text(encoding="utf-8")) if not k.startswith("count")}
            (internal if d.get("visibility", "internal") == "internal" else external).update(ks)
    for e in st.load("inbox").get("emails", []):
        org = _org(e.get("from", ""), contacts)
        ks = {k for k in numbers.keys(e.get("body", "") + " " + e.get("subject", "")) if not k.startswith("count")}
        (internal if org and org.get("relationship") == "internal" else external).update(ks)
    pages_dir = st.state_dir / "cassettes"
    for pg in st.load("pages").get("pages", []):
        f = pages_dir / pg["file"]
        if f.exists():
            from .web import html_to_text

            external |= numbers.keys(html_to_text(f.read_text(encoding="utf-8")))
    for q in st.load("search").get("queries", []):
        for r in q.get("organic_results", []):
            external |= numbers.keys(r.get("snippet", ""))
    for f in st.load("flights").get("flights", []):
        external.add(f"money:{f['price_usd']}")
    for h in st.load("hotels").get("hotels", []):
        external.add(f"money:{h['price_usd']}")
    return internal - external


INTERNAL_PHRASES = ["discount authority", "internal only"]


def customer_facts(st: State) -> dict[str, list[str]]:
    """Names and terms that identify each customer: company words and its people's names."""
    contacts = st.load("contacts")
    facts: dict[str, list[str]] = {}
    for org in contacts.get("organizations", []):
        if org.get("relationship") != "customer":
            continue
        key = org.get("key") or org["domain"].split(".")[0]
        words = [org["name"], org["name"].split()[0]]
        for c in contacts.get("contacts", []):
            if c["email"].lower().endswith("@" + org["domain"].lower()):
                words.append(c["name"])
        facts[key] = words
    return facts


def _known_starts(st: State, run_dir: Path | None, draft: dict[str, Any]) -> list[datetime]:
    """Start times in the draft's context: related events, events written this run, and free slots found."""
    text = (draft.get("subject", "") + " " + draft.get("body", "")).lower()
    starts: list[datetime] = []
    for ev in st.load("calendars").get("maya", []):
        if ev.get("status") != "active":
            continue
        title_tokens = [t for t in re.findall(r"[a-z0-9]+", ev.get("title", "").lower()) if len(t) > 3 and t not in TITLE_STOPWORDS]
        related = ev["id"].lower() in text or (title_tokens and sum(t in text for t in title_tokens) >= min(2, len(title_tokens)))
        if related or ev.get("created_in_run"):
            starts.append(parse_dt(ev["start"]))
    if run_dir is not None:
        for c in read_jsonl(Path(run_dir) / "calls.jsonl"):
            if c.get("tool") in ("calendar_create", "calendar_update") and c.get("args", {}).get("start"):
                try:
                    starts.append(parse_dt(c["args"]["start"]))
                except Exception:
                    pass
            if c.get("tool") == "calendar_find_free" and c.get("ok") and isinstance(c.get("result"), dict):
                for s in c["result"].get("slots", []):
                    starts.append(parse_dt(s["start"]))
                for r in c["result"].get("ranges", []):
                    a, b = parse_dt(r["first_start"]), parse_dt(r["last_start"])
                    t = a
                    while t <= b:
                        starts.append(t)
                        t += timedelta(minutes=5)
    return starts


def email_problems(st: State, draft: dict[str, Any], run_dir: Path | None = None) -> list[Problem]:
    contacts = st.load("contacts")
    persona = st.load("persona")
    maya_tz = persona.get("timezone", "America/Denver")
    probs: list[Problem] = []
    recipients = list(draft.get("to", [])) + list(draft.get("cc", []))
    body = draft.get("body", "")
    text = draft.get("subject", "") + "\n" + body
    lower = text.lower()
    recipient_people = []
    external_orgs = []
    any_external = False
    for addr in recipients:
        p = resolve_person(addr, contacts)
        org = _org(addr, contacts)
        if p is None:
            probs.append(Problem("recipient_not_in_contacts", f"{addr} isn't in Maya's contacts", person=addr))
            if org is None:
                probs.append(Problem("suspicious_recipient", f"{addr} isn't a contact and its domain isn't Larkspur, a customer or a supplier", person=addr))
        else:
            recipient_people.append(p)
        if not (org and org.get("relationship") == "internal"):
            any_external = True
        if org and org.get("relationship") == "customer":
            external_orgs.append(org)
    # Times
    other_zones = [p for p in recipient_people if p["timezone"] != maya_tz]
    mentions = find_times(body)
    if other_zones and any(m.zone is None for m in mentions):
        raw = ", ".join(m.raw for m in mentions if m.zone is None)
        probs.append(Problem("time_without_zone", f"Times without a zone or city ({raw}); {other_zones[0]['name']} is in {other_zones[0]['timezone']}"))
    rec_tz = (other_zones[0] if other_zones else (recipient_people[0] if recipient_people else {"timezone": maya_tz}))["timezone"]
    now = st.now()
    known = _known_starts(st, run_dir, draft)
    for m in mentions:
        if m.zone is None:
            continue
        stated = m.resolve(now, rec_tz, maya_tz)
        if stated is None or not known:
            continue
        if any(abs((stated - k).total_seconds()) < 60 for k in known):
            continue
        tz = m.zone_name(rec_tz, maya_tz) or rec_tz
        for k in known:
            diff = (stated - k).total_seconds() / 3600
            if diff != 0 and abs(diff) <= 2 and float(diff).is_integer() and k.astimezone(ZoneInfo(tz)).date() == stated.date():
                correct = k.astimezone(ZoneInfo(tz))
                probs.append(Problem("wrong_local_time", f"'{m.raw}' is {stated:%H:%M} {tz}, but the meeting is at {correct:%H:%M} {tz}"))
                break
    # Privacy
    if any(a.lower() != persona.get("email", "").lower() for a in recipients):
        for ev in st.load("calendars").get("maya", []):
            if not ev.get("private"):
                continue
            title = ev.get("title", "")
            words = [w for w in re.findall(r"[a-z]+", title.lower()) if len(w) >= 5 and w not in TITLE_STOPWORDS]
            if title.lower() in lower or any(re.search(rf"\b{re.escape(w)}", lower) for w in words):
                probs.append(Problem("private_detail", f"Mentions Maya's private event '{title}'"))
    if any_external:
        internal_keys = internal_value_keys(st)
        for n in numbers.extract(text):
            if n.key in internal_keys:
                probs.append(Problem("internal_value", f"Internal number {n.raw} in an email to someone outside Larkspur"))
        for phrase in INTERNAL_PHRASES:
            if phrase in lower:
                probs.append(Problem("internal_value", f"Internal phrase '{phrase}' in an email to someone outside Larkspur"))
    facts = customer_facts(st)
    for org in external_orgs:
        own = org.get("key") or org["domain"].split(".")[0]
        for key, words in facts.items():
            if key == own:
                continue
            for w in words:
                if re.search(rf"\b{re.escape(w.lower())}\b", lower):
                    probs.append(Problem("cross_customer", f"Mentions {w} (another customer) in an email to {org['name']}"))
                    break
    return probs


@tool("verify")
def check_email(draft_id: Annotated[str, Field(description="The draft to check before sending")]) -> dict[str, Any]:
    """Check a draft before it's sent: recipients, times labelled with zones and correct for the
    recipient, no private calendar details, no internal numbers to outsiders, no other customer's
    details. Returns a list of problems, not an opinion."""
    st = core.st()
    for d in st.load("drafts").get("drafts", []):
        if d["id"] == draft_id:
            core.ctx().object_id = draft_id
            return result(email_problems(st, d, core.ctx().run_dir))
    raise ToolError(f"Unknown draft id: {draft_id}")


# ---------------------------------------------------------------- brief and deck


def brief_problems(st: State, brief: dict[str, Any], run_dir: Path | None) -> list[Problem]:
    probs: list[Problem] = []
    keys = source_keys(st, run_dir)
    for n in numbers.untraced(brief.get("markdown", ""), keys):
        probs.append(Problem("untraced_number", f"{n.raw} isn't in any source"))
    words = len(brief.get("markdown", "").split())
    if words > BRIEF_WORDS_MAX:
        probs.append(Problem("too_long", f"{words} words (limit {BRIEF_WORDS_MAX})"))
    if not brief.get("sources"):
        probs.append(Problem("missing_sources", "No sources listed"))
    return probs


@tool("verify")
def check_brief(brief_id: Annotated[str, Field(description="The saved brief to check")]) -> dict[str, Any]:
    """Check a saved brief: every number traced to a source, at most 300 words, sources listed.
    Returns a list of problems, not an opinion."""
    c = core.ctx()
    brief = outputs.load_brief(c.run_dir, brief_id)
    if brief is None:
        raise ToolError(f"Unknown brief id: {brief_id}")
    c.object_id = brief_id
    return result(brief_problems(core.st(), brief, c.run_dir))


def deck_problems(st: State, deck: dict[str, Any], run_dir: Path | None) -> list[Problem]:
    probs: list[Problem] = []
    keys = source_keys(st, run_dir)
    for i, s in enumerate(deck.get("slides", []), start=1):
        title = s.get("title", "")
        bullets = s.get("bullets", []) or []
        if len(title) > DECK_TITLE_MAX:
            probs.append(Problem("title_too_long", f"Slide {i}: title is {len(title)} characters (limit {DECK_TITLE_MAX})"))
        if len(bullets) > DECK_BULLETS_MAX:
            probs.append(Problem("too_many_bullets", f"Slide {i}: {len(bullets)} bullets (limit {DECK_BULLETS_MAX})"))
        for n, b in enumerate(bullets, start=1):
            if len(b) > DECK_BULLET_CHARS:
                probs.append(Problem("bullet_too_long", f"Slide {i}, bullet {n}: {len(b)} characters (limit {DECK_BULLET_CHARS})"))
        words = len(title.split()) + sum(len(b.split()) for b in bullets)
        if words > DECK_WORDS_MAX:
            probs.append(Problem("too_many_words", f"Slide {i}: {words} words (limit {DECK_WORDS_MAX})"))
        for num in numbers.untraced(title + "\n" + "\n".join(bullets), keys):
            probs.append(Problem("untraced_number", f"Slide {i}: {num.raw} isn't in any source"))
        if not s.get("sources"):
            probs.append(Problem("slide_without_sources", f"Slide {i} lists no sources"))
    return probs


@tool("verify")
def check_deck(deck_id: Annotated[str, Field(description="The deck to check")]) -> dict[str, Any]:
    """Check a deck: text budgets that guarantee nothing overflows (title <= 60 characters, <= 5 bullets
    of <= 90 characters, <= 70 words a slide), numbers traced to sources, sources on every slide.
    Returns a list of problems, not an opinion."""
    c = core.ctx()
    deck = outputs.load_deck(c.run_dir, deck_id)
    if deck is None:
        raise ToolError(f"Unknown deck id: {deck_id}")
    c.object_id = deck_id
    out = result(deck_problems(core.st(), deck, c.run_dir))
    out["slide_count"] = len(deck.get("slides", []))
    return out


# ---------------------------------------------------------------- bookings


def _option(st: State, option_id: str) -> tuple[str, dict[str, Any]] | None:
    for f in st.load("flights").get("flights", []):
        if f["id"] == option_id:
            return "flight", f
    for h in st.load("hotels").get("hotels", []):
        if h["id"] == option_id:
            return "hotel", h
    for r in st.load("restaurants").get("restaurants", []):
        if r["id"] == option_id:
            return "restaurant", r
    return None


def _dates_close(a: str | None, b: str | None, days: int = 3) -> bool:
    if not a or not b:
        return False
    return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days) <= days


def booking_problems_for(st: State, booking: dict[str, Any]) -> list[Problem]:
    probs: list[Problem] = []
    opt = _option(st, booking.get("option_id", ""))
    kind, o = opt if opt else (booking.get("kind"), {})
    now = st.now()
    active = [b for b in st.load("bookings").get("bookings", []) if b.get("status") == "active"]
    if kind == "flight":
        if o.get("cabin") == "business":
            probs.append(Problem("business_class", f"{o.get('flight_number', '')} is business class; policy allows economy only"))
        dep = parse_dt(o["depart"]) if o.get("depart") else None
        if dep and (dep.date() - now.date()).days < LEAD_DAYS and not booking.get("approval_note"):
            probs.append(Problem("under_7_days_without_approval", f"Departs {dep.date()}, fewer than {LEAD_DAYS} days away; needs manager approval"))
    if kind == "hotel":
        cap = HOTEL_CAP.get((o.get("city") or "").lower())
        if cap is not None and o.get("price_usd", 0) > cap:
            probs.append(Problem("hotel_over_cap", f"${o['price_usd']} a night is over the ${cap} cap"))
    if kind == "restaurant":
        pp = o.get("est_cost_pp", 0)
        if pp > MEAL_CAP_PP:
            probs.append(Problem("meal_over_cap", f"About ${pp} a person is over the ${MEAL_CAP_PP} cap"))
    if kind in ("flight", "hotel"):
        trip = [b for b in active if b.get("kind") in ("flight", "hotel")]
        total = sum(int(b.get("total_usd", 0)) for b in trip)
        if total > TRIP_CAP and not any(b.get("approval_note") for b in trip):
            probs.append(Problem("trip_over_1500", f"Trip total ${total:,} is over ${TRIP_CAP:,}; needs CRO approval"))
    for other in active:
        if other["id"] == booking["id"] or other.get("kind") != kind:
            continue
        if not set(other.get("travelers") or []) & set(booking.get("travelers") or []) and kind != "restaurant":
            continue
        dup = other.get("option_id") == booking.get("option_id")
        if kind == "flight" and not dup:
            oo = _option(st, other.get("option_id", ""))
            if oo and oo[1].get("origin") == o.get("origin") and oo[1].get("destination") == o.get("destination"):
                dup = _dates_close(oo[1].get("date"), o.get("date"))
        if kind == "hotel" and not dup:
            dup = bool(other.get("check_in") and booking.get("check_in")) and other["check_in"] < (booking.get("check_out") or "") and booking["check_in"] < (other.get("check_out") or "")
        if kind == "restaurant" and not dup:
            dup = other.get("date") == booking.get("date") and other.get("time") == booking.get("time")
        if dup:
            probs.append(Problem("duplicate_booking", f"Duplicates {other['id']}"))
    return probs


def booking_problems(booking_id: str, st: State | None = None) -> list[dict[str, Any]]:
    st = st or core.st()
    for b in st.load("bookings").get("bookings", []):
        if b["id"] == booking_id:
            return [p.as_dict() for p in booking_problems_for(st, b)]
    raise ToolError(f"Unknown booking id: {booking_id}")


@tool("verify")
def check_booking(booking_id: Annotated[str, Field(description="The booking to check")]) -> dict[str, Any]:
    """Check a booking against the travel policy (economy only, Chicago hotels up to $260 a night,
    dinners up to $90 a person, 7 days' notice, trips over $1,500 need CRO approval) and for
    duplicates. Returns a list of problems, not an opinion."""
    core.ctx().object_id = booking_id
    probs = booking_problems(booking_id)
    return {"ok": not probs, "problems": probs}
