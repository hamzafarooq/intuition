"""Skyway: flight search → fares → passenger details → review (creates a hold)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route, Router

from . import pages
from .common import (
    SiteRequest,
    clean_name,
    collect_people,
    create_hold,
    day_label,
    money,
    option,
    parse_day,
    parse_int,
    place_match,
)

SITE = "skyway"
MAX_PASSENGERS = 4
CABINS = {"economy": "Economy", "basic_economy": "Basic Economy", "business": "Business"}


def _flights(sr: SiteRequest) -> list[dict[str, Any]]:
    return (sr.load("flights") or {}).get("flights", [])


def flight_view(f: dict[str, Any], passengers: int = 1) -> dict[str, Any]:
    dep, arr = datetime.fromisoformat(f["depart"]), datetime.fromisoformat(f["arrive"])
    mins = int((arr - dep).total_seconds() // 60)
    plus = (arr.date() - dep.date()).days
    return {
        **f,
        "dep_time": dep.strftime("%H:%M"),
        "arr_time": arr.strftime("%H:%M"),
        "plus_days": plus,
        "arr_day": day_label(arr.date()) if plus else "",
        "duration": f"{mins // 60}h {mins % 60:02d}m",
        "cabin_label": CABINS.get(f.get("cabin", ""), str(f.get("cabin", "")).replace("_", " ").title()),
        "route": f"{f.get('origin_city', '')} ({f['origin']}) to {f.get('destination_city', '')} ({f['destination']})",
        "passengers": passengers,
        "total": f["price_usd"] * passengers,
    }


def _search_form(sr: SiteRequest, q: dict[str, str], errors: dict[str, str], status: int = 200) -> Response:
    form = {"from": q.get("from", ""), "to": q.get("to", ""), "date": q.get("date", ""),
            "passengers": q.get("passengers", "1")}
    return sr.render("skyway/search.html", "Find a flight", code=status, form=form, errors=errors,
                     max_passengers=MAX_PASSENGERS, step=1)


async def search(request: Request) -> Response:
    sr = SiteRequest(request, SITE)
    return _search_form(sr, sr.query(), {})


async def results(request: Request) -> Response:
    sr = SiteRequest(request, SITE)
    q = sr.query()
    sr.log("form_submit", q)
    errors: dict[str, str] = {}
    origin, dest = clean_name(q.get("from")), clean_name(q.get("to"))
    if not origin:
        errors["from"] = "Enter where you're flying from, a city or airport code."
    if not dest:
        errors["to"] = "Enter where you're flying to, a city or airport code."
    day = parse_day(q.get("date"), sr.now().year)
    if day is None:
        errors["date"] = "Enter the departure date as YYYY-MM-DD, for example 2026-11-02."
    pax = parse_int(q.get("passengers", "1") or "1", 1, MAX_PASSENGERS)
    if pax is None:
        errors["passengers"] = f"Choose between 1 and {MAX_PASSENGERS} passengers."
    if errors:
        return _search_form(sr, q, errors, status=400)
    assert day is not None and pax is not None
    on_route = [f for f in _flights(sr) if place_match(origin, f["origin"], f.get("origin_city", ""))
                and place_match(dest, f["destination"], f.get("destination_city", ""))]
    found = sorted((f for f in on_route if f["date"] == day.isoformat()), key=lambda f: (f["depart"], f["price_usd"]))
    other_days = sorted({f["date"] for f in on_route if f["date"] != day.isoformat()})
    sample = found[0] if found else (on_route[0] if on_route else None)
    heading = (f"{sample['origin_city']} to {sample['destination_city']}" if sample else f"{origin} to {dest}")
    other_links = [{"label": day_label(d), "url": sr.url("/results", **{"from": origin, "to": dest, "date": d, "passengers": pax})}
                   for d in other_days]
    return sr.render("skyway/results.html", f"Flights: {heading}", heading=heading, day=day.isoformat(),
                     passengers=pax, flights=[flight_view(f, pax) for f in found], other_days=other_links,
                     change_url=sr.url("/", **{"from": origin, "to": dest, "date": day.isoformat(), "passengers": pax}),
                     step=2)


def _passenger_form(sr: SiteRequest, flight: dict[str, Any], pax: int, values: dict[str, str],
                    errors: dict[str, str], status: int = 200) -> Response:
    rows = []
    me = sr.user()
    for i in range(1, pax + 1):
        name_key, email_key = f"p{i}-name", f"p{i}-email"
        default_name = me["name"] if i == 1 else ""
        default_email = me["email"] if i == 1 else ""
        rows.append({"n": i, "name_id": name_key, "email_id": email_key,
                     "name": values.get(name_key, default_name), "email": values.get(email_key, default_email)})
    add_url = sr.url("/passengers", flight=flight["id"], passengers=pax + 1) if pax < MAX_PASSENGERS else None
    return sr.render("skyway/passengers.html", "Passenger details", code=status, flight=flight_view(flight, pax),
                     passengers=pax, rows=rows, errors=errors, add_url=add_url,
                     back_url=sr.url("/results", **{"from": flight["origin"], "to": flight["destination"],
                                                    "date": flight["date"], "passengers": pax}),
                     step=3)


async def passengers(request: Request) -> Response:
    sr = SiteRequest(request, SITE)
    q = sr.query()
    sr.log("form_submit", q)
    flight = option(sr, "flight", q.get("flight", ""))
    if not flight:
        return sr.not_found("That fare isn't available", "The flight you picked isn't on sale any more. Search again.")
    pax = parse_int(q.get("passengers", "1") or "1", 1, MAX_PASSENGERS) or 1
    return _passenger_form(sr, flight, pax, {}, {})


async def review_post(request: Request) -> Response:
    sr = SiteRequest(request, SITE)
    form = await sr.form()
    sr.log("form_submit", form)
    flight = option(sr, "flight", form.get("flight", ""))
    if not flight:
        return sr.not_found("That fare isn't available", "The flight you picked isn't on sale any more. Search again.")
    pax = parse_int(form.get("passengers", "1") or "1", 1, MAX_PASSENGERS) or 1
    people, errors = collect_people(sr, form, pax, "p", emails_required=1, who="passenger")
    if errors:
        return _passenger_form(sr, flight, pax, form, errors, status=400)
    v = flight_view(flight, pax)
    details = {"travelers": [p["contact_id"] for p in people], "people": people, "date": flight["date"],
               "passengers": pax, "unit_price_usd": flight["price_usd"]}
    summary = (f"{flight['airline']} {flight['flight_number']} {flight['origin']}→{flight['destination']} "
               f"{day_label(flight['date'])} {v['dep_time']}, {v['cabin_label']}, "
               f"{pax} passenger{'s' if pax > 1 else ''}, {money(v['total'])}")
    hold, _ = create_hold(sr, "flight", flight["id"], details, v["total"], summary)
    return sr.redirect(f"/review/{hold['hold_id']}")


def describe(sr: SiteRequest, record: dict[str, Any], hold: dict[str, Any] | None = None) -> dict[str, Any]:
    """Title, facts and price lines for a flight hold or booking (for the shared review, hold and booking
    pages). `record` is the hold or the booking; `hold` is the booking's hold, if it has one."""
    det = (hold or {}).get("details") or record.get("details") or {}
    flight = option(sr, "flight", record.get("option_id", ""))
    names = [p.get("name") for p in det.get("people") or [] if p.get("name")]
    if not names:
        names = [(sr.contact(t) or {}).get("name", t) for t in record.get("travelers") or det.get("travelers") or []]
    count = len(names) or 1
    facts: list[tuple[str, str]] = []
    lines: list[tuple[str, str]] = []
    title = "Flight"
    if flight:
        v = flight_view(flight, count)
        arrive = v["arr_time"] + (f" (next day, {v['arr_day']})" if v["plus_days"] else "")
        title = f"{flight['flight_number']} {flight['origin']} to {flight['destination']}, {day_label(flight['date'])}"
        facts += [("Flight", f"{flight['airline']} {flight['flight_number']}"), ("Date", day_label(flight["date"])),
                  ("Route", v["route"]), ("Departs", v["dep_time"]), ("Arrives", arrive), ("Duration", v["duration"]),
                  ("Cabin", v["cabin_label"])]
        lines.append((f"Fare: {money(flight['price_usd'])} × {count} passenger{'s' if count > 1 else ''}",
                      money(flight["price_usd"] * count)))
    else:
        facts.append(("Flight", (hold or record).get("summary") or record.get("option_id", "")))
    facts.append(("Passengers" if count > 1 else "Passenger", ", ".join(names)))
    return {"title": title, "facts": facts, "lines": lines}


def router() -> Router:
    return Router(routes=[
        Route("/", search, methods=["GET"]),
        Route("/results", results, methods=["GET"]),
        Route("/passengers", passengers, methods=["GET"]),
        Route("/review", review_post, methods=["POST"]),
        *pages.shared_routes(SITE),
    ])

