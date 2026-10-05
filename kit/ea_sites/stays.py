"""Stays: hotel search → hotels → guest details → review (creates a hold)."""

from __future__ import annotations

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
)

SITE = "stays"
MAX_ROOMS = 4


def _hotels(sr: SiteRequest) -> list[dict[str, Any]]:
    return (sr.load("hotels") or {}).get("hotels", [])


def hotel_view(h: dict[str, Any], nights: int, rooms: int) -> dict[str, Any]:
    return {**h, "nights": nights, "rooms": rooms, "stay_total": h["price_usd"] * nights * rooms}


def _dates(sr: SiteRequest, q: dict[str, str], errors: dict[str, str]) -> tuple[str | None, str | None, int]:
    year = sr.now().year
    ci, co = parse_day(q.get("check_in"), year), parse_day(q.get("check_out"), year)
    if ci is None:
        errors["check_in"] = "Enter the check-in date as YYYY-MM-DD, for example 2026-11-02."
    if co is None:
        errors["check_out"] = "Enter the check-out date as YYYY-MM-DD, for example 2026-11-04."
    if ci and co and co <= ci:
        errors["check_out"] = "Check-out must be after check-in."
    if ci and co and (co - ci).days > 30:
        errors["check_out"] = "Stays can be up to 30 nights."
    nights = (co - ci).days if ci and co else 0
    return (ci.isoformat() if ci else None), (co.isoformat() if co else None), nights


def _search_form(sr: SiteRequest, q: dict[str, str], errors: dict[str, str], status: int = 200) -> Response:
    form = {"city": q.get("city", ""), "check_in": q.get("check_in", ""), "check_out": q.get("check_out", ""),
            "rooms": q.get("rooms", "1")}
    return sr.render("stays/search.html", "Find a hotel", code=status, form=form, errors=errors,
                     max_rooms=MAX_ROOMS, step=1)


async def search(request: Request) -> Response:
    sr = SiteRequest(request, SITE)
    return _search_form(sr, sr.query(), {})


async def results(request: Request) -> Response:
    sr = SiteRequest(request, SITE)
    q = sr.query()
    sr.log("form_submit", q)
    errors: dict[str, str] = {}
    city = clean_name(q.get("city"))
    if not city:
        errors["city"] = "Enter a city, for example Chicago."
    ci, co, nights = _dates(sr, q, errors)
    rooms = parse_int(q.get("rooms", "1") or "1", 1, MAX_ROOMS)
    if rooms is None:
        errors["rooms"] = f"Choose between 1 and {MAX_ROOMS} rooms."
    if errors:
        return _search_form(sr, q, errors, status=400)
    assert rooms is not None
    found = sorted((h for h in _hotels(sr) if city.lower() in (h.get("city") or "").lower()), key=lambda h: h["price_usd"])
    cities = sorted({h.get("city", "") for h in _hotels(sr)} - {""})
    shown_city = found[0]["city"] if found else city
    return sr.render("stays/results.html", f"Hotels in {shown_city}", city=shown_city, check_in=ci, check_out=co,
                     nights=nights, rooms=rooms, hotels=[hotel_view(h, nights, rooms) for h in found], cities=cities,
                     change_url=sr.url("/", city=city, check_in=ci, check_out=co, rooms=rooms), step=2)


def _guest_form(sr: SiteRequest, hotel: dict[str, Any], ci: str, co: str, nights: int, rooms: int,
                values: dict[str, str], errors: dict[str, str], status: int = 200) -> Response:
    me = sr.user()
    rows = []
    for i in range(1, rooms + 1):
        name_key, email_key = f"g{i}-name", f"g{i}-email"
        rows.append({"n": i, "name_id": name_key, "email_id": email_key,
                     "name": values.get(name_key, me["name"] if i == 1 else ""),
                     "email": values.get(email_key, me["email"] if i == 1 else "")})
    add_url = (sr.url("/guests", hotel=hotel["id"], check_in=ci, check_out=co, rooms=rooms + 1)
               if rooms < MAX_ROOMS else None)
    return sr.render("stays/guests.html", "Guest details", code=status, hotel=hotel_view(hotel, nights, rooms),
                     check_in=ci, check_out=co, nights=nights, rooms=rooms, rows=rows, errors=errors, add_url=add_url,
                     back_url=sr.url("/results", city=hotel.get("city"), check_in=ci, check_out=co, rooms=rooms), step=3)


def _selection(sr: SiteRequest, data: dict[str, str]) -> tuple[dict[str, Any] | None, str | None, str | None, int, int, dict[str, str]]:
    errors: dict[str, str] = {}
    hotel = option(sr, "hotel", data.get("hotel", ""))
    ci, co, nights = _dates(sr, data, errors)
    rooms = parse_int(data.get("rooms", "1") or "1", 1, MAX_ROOMS) or 1
    return hotel, ci, co, nights, rooms, errors


async def guests(request: Request) -> Response:
    sr = SiteRequest(request, SITE)
    q = sr.query()
    sr.log("form_submit", q)
    hotel, ci, co, nights, rooms, errors = _selection(sr, q)
    if not hotel:
        return sr.not_found("That hotel isn't available", "The hotel you picked has no rooms left. Search again.")
    if errors or not ci or not co:
        return sr.render("error.html", "Check your dates", code=400, heading="Check your dates",
                         message=" ".join(errors.values()) or "Search again with your dates.")
    return _guest_form(sr, hotel, ci, co, nights, rooms, {}, {})


async def review_post(request: Request) -> Response:
    sr = SiteRequest(request, SITE)
    form = await sr.form()
    sr.log("form_submit", form)
    hotel, ci, co, nights, rooms, errors = _selection(sr, form)
    if not hotel:
        return sr.not_found("That hotel isn't available", "The hotel you picked has no rooms left. Search again.")
    if errors or not ci or not co:
        return sr.render("error.html", "Check your dates", code=400, heading="Check your dates",
                         message=" ".join(errors.values()) or "Search again with your dates.")
    people, errors = collect_people(sr, form, rooms, "g", emails_required=0, who="guest")
    if errors:
        return _guest_form(sr, hotel, ci, co, nights, rooms, form, errors, status=400)
    total = hotel["price_usd"] * nights * rooms
    details = {"travelers": [p["contact_id"] for p in people], "people": people, "check_in": ci, "check_out": co,
               "nights": nights, "rooms": rooms, "unit_price_usd": hotel["price_usd"]}
    summary = (f"{hotel['name']}, {day_label(ci)} to {day_label(co)}, {nights} night{'s' if nights != 1 else ''}, "
               f"{rooms} room{'s' if rooms != 1 else ''}, {money(total)}")
    hold, _ = create_hold(sr, "hotel", hotel["id"], details, total, summary)
    return sr.redirect(f"/review/{hold['hold_id']}")


def describe(sr: SiteRequest, record: dict[str, Any], hold: dict[str, Any] | None = None) -> dict[str, Any]:
    det = (hold or {}).get("details") or record.get("details") or {}
    hotel = option(sr, "hotel", record.get("option_id", ""))
    ci, co = record.get("check_in") or det.get("check_in"), record.get("check_out") or det.get("check_out")
    nights = record.get("nights") or det.get("nights") or 0
    rooms = det.get("rooms") or record.get("rooms") or len(record.get("travelers") or det.get("travelers") or []) or 1
    names = [p.get("name") for p in det.get("people") or [] if p.get("name")]
    if not names:
        names = [(sr.contact(t) or {}).get("name", t) for t in record.get("travelers") or det.get("travelers") or []]
    facts: list[tuple[str, str]] = []
    lines: list[tuple[str, str]] = []
    title = "Hotel stay"
    if hotel:
        title = f"{hotel['name']}, {day_label(ci)} to {day_label(co)}"
        facts += [("Hotel", hotel["name"]), ("Address", hotel.get("address", "")),
                  ("Distance", f"{hotel.get('distance_miles')} miles to {hotel.get('distance_to', 'the centre')}")]
        if hotel.get("block_code"):
            facts.append(("Group rate code", hotel["block_code"]))
        unit = hotel["price_usd"]
    else:
        facts.append(("Hotel", (hold or record).get("summary") or record.get("option_id", "")))
        unit = det.get("unit_price_usd") or record.get("unit_price_usd") or 0
    facts += [("Check-in", day_label(ci)), ("Check-out", day_label(co)),
              ("Nights", str(nights)), ("Rooms", str(rooms)),
              ("Guests" if len(names) > 1 else "Guest", ", ".join(names))]
    lines.append((f"{money(unit)} a night × {nights} night{'s' if nights != 1 else ''} × {rooms} "
                  f"room{'s' if rooms != 1 else ''} (taxes included)", money(unit * nights * rooms)))
    return {"title": title, "facts": facts, "lines": lines}


def router() -> Router:
    return Router(routes=[
        Route("/", search, methods=["GET"]),
        Route("/results", results, methods=["GET"]),
        Route("/guests", guests, methods=["GET"]),
        Route("/review", review_post, methods=["POST"]),
        *pages.shared_routes(SITE),
    ])
