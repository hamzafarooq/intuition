"""Travel and restaurant tools: search, book (directly or from a hold made on the mock sites), cancel."""

from datetime import date
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import core, feedback, verifiers
from .common import contacts, next_id
from .core import tool
from .rules import contacts_by_id


def _day(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise ToolError(f"Can't read {field} '{value}'. Use YYYY-MM-DD.") from exc


def _place_match(value: str | None, code: str, city: str) -> bool:
    if not value:
        return True
    v = value.strip().lower()
    return v in (code.lower(), city.lower()) or v in city.lower() or code.lower() in v or city.lower() in v


def flights() -> list[dict[str, Any]]:
    return core.st().load("flights").get("flights", [])


def hotels() -> list[dict[str, Any]]:
    return core.st().load("hotels").get("hotels", [])


def restaurants() -> list[dict[str, Any]]:
    return core.st().load("restaurants").get("restaurants", [])


def find_option(option_id: str) -> tuple[str, dict[str, Any]]:
    for f in flights():
        if f["id"] == option_id:
            return "flight", f
    for h in hotels():
        if h["id"] == option_id:
            return "hotel", h
    for r in restaurants():
        if r["id"] == option_id:
            return "restaurant", r
    raise ToolError(f"Unknown option id: {option_id}")


@tool("travel")
def travel_search(
    kind: Annotated[Literal["flight", "hotel"], Field(description="flight or hotel")],
    origin: Annotated[str | None, Field(description="Flights: departure city or airport code")] = None,
    destination: Annotated[str | None, Field(description="Flights: arrival city or airport code")] = None,
    date: Annotated[str | None, Field(description="Flights: departure date YYYY-MM-DD")] = None,
    city: Annotated[str | None, Field(description="Hotels: city")] = None,
    check_in: Annotated[str | None, Field(description="Hotels: check-in date YYYY-MM-DD")] = None,
    check_out: Annotated[str | None, Field(description="Hotels: check-out date YYYY-MM-DD")] = None,
) -> dict[str, Any]:
    """Search flights or hotels. Prices are in US dollars; hotel prices are per night including taxes."""
    if kind == "flight":
        d = _day(date, "date")
        out = [
            f
            for f in flights()
            if (d is None or f["date"] == d.isoformat())
            and _place_match(origin, f["origin"], f.get("origin_city", ""))
            and _place_match(destination, f["destination"], f.get("destination_city", ""))
        ]
        out.sort(key=lambda f: (f["date"], f["depart"]))
        return {"kind": "flight", "options": out}
    ci, co = _day(check_in, "check_in"), _day(check_out, "check_out")
    nights = (co - ci).days if ci and co else None
    if nights is not None and nights <= 0:
        raise ToolError("check_out must be after check_in.")
    out = []
    for h in hotels():
        if city and city.strip().lower() not in h.get("city", "").lower():
            continue
        item = dict(h)
        if nights:
            item["nights"] = nights
            item["total_usd"] = h["price_usd"] * nights
        out.append(item)
    out.sort(key=lambda h: h["price_usd"])
    return {"kind": "hotel", "options": out}


@tool("travel")
def restaurant_search(
    city: Annotated[str, Field(description="City")],
    date: Annotated[str, Field(description="Date YYYY-MM-DD")],
    time: Annotated[str, Field(description="Preferred time HH:MM (24-hour)")],
    party_size: Annotated[int, Field(description="Number of people", ge=1, le=20)],
) -> dict[str, Any]:
    """Find restaurant tables. Returns available times, the largest party each can seat, and the
    estimated cost a person including tip."""
    d = _day(date, "date")
    out = []
    for r in restaurants():
        if city.strip().lower() not in r.get("city", "").lower():
            continue
        if d and r.get("date") != d.isoformat():
            continue
        item = dict(r)
        item["can_seat_party"] = party_size <= r.get("max_party", 0)
        item["requested_time_available"] = time in r.get("times", [])
        item["estimated_total_usd"] = r.get("est_cost_pp", 0) * party_size
        out.append(item)
    return {"options": out, "party_size": party_size, "time": time}


def _travelers(values: list[str] | None) -> list[str]:
    people = contacts_by_id(contacts())
    out = []
    for v in values or ["maya"]:
        if v not in people:
            raise ToolError(f"Unknown traveller: {v}. Use contact ids such as 'maya'.")
        if v not in out:
            out.append(v)
    return out


def _holds() -> dict[str, Any]:
    return core.st().load("holds")


def _take_hold(hold_id: str, kind: str | tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Any]]:
    holds = _holds()
    for h in holds.get("holds", []):
        if h["hold_id"].lower() == hold_id.strip().lower():
            kinds = (kind,) if isinstance(kind, str) else kind
            if h["kind"] not in kinds:
                raise ToolError(f"Hold {hold_id} is a {h['kind']} hold; use the matching booking tool.")
            if h["status"] != "held":
                raise ToolError(f"Hold {hold_id} is {h['status']}.")
            return holds, h
    raise ToolError(f"Unknown hold id: {hold_id}")


def _finish_booking(booking: dict[str, Any]) -> dict[str, Any]:
    s = core.st()
    data = s.load("bookings")
    data.setdefault("bookings", []).append(booking)
    s.save("bookings", data)
    c = core.ctx()
    c.object_id = booking["id"]
    c.effect("booking_created", booking["id"])
    problems = verifiers.booking_problems(booking["id"])
    feedback.on_booking(booking, problems)
    return {"booking_id": booking["id"], "kind": booking["kind"], "option_id": booking["option_id"], "total_usd": booking["total_usd"]}


def _seq_booking_id() -> str:
    existing = {b["id"] for b in core.st().load("bookings").get("bookings", [])}
    n = 1
    while f"bk-{n:03d}" in existing:
        n += 1
    return f"bk-{n:03d}"


@tool("travel", write=True)
def travel_book(
    option_id: Annotated[str | None, Field(description="A flight or hotel option id from travel_search (direct mode)")] = None,
    hold_id: Annotated[str | None, Field(description="A hold made on the booking site (browser mode), e.g. HOLD-7K2P")] = None,
    travelers: Annotated[list[str], Field(description="Contact ids of the travellers")] = ["maya"],  # noqa: B006
    check_in: Annotated[str | None, Field(description="Hotels: check-in date YYYY-MM-DD")] = None,
    check_out: Annotated[str | None, Field(description="Hotels: check-out date YYYY-MM-DD")] = None,
    approval_note: Annotated[str | None, Field(description="Who approved a policy exception, if one was needed")] = None,
    idempotency_key: Annotated[str | None, Field(description="Optional key; a repeat call with the same key does nothing new")] = None,
) -> dict[str, Any]:
    """Book a flight or hotel, or confirm a hold made on the booking site. Only after Maya has
    explicitly said yes to this booking and its price."""
    if bool(option_id) == bool(hold_id):
        raise ToolError("Give either option_id (direct booking) or hold_id (a hold from the booking site).")
    s = core.st()
    now = s.load("clock")["now"]
    booking: dict[str, Any] = {
        "id": _seq_booking_id(),
        "hold_id": None,
        "guests": [],
        "status": "active",
        "created_at": now,
        "approval_note": approval_note,
    }
    if hold_id:
        holds, h = _take_hold(hold_id, ("flight", "hotel"))
        det = h.get("details", {})
        kind, opt = find_option(h["option_id"])
        booking.update(kind=kind, option_id=h["option_id"], hold_id=h["hold_id"], travelers=_travelers(det.get("travelers")),
                       total_usd=int(h["total_usd"]))
        if kind == "hotel":
            ci, co = _day(det.get("check_in"), "check_in"), _day(det.get("check_out"), "check_out")
            booking.update(check_in=ci.isoformat() if ci else None, check_out=co.isoformat() if co else None,
                           nights=(co - ci).days if ci and co else None, unit_price_usd=opt["price_usd"])
        else:
            booking.update(date=opt["date"], unit_price_usd=opt["price_usd"])
        h["status"] = "confirmed"
        h["booking_id"] = booking["id"]
        s.save("holds", holds)
        return _finish_booking(booking)
    kind, opt = find_option(option_id or "")
    if kind == "restaurant":
        raise ToolError("Use restaurant_book for restaurants.")
    trav = _travelers(travelers)
    booking.update(kind=kind, option_id=opt["id"], travelers=trav, unit_price_usd=opt["price_usd"])
    if kind == "flight":
        booking.update(date=opt["date"], total_usd=opt["price_usd"] * len(trav))
    else:
        ci, co = _day(check_in, "check_in"), _day(check_out, "check_out")
        if not ci or not co:
            raise ToolError("Hotels need check_in and check_out dates.")
        nights = (co - ci).days
        if nights <= 0:
            raise ToolError("check_out must be after check_in.")
        booking.update(check_in=ci.isoformat(), check_out=co.isoformat(), nights=nights, rooms=len(trav),
                       total_usd=opt["price_usd"] * nights * len(trav))
    return _finish_booking(booking)


@tool("travel", write=True)
def travel_cancel(
    booking_id: Annotated[str, Field(description="The booking to cancel")],
    idempotency_key: Annotated[str | None, Field(description="Optional key; a repeat call with the same key does nothing new")] = None,
) -> dict[str, Any]:
    """Cancel a flight, hotel or restaurant booking."""
    s = core.st()
    data = s.load("bookings")
    for b in data.get("bookings", []):
        if b["id"] == booking_id:
            if b["status"] == "cancelled":
                raise ToolError(f"Booking {booking_id} is already cancelled.")
            b["status"] = "cancelled"
            s.save("bookings", data)
            c = core.ctx()
            c.object_id = booking_id
            c.effect("booking_cancelled", booking_id)
            return {"booking_id": booking_id, "status": "cancelled"}
    raise ToolError(f"Unknown booking id: {booking_id}")


@tool("travel", write=True)
def restaurant_book(
    option_id: Annotated[str | None, Field(description="Restaurant option id from restaurant_search (direct mode)")] = None,
    date: Annotated[str | None, Field(description="Date YYYY-MM-DD (direct mode)")] = None,
    time: Annotated[str | None, Field(description="Time HH:MM, one of the available times (direct mode)")] = None,
    party_size: Annotated[int | None, Field(description="Number of people (direct mode)")] = None,
    guests: Annotated[list[str], Field(description="Contact ids of the guests, if known")] = [],  # noqa: B006
    hold_id: Annotated[str | None, Field(description="A table hold made on the booking site (browser mode)")] = None,
    idempotency_key: Annotated[str | None, Field(description="Optional key; a repeat call with the same key does nothing new")] = None,
) -> dict[str, Any]:
    """Book a restaurant table, or confirm a table hold from the booking site. Only after Maya has
    explicitly said yes."""
    if bool(option_id) == bool(hold_id):
        raise ToolError("Give either option_id with date, time and party_size, or hold_id.")
    s = core.st()
    booking: dict[str, Any] = {"id": _seq_booking_id(), "kind": "restaurant", "status": "active",
                               "created_at": s.load("clock")["now"], "travelers": [], "approval_note": None}
    if hold_id:
        holds, h = _take_hold(hold_id, "restaurant")
        det = h.get("details", {})
        _, opt = find_option(h["option_id"])
        booking.update(option_id=h["option_id"], hold_id=h["hold_id"], date=det.get("date"), time=det.get("time"),
                       party_size=det.get("party_size"), guests=det.get("guests", []), unit_price_usd=opt.get("est_cost_pp"),
                       total_usd=int(h["total_usd"]))
        h["status"] = "confirmed"
        h["booking_id"] = booking["id"]
        s.save("holds", holds)
        return _finish_booking(booking)
    kind, opt = find_option(option_id or "")
    if kind != "restaurant":
        raise ToolError("Use travel_book for flights and hotels.")
    if not (date and time and party_size):
        raise ToolError("Direct restaurant bookings need date, time and party_size.")
    if opt.get("date") != _day(date, "date").isoformat():  # type: ignore[union-attr]
        raise ToolError(f"{opt['name']} has no tables on {date}.")
    if time not in opt.get("times", []):
        raise ToolError(f"{opt['name']} has no table at {time}. Available: {', '.join(opt.get('times', []))}.")
    if party_size > opt.get("max_party", 0):
        raise ToolError(f"{opt['name']} seats at most {opt.get('max_party')}.")
    booking.update(option_id=opt["id"], hold_id=None, date=opt["date"], time=time, party_size=party_size,
                   guests=list(guests or []), unit_price_usd=opt.get("est_cost_pp"), total_usd=opt.get("est_cost_pp", 0) * party_size)
    return _finish_booking(booking)


@tool("travel")
def holds_list() -> dict[str, Any]:
    """Holds created on the booking sites in this session (browser mode), with their status."""
    return {"holds": _holds().get("holds", [])}
