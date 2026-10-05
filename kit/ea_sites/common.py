"""Shared plumbing for the mock booking sites: run directory, world state, holds, logging, rendering.

Every request re-resolves the run directory (``EA_RUN_DIR``, else ``runs/CURRENT``), so the app or the
runner can switch worlds without restarting the sites. Reads go straight to the JSON files in
``<run_dir>/state/`` (writers replace them atomically); hold creation takes the state lock through
``State.session()``. Page views, form submissions and holds are appended to ``<run_dir>/site.jsonl``.
"""

from __future__ import annotations

import copy
import os
import re
import secrets
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from ea_world import state as world_state
from ea_world.state import State, append_jsonl, file_lock

PORT = 8766
HOLD_MINUTES = 30
HOLD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # uppercase alphanumerics without 0/O and 1/I
TEMPLATES_DIR = Path(__file__).parent / "templates"

SITES: dict[str, dict[str, str]] = {
    "skyway": {"key": "skyway", "name": "Skyway", "kind": "flight", "noun": "flight", "accent": "#1d5bbf",
               "tint": "#e8f0fb", "tagline": "Flights, plainly priced", "host": "skyway.localhost"},
    "stays": {"key": "stays", "name": "Stays", "kind": "hotel", "noun": "hotel stay", "accent": "#0e7466",
              "tint": "#e5f3f1", "tagline": "Rooms near where you need to be", "host": "stays.localhost"},
    "tables": {"key": "tables", "name": "Tables", "kind": "restaurant", "noun": "table", "accent": "#a63d2a",
               "tint": "#f8ebe8", "tagline": "Tables for tonight and later", "host": "tables.localhost"},
}
KIND_SITE = {meta["kind"]: key for key, meta in SITES.items()}
SITE_HOSTS = {meta["host"]: key for key, meta in SITES.items()}

RunDirResolver = Callable[[], "Path | str | None"]


class NoWorld(Exception):
    """No run directory with a `state/` folder is available."""


# ---------------------------------------------------------------- run directory


def default_resolver() -> Path | None:
    env = os.environ.get("EA_RUN_DIR")
    if env:
        return Path(env)
    return world_state.read_current()


def resolve_run_dir(request: Request) -> Path:
    resolver: RunDirResolver = getattr(request.app.state, "run_dir_resolver", None) or default_resolver
    value = resolver()
    if not value:
        raise NoWorld("No run is active (runs/CURRENT is empty and EA_RUN_DIR isn't set).")
    run_dir = Path(value).expanduser()
    if not (run_dir / "state").is_dir():
        raise NoWorld(f"The run directory {run_dir} has no world state yet.")
    return run_dir


# ---------------------------------------------------------------- formatting and parsing


def money(value: Any) -> str:
    try:
        return f"${int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def day_label(value: str | date | None) -> str:
    if not value:
        return ""
    d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return f"{d:%a} {d.day} {d:%b %Y}"


def short_day(value: str | date) -> str:
    d = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return f"{d:%a} {d.day} {d:%b}"


_MONTHS = {m.lower(): i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_WEEKDAYS = re.compile(r"\b(mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)[a-z]*\b\.?", re.I)


def parse_day(value: str | None, default_year: int) -> date | None:
    """Read a date the way a person might type it: 2026-11-02, 11/02/2026, 2 Nov 2026, Nov 2."""
    s = (value or "").strip()
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:T.*)?", s)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return _mkdate(y, mo, d)
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", s)  # US style: month/day[/year]
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else default_year
        return _mkdate(y + 2000 if y < 100 else y, mo, d)
    words = _WEEKDAYS.sub(" ", s.lower()).replace(",", " ")
    words = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", words)
    parts = words.split()
    nums = [p for p in parts if p.isdigit()]
    months = [_MONTHS[p[:3]] for p in parts if p[:3] in _MONTHS and not p.isdigit()]
    if len(months) == 1 and 1 <= len(nums) <= 2 and len(parts) == len(nums) + 1:
        day_n = next((int(n) for n in nums if len(n) <= 2), None)
        year_n = next((int(n) for n in nums if len(n) == 4), default_year)
        if day_n is not None:
            return _mkdate(year_n, months[0], day_n)
    return None


def _mkdate(y: int, mo: int, d: int) -> date | None:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_time(value: str | None) -> str | None:
    """Read 19:00, 7pm, 7:30 PM or 1930 as HH:MM (24-hour)."""
    s = (value or "").strip().lower().replace(".", "")
    if not s:
        return None
    m = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?\s*(am|pm)?", s)
    if not m:
        return None
    h, mi, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        return None
    return f"{h:02d}:{mi:02d}"


def parse_int(value: str | None, lo: int, hi: int) -> int | None:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def place_match(value: str, code: str, city: str) -> bool:
    """`Denver`, `DEN`, `denver (den)` or `Den` all match DEN / Denver."""
    v = " ".join(value.strip().lower().replace("(", " ").replace(")", " ").split())
    if not v:
        return False
    code, city = code.lower(), city.lower()
    if v in (code, city) or code in v.split() or city in v:
        return True
    return len(v) >= 3 and city.startswith(v)


def clean_name(value: str | None) -> str:
    return " ".join((value or "").split())


# ---------------------------------------------------------------- templates

ENV = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=select_autoescape(["html"]),
                  trim_blocks=True, lstrip_blocks=True)
ENV.filters["money"] = money
ENV.filters["day"] = day_label
ENV.filters["short_day"] = short_day


# ---------------------------------------------------------------- per-request helper


class SiteRequest:
    """One request to one site: where its world lives, how to link within it, and its event log."""

    def __init__(self, request: Request, site: str):
        self.request = request
        self.site = site
        self.meta = SITES.get(site, {"key": site, "name": "Intuition booking sites", "kind": "", "accent": "#3b4252",
                                     "tint": "#eceff4", "tagline": "", "host": "localhost"})
        self.base = request.scope.get("root_path", "") or ""  # "" when routed by host, "/skyway" by path
        self.run_dir = resolve_run_dir(request)
        self.st = State(self.run_dir)

    # paths and links --------------------------------------------------------
    @property
    def host_mode(self) -> bool:
        return (self.request.url.hostname or "").lower() in SITE_HOSTS

    @property
    def path(self) -> str:
        full = self.request.url.path
        return full[len(self.base):] or "/" if self.base and full.startswith(self.base) else full

    def url(self, path: str, **params: Any) -> str:
        q = {k: v for k, v in params.items() if v not in (None, "")}
        return f"{self.base}{path}" + (f"?{urlencode(q)}" if q else "")

    def site_url(self, site: str, path: str) -> str:
        """A link to another site, in the same routing style as this request."""
        if site == self.site:
            return self.url(path)
        if self.host_mode:
            u = self.request.url
            port = f":{u.port}" if u.port else ""
            return f"{u.scheme}://{SITES[site]['host']}{port}{path}"
        root = self.request.scope.get("app_root_path", "") or ""
        return f"{root}/{site}{path}"

    # state ----------------------------------------------------------------------
    def load(self, name: str) -> Any:
        return self.st.load(name)

    def now(self) -> datetime:
        return self.st.now()

    def persona(self) -> dict[str, Any]:
        return self.load("persona") or {}

    def contacts(self) -> list[dict[str, Any]]:
        return (self.load("contacts") or {}).get("contacts", [])

    def contact(self, contact_id: str) -> dict[str, Any] | None:
        return next((c for c in self.contacts() if c.get("id") == contact_id), None)

    def user(self) -> dict[str, Any]:
        p = self.persona()
        me = self.contact(p.get("id", "maya")) or {}
        return {"id": p.get("id", "maya"), "name": p.get("name") or me.get("name", ""),
                "email": me.get("email") or p.get("email", ""), "company": p.get("company", "")}

    # request data -----------------------------------------------------------------
    def query(self) -> dict[str, str]:
        return {k: v for k, v in self.request.query_params.items()}

    async def form(self) -> dict[str, str]:
        body = (await self.request.body()).decode("utf-8", errors="replace")
        out: dict[str, str] = {}
        for k, v in parse_qsl(body, keep_blank_values=True):
            out.setdefault(k, v)
        return out

    # logging ---------------------------------------------------------------------
    def log(self, type_: str, data: dict[str, Any] | None = None, path: str | None = None) -> None:
        log_event(self.run_dir, self.site, type_, path or self.path, data or {})

    # responses -------------------------------------------------------------------
    def render(self, template: str, title: str, code: int = 200, **ctx: Any) -> HTMLResponse:
        context = {"site": self.meta, "base": self.base, "title": title, "user": self.user(), "sr": self, **ctx}
        html = ENV.get_template(template).render(**context)
        self.log("page_view", {"title": title, "status": code, "url": str(self.request.url),
                               **({"query": self.query()} if self.request.query_params else {})})
        return HTMLResponse(html, status_code=code)

    def redirect(self, path: str) -> RedirectResponse:
        return RedirectResponse(self.url(path), status_code=303)

    def not_found(self, heading: str, message: str) -> HTMLResponse:
        return self.render("error.html", heading, code=404, heading=heading, message=message)


def log_event(run_dir: Path, site: str, type_: str, path: str, data: dict[str, Any]) -> None:
    record = {"ts": datetime.now().astimezone().isoformat(timespec="milliseconds"), "site": site,
              "type": type_, "path": path, "data": data}
    with file_lock(Path(run_dir) / ".site.lock"):
        append_jsonl(Path(run_dir) / "site.jsonl", record)


# ---------------------------------------------------------------- travellers


def match_person(contacts: list[dict[str, Any]], name: str, email: str) -> tuple[dict[str, Any] | None, str | None]:
    """Find the contact a traveller's name or email belongs to (by email, full name or contact id)."""
    n, e = clean_name(name).lower(), (email or "").strip().lower()
    by_email = next((c for c in contacts if e and (c.get("email") or "").lower() == e), None)
    by_name = next((c for c in contacts if n and n in ((c.get("name") or "").lower(), (c.get("id") or "").lower())), None)
    if by_email and by_name and by_email.get("id") != by_name.get("id"):
        return None, "The name and the email belong to different people."
    found = by_email or by_name
    if not found:
        return None, None
    return found, None


def collect_people(sr: SiteRequest, form: dict[str, str], count: int, prefix: str, emails_required: int,
                   who: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Read `<prefix><i>-name` / `-email` fields and match each person to a contact on the account.
    The first `emails_required` people must give an email."""
    people: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    company = sr.user().get("company") or "the company"
    seen: set[str] = set()
    for i in range(1, count + 1):
        nid, eid = f"{prefix}{i}-name", f"{prefix}{i}-email"
        name, email = clean_name(form.get(nid)), (form.get(eid) or "").strip()
        if not name:
            errors[nid] = f"Enter the full name of {who} {i}."
        if i <= emails_required and not email:
            errors[eid] = f"Enter an email address for {who} {i}."
        elif email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            errors[eid] = f"Enter a real email address for {who} {i}, like name@example.com."
        if nid in errors or eid in errors:
            continue
        contact, problem = match_person(sr.contacts(), name, email)
        if problem:
            errors[nid] = f"{who.capitalize()} {i}: {problem}"
            continue
        if not contact:
            errors[nid] = (f"“{name}” isn't a traveller on the {company} account. "
                           "Use the person's full name or work email.")
            continue
        if contact["id"] in seen:
            errors[nid] = f"{contact['name']} is already listed. Each {who} must be a different person."
            continue
        seen.add(contact["id"])
        people.append({"name": contact.get("name") or name, "email": email or contact.get("email", ""),
                       "contact_id": contact["id"]})
    return people, errors


# ---------------------------------------------------------------- holds


def new_hold_id(existing: set[str]) -> str:
    while True:
        hid = "HOLD-" + "".join(secrets.choice(HOLD_ALPHABET) for _ in range(4))
        if hid not in existing:
            return hid


def create_hold(sr: SiteRequest, kind: str, option_id: str, details: dict[str, Any], total_usd: int,
                summary: str) -> tuple[dict[str, Any], bool]:
    """Write a hold to `state/holds.json` under the state lock. An identical hold that's still held is
    reused (a double-submitted form doesn't make two holds). Returns (hold, created)."""
    with sr.st.session():
        data = sr.st.load("holds") or {"holds": []}
        holds = data.setdefault("holds", [])
        for h in holds:
            if (h.get("status") == "held" and h.get("kind") == kind and h.get("option_id") == option_id
                    and h.get("details") == details and h.get("total_usd") == total_usd):
                return copy.deepcopy(h), False
        now = sr.st.load("clock")["now"]
        expires = (datetime.fromisoformat(now) + timedelta(minutes=HOLD_MINUTES)).isoformat()
        hold = {
            "hold_id": new_hold_id({h.get("hold_id", "") for h in holds}),
            "kind": kind,
            "option_id": option_id,
            "details": details,
            "total_usd": int(total_usd),
            "created_at": now,
            "expires_at": expires,
            "status": "held",
            "booking_id": None,
            "site": sr.site,
            "summary": summary,
        }
        holds.append(hold)
        sr.st.save("holds", data)
    sr.log("hold_created", copy.deepcopy(hold))
    return hold, True


def find_hold(sr: SiteRequest, hold_id: str) -> dict[str, Any] | None:
    hid = (hold_id or "").strip().lower()
    return next((h for h in (sr.load("holds") or {}).get("holds", []) if (h.get("hold_id") or "").lower() == hid), None)


def find_booking(sr: SiteRequest, booking_id: str) -> dict[str, Any] | None:
    bid = (booking_id or "").strip().lower()
    return next((b for b in (sr.load("bookings") or {}).get("bookings", []) if (b.get("id") or "").lower() == bid), None)


def hold_status(sr: SiteRequest, hold: dict[str, Any]) -> str:
    status = hold.get("status", "held")
    if status == "held" and hold.get("expires_at"):
        try:
            if sr.now() > datetime.fromisoformat(hold["expires_at"]):
                return "expired"
        except (ValueError, TypeError):
            pass
    return status


def option(sr: SiteRequest, kind: str, option_id: str) -> dict[str, Any] | None:
    name, key = {"flight": ("flights", "flights"), "hotel": ("hotels", "hotels"),
                 "restaurant": ("restaurants", "restaurants")}[kind]
    return next((o for o in (sr.load(name) or {}).get(key, []) if o.get("id") == option_id), None)
