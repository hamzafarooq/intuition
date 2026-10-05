"""Read-only views of a run directory for the UI: approval card details and Maya's world."""

import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ea_world import state

log = logging.getLogger("ea_app.views")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}


def _tz(run_dir: Path) -> str:
    clock = state.read_json(Path(run_dir) / "state" / "clock.json", {}) or {}
    return clock.get("timezone") or "America/Denver"


def _dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_range(start: Any, end: Any, tz: str) -> str:
    s, e = _dt(start), _dt(end)
    if not s:
        return str(start or "")
    zone = ZoneInfo(tz)
    s = s.astimezone(zone)
    out = s.strftime("%a %d %b, %H:%M")
    if e:
        e = e.astimezone(zone)
        out += "–" + (e.strftime("%H:%M") if e.date() == s.date() else e.strftime("%a %d %b, %H:%M"))
    city = tz.split("/")[-1].replace("_", " ")
    return f"{out} ({city})"


def _contacts(run_dir: Path) -> dict[str, dict[str, Any]]:
    data = state.read_json(Path(run_dir) / "state" / "contacts.json", {}) or {}
    return {c["id"]: c for c in data.get("contacts", []) if "id" in c}


def _names(ids: list[str], people: dict[str, dict[str, Any]]) -> str:
    out = []
    for a in ids or []:
        p = people.get(a)
        if p is None:
            p = next((c for c in people.values() if c.get("email", "").lower() == str(a).lower()), None)
        if p:
            out.append(p.get("name", a) + ("" if p.get("internal", True) else " (external)"))
        else:
            out.append(str(a))
    return ", ".join(out)


def _load(run_dir: Path, name: str) -> Any:
    return state.read_json(Path(run_dir) / "state" / state.STATE_FILES[name], state.DEFAULTS.get(name, {})) or {}


def _option(run_dir: Path, option_id: str) -> tuple[str, dict[str, Any]] | None:
    for kind, name, key in (("flight", "flights", "flights"), ("hotel", "hotels", "hotels"),
                            ("restaurant", "restaurants", "restaurants")):
        for o in _load(run_dir, name).get(key, []):
            if o.get("id") == option_id:
                return kind, o
    return None


def _option_label(kind: str, o: dict[str, Any], tz: str) -> str:
    if kind == "flight":
        dep = _dt(o.get("depart"))
        when = dep.strftime("%a %d %b %H:%M") if dep else o.get("date", "")
        cabin = str(o.get("cabin", "")).replace("_", " ")
        return f"{o.get('airline', 'Flight')} {o.get('flight_number', '')} {o.get('origin', '')} → {o.get('destination', '')}, {when}, {cabin}".replace("  ", " ")
    if kind == "hotel":
        return f"{o.get('name', 'Hotel')}, ${o.get('price_usd', '?')} a night"
    return f"{o.get('name', 'Restaurant')}, {o.get('city', '')}"


def describe_action(run_dir: Path, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """What an approval card shows: a title and label/value rows (recipient and text, event details,
    or booking and price). Never raises."""
    try:
        return _describe(Path(run_dir), tool, dict(args or {}))
    except Exception:  # a card must never break the stream
        log.exception("describe_action failed")
        return {"title": tool.replace("_", " "), "rows": []}


def _describe(run_dir: Path, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    tz = _tz(run_dir)
    people = _contacts(run_dir)
    rows: list[list[str]] = []
    if tool == "calendar_create":
        rows += [["Event", args.get("title", "")], ["When", _fmt_range(args.get("start"), args.get("end"), tz)],
                 ["With", _names(args.get("attendees", []), people)]]
        if args.get("description"):
            rows.append(["Agenda", str(args["description"])[:400]])
        return {"title": "Create this event?", "rows": rows}
    if tool in ("calendar_update", "calendar_cancel"):
        ev = next((e for e in _load(run_dir, "calendars").get("maya", []) if e.get("id") == args.get("event_id")), None)
        if ev:
            rows += [["Event", ev.get("title", "")], ["Now", _fmt_range(ev.get("start"), ev.get("end"), tz)]]
        else:
            rows.append(["Event", str(args.get("event_id", ""))])
        if tool == "calendar_cancel":
            return {"title": "Cancel this event?", "rows": rows}
        if args.get("start") or args.get("end"):
            rows.append(["Move to", _fmt_range(args.get("start") or (ev or {}).get("start"), args.get("end"), tz)])
        for k in ("title", "description"):
            if args.get(k):
                rows.append([k.capitalize(), str(args[k])[:300]])
        if args.get("add_attendees"):
            rows.append(["Add", _names(args["add_attendees"], people)])
        if args.get("remove_attendees"):
            rows.append(["Remove", _names(args["remove_attendees"], people)])
        return {"title": "Change this event?", "rows": rows}
    if tool == "email_send":
        d = next((x for x in _load(run_dir, "drafts").get("drafts", []) if x.get("id") == args.get("draft_id")), None)
        if not d:
            return {"title": "Send this email?", "rows": [["Draft", str(args.get("draft_id", ""))]]}
        rows.append(["To", ", ".join(d.get("to", []))])
        if d.get("cc"):
            rows.append(["Cc", ", ".join(d["cc"])])
        rows.append(["Subject", d.get("subject", "")])
        return {"title": "Send this email?", "rows": rows, "body": str(d.get("body", ""))[:1500]}
    if tool in ("travel_book", "restaurant_book"):
        if args.get("hold_id"):
            h = next((x for x in _load(run_dir, "holds").get("holds", [])
                      if str(x.get("hold_id", "")).lower() == str(args["hold_id"]).lower()), None)
            rows.append(["Hold", str(args["hold_id"])])
            if h:
                found = _option(run_dir, h.get("option_id", ""))
                if found:
                    rows.append(["Item", _option_label(found[0], found[1], tz)])
                det = h.get("details") or {}
                for k in ("check_in", "check_out", "date", "time", "party_size"):
                    if det.get(k):
                        rows.append([k.replace("_", " ").capitalize(), str(det[k])])
                rows.append(["Price", f"${h.get('total_usd', '?')}"])
        else:
            found = _option(run_dir, args.get("option_id", ""))
            if found:
                kind, o = found
                rows.append(["Item", _option_label(kind, o, tz)])
                if kind == "hotel" and args.get("check_in") and args.get("check_out"):
                    try:
                        nights = (date.fromisoformat(args["check_out"]) - date.fromisoformat(args["check_in"])).days
                    except ValueError:
                        nights = 0
                    rows.append(["Dates", f"{args['check_in']} to {args['check_out']} ({nights} nights)"])
                    if nights > 0:
                        rows.append(["Price", f"${o.get('price_usd', 0) * nights}"])
                elif kind == "flight":
                    n = len(args.get("travelers") or ["maya"])
                    rows.append(["Price", f"${o.get('price_usd', 0) * n}"])
                elif kind == "restaurant":
                    for k in ("date", "time", "party_size"):
                        if args.get(k):
                            rows.append([k.replace("_", " ").capitalize(), str(args[k])])
                    if args.get("party_size"):
                        rows.append(["Estimate", f"${o.get('est_cost_pp', 0) * int(args['party_size'])}"])
            else:
                rows.append(["Option", str(args.get("option_id", ""))])
        return {"title": "Book this?", "rows": rows}
    if tool == "travel_cancel":
        b = next((x for x in _load(run_dir, "bookings").get("bookings", []) if x.get("id") == args.get("booking_id")), None)
        rows.append(["Booking", str(args.get("booking_id", ""))])
        if b:
            found = _option(run_dir, b.get("option_id", ""))
            if found:
                rows.append(["Item", _option_label(found[0], found[1], tz)])
            rows.append(["Price", f"${b.get('total_usd', '?')}"])
        return {"title": "Cancel this booking?", "rows": rows}
    return {"title": f"Allow {tool.replace('_', ' ')}?", "rows": [[k, json.dumps(v)[:200]] for k, v in args.items()]}


# ---------------------------------------------------------------- Maya's world


def _event_view(ev: dict[str, Any], tz: str) -> dict[str, Any]:
    s, e = _dt(ev.get("start")), _dt(ev.get("end"))
    zone = ZoneInfo(tz)
    label = ""
    if s and e:
        ls, le = s.astimezone(zone), e.astimezone(zone)
        if ev.get("all_day") or (le - ls) >= timedelta(hours=23):
            label = "All day" if le.date() <= ls.date() + timedelta(days=1) else f"{ls:%a %d} – {le - timedelta(minutes=1):%a %d %b}"
        else:
            label = f"{ls:%H:%M}–{le:%H:%M}"
    return {**ev, "time_label": label, "local_date": s.astimezone(zone).date().isoformat() if s else ""}


def _image_files(folder: Path) -> list[str]:
    if not folder.exists():
        return []
    return sorted((p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT),
                  key=lambda n: (folder / n).stat().st_mtime)


def world_view(run_dir: Path) -> dict[str, Any]:
    """`state.world_snapshot` plus the week's calendar by day, names, and links to outputs."""
    run_dir = Path(run_dir)
    snap = state.world_snapshot(run_dir)
    tz = (snap.get("clock") or {}).get("timezone") or "America/Denver"
    now = _dt((snap.get("clock") or {}).get("now")) or datetime.now(ZoneInfo(tz))
    today = now.astimezone(ZoneInfo(tz)).date()
    monday = today - timedelta(days=today.weekday())
    events = [_event_view(e, tz) for e in snap.get("calendar", [])]
    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        todays = [e for e in events if e["local_date"] == d.isoformat()]
        if i >= 5 and not todays:
            continue
        days.append({"date": d.isoformat(), "label": f"{d:%A} {d.day} {d:%B}", "today": d == today, "events": todays})
    week_end = monday + timedelta(days=7)
    later = [e for e in events if e["local_date"] and e["local_date"] >= week_end.isoformat()][:6]
    people = _contacts(run_dir)
    out = Path(run_dir) / "outputs"
    links: dict[str, list[dict[str, Any]]] = {"briefs": [], "decks": [], "browser": []}
    for kind, prefix in (("briefs", "br-"), ("decks", "dk-")):
        for p in sorted((out / kind).glob(f"{prefix}*.json")) if (out / kind).exists() else []:
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                rec = {}
            html = p.with_suffix(".html")
            if html.exists():
                links[kind].append({"id": p.stem, "title": rec.get("title") or p.stem, "path": f"{kind}/{html.name}",
                                    "saved_at": rec.get("saved_at", "")})
    for name in _image_files(out / "browser"):
        links["browser"].append({"name": name, "path": f"browser/{name}",
                                 "label": re.sub(r"[-_]+", " ", Path(name).stem)})
    inbox = snap.get("inbox", [])
    return {
        **snap,
        "calendar": events,
        "timezone": tz,
        "week": {"start": monday.isoformat(), "end": (week_end - timedelta(days=1)).isoformat(), "days": days, "later": later},
        "contacts": {pid: {"name": c.get("name", pid), "internal": bool(c.get("internal")), "timezone": c.get("timezone", "")}
                     for pid, c in people.items()},
        "links": links,
        "new_arrivals": [e["id"] for e in inbox if e.get("arrived_in_run")],
        "unread": sum(1 for e in inbox if e.get("unread")),
    }
