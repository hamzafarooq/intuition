"""Real-site search demo (instructor only): what the browser may do on real travel sites.

This file is the one place to read or change the demo's limits. The browser guard
(``python -m ea_harness.browser_guard --real-sites``) enforces them; the overlay, the app and
``make demo-real`` only switch the mode on.

**Off by default, everywhere.** It is on only when an instructor starts the app with
``EA_REAL_SITES=on`` (``make demo-real``, or ``ea-start --real-sites``), and only for Claude Code
sessions with "Book through the browser" on. The eval runner refuses it (``browser=real``), so evals
stay deterministic. Students' default path never sees it.

**What it allows:** open the sites in ``SITES``, type search terms (places, dates, the number of
travellers), and read results and prices. Real sites give **quotes only**: holds and bookings still
happen on the workshop's mock sites (Skyway, Stays, Tables) and through the gated booking tools after
Sarah approves. The mock sites stay allowed exactly as in ordinary browser mode.

**What the guard blocks on real pages** (each block returns a message to the model and is logged to
``browser.jsonl``, which the adapter merges into the trace as a ``browser_step`` "blocked"):

1. any address that isn't on ``SITES`` (and isn't a mock booking site);
2. addresses that look like sign-in, account, cart, checkout, payment or booking pages
   (``BLOCKED_PATH_PARTS`` in the path, ``BLOCKED_WORDS`` anywhere in the path, query or fragment);
3. filling a field whose label looks like personal data (``PERSONAL_FIELD``), or typing a value that
   looks like personal data: an email address, a phone or card number, or the name or email of anyone
   in Sarah's world (``personal_value``);
4. clicking anything whose text suggests committing: Book, Reserve, Sign in, Checkout, Continue to
   payment, Select and continue ... (``COMMIT_TEXT``), or a link to a blocked address;
5. any click, fill or key press while some tab sits on a page that isn't allowed (it got there by a
   click or a redirect): the model must go back or close it first;
6. key presses other than navigation keys (``SAFE_KEYS``), and browser tools outside ``REAL_TOOLS``.

Real sites change their layout and may show bot checks or captchas. The assistant is told not to get
around them; the guard has no bypass either.

The browser for this mode is a clean, throwaway one: a fresh profile in a temporary folder, on its own
debugging port (``PORT``), deleted when the app stops. It never uses your own browser profile, cookies
or saved logins (``ea_harness/clean_browser.py``).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

ENV_SWITCH = "EA_REAL_SITES"
LABEL = "Real-site search (instructor demo)"
PORT = 9223  # the clean browser's debugging port; ordinary browser mode uses 9222


def requested(env: dict[str, str] | os._Environ[str] | None = None) -> bool:
    """True when the instructor switch is set (``EA_REAL_SITES=on``)."""
    value = (env if env is not None else os.environ).get(ENV_SWITCH, "")
    return value.strip().lower() in ("1", "on", "true", "yes")


# ---------------------------------------------------------------- the allowlist


@dataclass(frozen=True)
class Site:
    name: str
    hosts: tuple[str, ...]
    paths: tuple[str, ...]  # allowed path prefixes; "/" is the whole site (the blocked patterns still apply)
    start: str  # where a search begins


SITES: tuple[Site, ...] = (
    Site("Expedia", ("www.expedia.com", "expedia.com"), ("/",), "https://www.expedia.com/Flights"),
    Site("Google Flights and Hotels", ("www.google.com", "google.com"), ("/travel/",),
         "https://www.google.com/travel/flights"),
    # Kayak is not on by default. To add it, uncomment (and add it to the travel agent's real-site text):
    # Site("Kayak", ("www.kayak.com", "kayak.com"), ("/flights", "/hotels"), "https://www.kayak.com/flights"),
)

# Browser tools the assistant may call in this mode (the same set as ordinary browser mode). The overlay
# turns these into exact permission rules; every other tool of the browser server is denied.
REAL_TOOLS: tuple[str, ...] = (
    "new_page", "navigate_page", "select_page", "list_pages", "take_snapshot", "click", "fill", "fill_form",
    "press_key", "hover", "wait_for", "take_screenshot", "close_page",
)
# chrome-devtools-mcp tools (1.10.1, plus a few behind flags) that this mode denies outright.
DENIED_TOOLS: tuple[str, ...] = (
    "evaluate_script", "upload_file", "handle_dialog", "emulate", "type_text", "click_at", "drag", "resize_page",
    "get_console_message", "list_console_messages", "get_css_styles", "get_network_request",
    "list_network_requests", "take_heapsnapshot", "lighthouse_audit", "performance_start_trace",
    "performance_stop_trace", "performance_analyze_insight", "screencast_start", "screencast_stop",
)

# Keys press_key may send. Text goes in with fill, where the guard can check it.
SAFE_KEYS = frozenset({
    "Enter", "Tab", "Shift+Tab", "Escape", "Backspace", "Delete", "Home", "End", "PageUp", "PageDown",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Shift+ArrowUp", "Shift+ArrowDown",
})
SUBMIT_KEYS = frozenset({"Enter", "Space", " "})

# Browser-internal pages a fresh profile may show; not real sites, never blocked.
IGNORED_PAGES = ("about:blank", "chrome://newtab", "chrome://new-tab-page", "brave://newtab", "brave://new-tab-page",
                 "chrome://welcome", "brave://welcome", "chrome-error://")

# ---------------------------------------------------------------- blocked addresses

# Substrings of the (lower-cased, decoded) path.
BLOCKED_PATH_PARTS: tuple[str, ...] = (
    # sign-in and accounts
    "login", "log-in", "logon", "logout", "signin", "sign-in", "signup", "sign-up", "register", "account",
    "profile", "password", "oauth", "/auth", "/user", "/member", "membership", "loyalty", "rewards", "wallet",
    "/trips", "mytrips",
    # cart, checkout, payment and booking
    "checkout", "/cart", "basket", "payment", "/pay", "billing", "purchase", "booking", "/book", "reserv",
    "itinerar", "trip-details", "tripdetails", "traveler-info", "travelerinfo", "traveller", "passenger",
    "confirmation",
)
# Whole words anywhere in the path, the query values or the fragment.
BLOCKED_WORDS = frozenset({
    "book", "booking", "bookings", "reserve", "reservation", "reservations", "checkout", "cart", "basket",
    "pay", "payment", "payments", "purchase", "signin", "login", "logon", "signup", "register", "account",
    "accounts",
})

# ---------------------------------------------------------------- blocked clicks


def _rx(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.I) for p in patterns)


# Text of an element (its accessible name) that suggests booking, signing in or paying.
COMMIT_TEXT = _rx(
    r"\bbook(?:ing|ed|s)?\b", r"\breserv(?:e|ed|ation|ations)\b", r"\bhold\b",
    r"\bsign[\s-]?(?:in|up|on)\b", r"\blog[\s-]?(?:in|on)\b", r"\bregister\b", r"\bcreate (?:an |your )?account\b",
    r"\baccount\b", r"\bmy trips\b", r"\bcontinue with (?:google|apple|facebook|email)\b", r"\bmember prices\b",
    r"\bcheckout\b", r"\b(?:proceed|continue|go) to check[\s-]?out\b", r"\bcart\b", r"\bbasket\b",
    r"\badd to (?:trip|cart|basket)\b", r"\bpay(?:ment)?\b", r"\bpurchase\b", r"\bbuy\b",
    r"\bplace (?:your |the )?order\b", r"\bconfirm\b", r"\bcomplete (?:booking|purchase|reservation|your)\b",
    r"\bselect and continue\b", r"^\s*continue\b", r"\bcontinue to (?:traveller|traveler|passenger|guest|payment|checkout|book|review|trip)",
    r"\b(?:traveller|traveler|passenger|guest) (?:details|information|info)\b",
    r"\bselect (?:this )?(?:flight|fare|room)\b", r"\bchoose (?:this )?(?:fare|room)\b",
    r"\btrack prices\b", r"\bprice alerts?\b", r"\bsubscribe\b",
)

# ---------------------------------------------------------------- blocked fills

# A field's label (accessible name or description) that asks for personal data.
PERSONAL_FIELD = _rx(
    r"\be-?mail\b", r"\bpass(?:word|code)\b", r"\bpin\b", r"\b(?:one[- ]time|verification|security) code\b",
    r"\b(?:first|last|full|middle|given|family|sur)\s*name\b", r"^\s*name\s*\*?\s*$",
    r"\b(?:your|traveller'?s?|traveler'?s?|passenger'?s?|guest'?s?|card ?holder'?s?) name\b",
    r"\b(?:phone|mobile|telephone|cell)\b", r"\b(?:card|credit|debit|cvv|cvc|csc)\b", r"\bexpir", r"\bbilling\b",
    r"\b(?:street|home|mailing|postal|email) address\b", r"\baddress line\b", r"^\s*address\s*\*?\s*$",
    r"\b(?:zip|postal) code\b", r"\bpostcode\b", r"\b(?:date of birth|birth ?date|dob|birthday)\b", r"\bpassport\b",
    r"\bknown travell?er\b", r"\bredress\b", r"\bsocial security\b", r"\bssn\b", r"\bnational id\b", r"\bgender\b",
    r"\bloyalty\b", r"\bfrequent fl(?:y|i)er\b", r"\brewards?\b", r"\bmember(?:ship)? (?:id|number|no)\b",
    r"\bmileage\b", r"\bgift card\b",
)
EMAIL_VALUE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
DATE_LIKE = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b")
PHONE_VALUE = re.compile(r"\+?\d[\d\s().-]{5,}\d")
CARD_VALUE = re.compile(r"(?:\d[ -]?){12,18}\d")


# ---------------------------------------------------------------- checks


def page_ignored(url: str) -> bool:
    return any(str(url).lower().startswith(p) for p in IGNORED_PAGES)


def _path_allowed(site: Site, path: str) -> bool:
    return any(p == "/" or path == p.rstrip("/") or path.startswith(p) for p in site.paths)


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if w}


def find_site(url: Any) -> Site | None:
    """The allowlisted site a URL belongs to (syntax and host only), else None."""
    if not isinstance(url, str):
        return None
    u = url.strip()
    if not u or len(u) > 4096 or "\\" in u or any(ch.isspace() or not (0x21 <= ord(ch) <= 0x7E) for ch in u):
        return None
    try:
        parts = urlsplit(u)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or "@" in parts.netloc or port not in (None, 443):
        return None
    host = (parts.hostname or "").lower()
    return next((s for s in SITES if host in s.hosts), None)


def check_url(url: Any) -> str | None:
    """None if the demo may open this real-site address; otherwise why it's blocked (for the model)."""
    site = find_site(url)
    if site is None:
        return "it isn't one of the demo's real sites (" + ", ".join(s.name for s in SITES) + ")"
    parts = urlsplit(str(url).strip())
    path = unquote(parts.path or "/").lower()
    if any(seg in (".", "..") for seg in path.split("/")):
        return "it isn't one of the demo's real sites"
    if not _path_allowed(site, path):
        return f"only {' and '.join(site.paths)} on {site.name} is allowed"
    if any(p in path for p in BLOCKED_PATH_PARTS) or _words(path) & BLOCKED_WORDS:
        return "it looks like a sign-in, account, cart, checkout, payment or booking page"
    values = " ".join(v for _k, v in parse_qsl(parts.query, keep_blank_values=True)) + " " + unquote(parts.fragment)
    if _words(values) & BLOCKED_WORDS or any(p in values.lower() for p in ("checkout", "signin", "sign-in", "login")):
        return "it looks like a sign-in, account, cart, checkout, payment or booking page"
    return None


def commit_text(label: str) -> bool:
    """True when an element's text suggests committing (booking, signing in, paying)."""
    return any(rx.search(label or "") for rx in COMMIT_TEXT)


def personal_field(label: str) -> bool:
    """True when a field's label asks for personal data."""
    return any(rx.search(label or "") for rx in PERSONAL_FIELD)


@lru_cache(maxsize=1)
def known_people() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(full names, emails) of everyone in Sarah's world, lower-cased."""
    try:
        from ea_world import paths

        world = paths.world_dir()
        names: set[str] = set()
        emails: set[str] = set()
        contacts = json.loads((world / "contacts.json").read_text(encoding="utf-8")).get("contacts", [])
        persona = json.loads((world / "persona.json").read_text(encoding="utf-8"))
        for person in [*contacts, persona]:
            if " " in str(person.get("name", "")).strip():
                names.add(str(person["name"]).strip().lower())
            if person.get("email"):
                emails.add(str(person["email"]).strip().lower())
        return tuple(sorted(names)), tuple(sorted(emails))
    except Exception:  # the guard must work without the world files
        return (), ()


def personal_value(value: Any) -> bool:
    """True when a value looks like personal data: an email, a phone or card number, or a known person."""
    if not isinstance(value, str):
        return False
    v = value.strip().lower()
    if not v:
        return False
    if EMAIL_VALUE.search(v):
        return True
    undated = DATE_LIKE.sub(" ", v)
    if CARD_VALUE.search(undated):
        return True
    for m in PHONE_VALUE.finditer(undated):
        if sum(ch.isdigit() for ch in m.group(0)) >= 7:
            return True
    names, emails = known_people()
    if any(e in v for e in emails):
        return True
    return any(re.search(r"\b" + re.escape(n) + r"\b", v) for n in names)


def permission_allow(server: str = "browser") -> list[str]:
    """Exact Claude Code allow rules for the tools this mode needs (no wildcards)."""
    return [f"mcp__{server}__{t}" for t in REAL_TOOLS]


def permission_deny(server: str = "browser") -> list[str]:
    return [f"mcp__{server}__{t}" for t in DENIED_TOOLS]


def summary() -> dict[str, Any]:
    """What the app shows about the mode."""
    return {"label": LABEL, "sites": [s.name for s in SITES], "start_urls": [s.start for s in SITES], "port": PORT}
