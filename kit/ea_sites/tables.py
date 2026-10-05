"""Tables: restaurant search → available times → name and phone → review (creates a hold)."""

from __future__ import annotations

import re
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route, Router

from . import pages
from .common import (
    SiteRequest,
    clean_name,
    create_hold,
    day_label,
    money,
    option,
    parse_day,
    parse_int,
    parse_time,
)

SITE = "tables"
MAX_PARTY = 12
TIME_CHOICES = [f"{h:02d}:{m:02d}" for h in range(11, 23) for m in (0, 30)]


def _restaurants(sr: SiteRequest) -> list[dict[str, Any]]:
    return (sr.load("restaurants") or {}).get("restaurants", [])


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _search_form(sr: SiteRequest, q: dict[str, str], errors: dict[str, str], status: int = 200) -> Response:
    form = {"city": q.get("city", ""), "date": q.get("date", ""), "time": parse_time(q.get("time")) or q.get("time", "19:00"),
            "party_size": q.get("party_size", "2")}
    times = list(TIME_CHOICES)
    if form["time"] and form["time"] not in times and parse_time(form["time"]):
        times = sorted(set(times) | {form["time"]})
    return sr.render("tables/search.html", "Find a table", code=status, form=form, errors=errors, times=times,
                     max_party=MAX_PARTY, step=1)


async def search(request: Request) -> Response:
    sr = SiteRequest(request, SITE)
    return _search_form(sr, sr.query(), {})


def _criteria(sr: SiteRequest, q: dict[str, str]) -> tuple[str, str | None, str | None, int | None, dict[str, str]]:
    errors: dict[str, str] = {}
    city = clean_name(q.get("city"))
    day = parse_day(q.get("date"), sr.now().year)
    time = parse_time(q.get("time"))
    party = parse_int(q.get("party_size"), 1, MAX_PARTY)
    if not city:
        errors["city"] = "Enter a city, for example Chicago."
    if day is None:
        errors["date"] = "Enter the date as YYYY-MM-DD, for example 2026-11-03."
    if time is None:
        errors["time"] = "Choose a time, for example 19:00."
    if party is None:
        errors["party_size"] = f"Choose a party size from 1 to {MAX_PARTY}."
    return city, (day.isoformat() if day else None), time, party, errors


async def results(request: Request) -> Response:
    sr = SiteRequest(request, SITE)
    q = sr.query()
    sr.log("form_submit", q)
    city, day, time, party, errors = _criteria(sr, q)
    if errors:
        return _search_form(sr, q, errors, status=400)
    assert day and time and party
    in_city = [r for r in _restaurants(sr) if city.lower() in (r.get("city") or "").lower()]
    on_day = [r for r in in_city if r.get("date") == day]
    want = _minutes(time)
    cards = []
    for r in sorted(on_day, key=lambda r: min((abs(_minutes(t) - want) for t in r.get("times", [])), default=9999)):
        fits = party <= r.get("max_party", 0)
        cards.append({**r, "fits": fits, "estimate": r.get("est_cost_pp", 0) * party,
                      "slots": [{"time": t, "exact": t == time,
                                 "url": sr.url("/details", restaurant=r["id"], date=day, time=t, party_size=party)}
                                for t in sorted(r.get("times", []))] if fits else []})
    other_days = sorted({r["date"] for r in in_city if r.get("date") and r["date"] != day})
    other_links = [{"label": day_label(d), "url": sr.url("/results", city=city, date=d, time=time, party_size=party)}
                   for d in other_days]
    shown_city = in_city[0]["city"] if in_city else city
    return sr.render("tables/results.html", f"Tables in {shown_city}", city=shown_city, day=day, time=time, party=party,
                     restaurants=cards, other_days=other_links,
                     change_url=sr.url("/", city=city, date=day, time=time, party_size=party), step=2)


def _check_slot(sr: SiteRequest, data: dict[str, str]) -> tuple[dict[str, Any] | None, str | None, str | None, int | None, str | None]:
    r = option(sr, "restaurant", data.get("restaurant", ""))
    day = parse_day(data.get("date"), sr.now().year)
    time = parse_time(data.get("time"))
    party = parse_int(data.get("party_size"), 1, MAX_PARTY)
    if not r:
        return None, None, None, None, "That restaurant isn't taking bookings here any more. Search again."
    if not day or r.get("date") != day.isoformat():
        return r, None, None, None, f"{r['name']} has no tables on that date. Search again."
    if not time or time not in r.get("times", []):
        return r, None, None, None, f"{r['name']} has no table at that time. Available: {', '.join(r.get('times', []))}."
    if not party or party > r.get("max_party", 0):
        return r, None, None, None, f"{r['name']} seats parties of up to {r.get('max_party')}."
    return r, day.isoformat(), time, party, None


def _details_form(sr: SiteRequest, r: dict[str, Any], day: str, time: str, party: int, values: dict[str, str],
                  errors: dict[str, str], status: int = 200) -> Response:
    me = sr.user()
    form = {"name": values.get("name", me["name"]), "phone": values.get("phone", "")}
    return sr.render("tables/details.html", "Your details", code=status, r=r, day=day, time=time, party=party,
                     estimate=r.get("est_cost_pp", 0) * party, form=form, errors=errors,
                     back_url=sr.url("/results", city=r.get("city"), date=day, time=time, party_size=party), step=3)


async def details(request: Request) -> Response:
    sr = SiteRequest(request, SITE)
    q = sr.query()
    sr.log("form_submit", q)
    r, day, time, party, problem = _check_slot(sr, q)
    if problem or not (r and day and time and party):
        return sr.render("error.html", "That table isn't available", code=404, heading="That table isn't available",
                         message=problem or "Search again.")
    return _details_form(sr, r, day, time, party, {}, {})


async def review_post(request: Request) -> Response:
    sr = SiteRequest(request, SITE)
    form = await sr.form()
    sr.log("form_submit", form)
    r, day, time, party, problem = _check_slot(sr, form)
    if problem or not (r and day and time and party):
        return sr.render("error.html", "That table isn't available", code=404, heading="That table isn't available",
                         message=problem or "Search again.")
    errors: dict[str, str] = {}
    name = clean_name(form.get("name"))
    phone = (form.get("phone") or "").strip()
    if not name:
        errors["name"] = "Enter the name for the reservation."
    if phone and not re.fullmatch(r"[0-9+()\-.\s]{7,20}", phone):
        errors["phone"] = "Enter a phone number using digits, spaces, +, ( ) or -, or leave it blank."
    if errors:
        return _details_form(sr, r, day, time, party, form, errors, status=400)
    total = r.get("est_cost_pp", 0) * party
    det = {"date": day, "time": time, "party_size": party, "guests": [], "name": name, "phone": phone,
           "unit_price_usd": r.get("est_cost_pp", 0)}
    summary = f"{r['name']}, {day_label(day)} {time}, party of {party}, about {money(total)}"
    hold, _ = create_hold(sr, "restaurant", r["id"], det, total, summary)
    return sr.redirect(f"/review/{hold['hold_id']}")


def describe(sr: SiteRequest, record: dict[str, Any], hold: dict[str, Any] | None = None) -> dict[str, Any]:
    det = (hold or {}).get("details") or record.get("details") or {}
    r = option(sr, "restaurant", record.get("option_id", ""))
    day = record.get("date") or det.get("date")
    time = record.get("time") or det.get("time")
    party = record.get("party_size") or det.get("party_size") or 1
    unit = (r or {}).get("est_cost_pp") or det.get("unit_price_usd") or record.get("unit_price_usd") or 0
    facts: list[tuple[str, str]] = []
    title = "Table"
    if r:
        title = f"{r['name']}, {day_label(day)} at {time}"
        facts += [("Restaurant", r["name"]), ("Cuisine", r.get("cuisine", "")), ("Address", r.get("address", ""))]
    else:
        facts.append(("Restaurant", (hold or record).get("summary") or record.get("option_id", "")))
    facts += [("Date", day_label(day)), ("Time", time or ""), ("Party size", str(party))]
    if det.get("name"):
        facts.append(("Name on the reservation", det["name"]))
    if det.get("phone"):
        facts.append(("Phone", det["phone"]))
    lines = [(f"About {money(unit)} a person including tip × {party}", money(unit * party))]
    return {"title": title, "facts": facts, "lines": lines, "estimate": True}


def router() -> Router:
    return Router(routes=[
        Route("/", search, methods=["GET"]),
        Route("/results", results, methods=["GET"]),
        Route("/details", details, methods=["GET"]),
        Route("/review", review_post, methods=["POST"]),
        *pages.shared_routes(SITE),
    ])
