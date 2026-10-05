"""Pages every site shares: the review page for a hold, the hold status page, the confirmation page."""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from .common import KIND_SITE, SITES, SiteRequest, find_booking, find_hold, hold_status, money


def _describe(sr: SiteRequest, kind: str, record: dict[str, Any], hold: dict[str, Any] | None = None) -> dict[str, Any]:
    from . import skyway, stays, tables  # late import: the site modules import this one

    fn = {"flight": skyway.describe, "hotel": stays.describe, "restaurant": tables.describe}.get(kind)
    if fn is None:
        return {"title": record.get("summary") or record.get("option_id", ""), "facts": [], "lines": []}
    return fn(sr, record, hold)


def _elsewhere(sr: SiteRequest, kind: str, path: str) -> dict[str, str] | None:
    """When a record belongs to another site, where to see it."""
    site = KIND_SITE.get(kind)
    if not site or site == sr.site:
        return None
    return {"name": SITES[site]["name"], "noun": SITES[site]["noun"], "url": sr.site_url(site, path)}


def _hold_page(sr: SiteRequest, hold_id: str, mode: str) -> Response:
    hold = find_hold(sr, hold_id)
    if not hold:
        return sr.not_found("No hold with that reference",
                            f"We can't find a hold with reference {hold_id}. Check the reference and try again.")
    kind = hold.get("kind", "")
    status = hold_status(sr, hold)
    desc = _describe(sr, kind, hold)
    booking_url = sr.url(f"/booking/{hold['booking_id']}") if hold.get("booking_id") else None
    title = (f"Review: hold {hold['hold_id']}" if mode == "review" else f"Hold {hold['hold_id']}")
    return sr.render("hold.html", title, mode=mode, hold=hold, status=status, desc=desc, total=money(hold.get("total_usd")),
                     booking_url=booking_url, elsewhere=_elsewhere(sr, kind, f"/{mode if mode == 'review' else 'holds'}/{hold['hold_id']}"),
                     hold_url=sr.url(f"/holds/{hold['hold_id']}"), step=4 if mode == "review" else None)


async def review_get(request: Request) -> Response:
    sr = SiteRequest(request, request.scope["ea_site"])
    return _hold_page(sr, request.path_params["hold_id"], "review")


async def hold_get(request: Request) -> Response:
    sr = SiteRequest(request, request.scope["ea_site"])
    return _hold_page(sr, request.path_params["hold_id"], "hold")


async def booking_get(request: Request) -> Response:
    sr = SiteRequest(request, request.scope["ea_site"])
    ref = request.path_params["booking_id"].strip()
    if ref.upper().startswith("HOLD-"):
        hold = find_hold(sr, ref)
        if hold and hold.get("booking_id"):
            site = KIND_SITE.get(hold.get("kind", ""), sr.site)
            return RedirectResponse(sr.site_url(site, f"/booking/{hold['booking_id']}"), status_code=303)
        if hold:
            return sr.render("booking.html", f"Booking {hold['hold_id']}: not confirmed", code=404, ref=hold["hold_id"],
                             confirmed=False, reason=(f"{hold['hold_id']} is a hold, not a booking. It hasn't been "
                                                      "confirmed yet; your assistant confirms it."),
                             hold_url=sr.url(f"/holds/{hold['hold_id']}"), desc=None, booking=None, elsewhere=None)
    booking = find_booking(sr, ref)
    if not booking:
        return sr.render("booking.html", f"Booking {ref}: not confirmed", code=404, ref=ref, confirmed=False,
                         reason=f"We can't find a confirmed booking with reference {ref}.", desc=None, booking=None,
                         hold_url=None, elsewhere=None)
    kind = booking.get("kind", "")
    elsewhere = _elsewhere(sr, kind, f"/booking/{booking['id']}")
    hold = find_hold(sr, booking["hold_id"]) if booking.get("hold_id") else None
    if hold is None:
        hold = next((h for h in (sr.load("holds") or {}).get("holds", []) if h.get("booking_id") == booking["id"]), None)
    confirmed = booking.get("status") == "active" and (hold is None or hold.get("status") == "confirmed")
    reason = None
    if booking.get("status") == "cancelled":
        reason = f"Booking {booking['id']} was cancelled."
    elif not confirmed:
        reason = f"Booking {booking['id']} isn't confirmed."
    desc = _describe(sr, kind, booking, hold)
    title = f"Booking {booking['id']}: " + ("confirmed" if confirmed else "not confirmed")
    return sr.render("booking.html", title, ref=booking["id"], booking=booking, confirmed=confirmed and not elsewhere,
                     reason=reason, desc=desc, total=money(booking.get("total_usd")), hold=hold, elsewhere=elsewhere,
                     hold_url=sr.url(f"/holds/{hold['hold_id']}") if hold else None)


async def not_found(request: Request) -> Response:
    sr = SiteRequest(request, request.scope["ea_site"])
    return sr.not_found("Page not found", "There's no page at this address.")


def _tag(site: str, endpoint: Any) -> Any:
    async def handler(request: Request) -> Response:
        request.scope["ea_site"] = site
        return await endpoint(request)

    handler.__name__ = endpoint.__name__
    return handler


def shared_routes(site: str) -> list[Route]:
    """Routes that go last in every site's router (the catch-all 404 must stay at the end)."""
    return [
        Route("/review/{hold_id}", _tag(site, review_get), methods=["GET"]),
        Route("/holds/{hold_id}", _tag(site, hold_get), methods=["GET"]),
        Route("/booking/{booking_id}", _tag(site, booking_get), methods=["GET"]),
        Route("/{path:path}", _tag(site, not_found), methods=["GET", "POST"]),
    ]
