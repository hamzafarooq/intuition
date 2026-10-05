"""Offline checks for the mock booking sites (`ea_sites`): Skyway, Stays and Tables.

Each test gets a fresh run directory. Sites are reached both by host name (skyway.localhost:8766) and by
the path fallback (localhost:8766/skyway). A hold made on a site is then confirmed through the real
`travel_book` / `restaurant_book` tools, and the site's confirmation page must say "Confirmed".
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from starlette.testclient import TestClient

from ea_sites.server import create_app
from ea_world import core, state, travel  # noqa: F401  (travel registers the booking tools)

WORLD = Path(__file__).resolve().parents[1] / "world"
VARIANTS = sorted(p.stem for p in (WORLD / "variants").glob("*.yaml"))
MAYA = ("Maya Chen", "maya.chen@larkspur.example")
KEVIN = ("Kevin Osei", "kevin.osei@larkspur.example")
HOLD_RE = re.compile(r"^HOLD-[A-Z0-9]{4}$")
MODES = ["host", "path"]


class Site:
    """One site, reached by host name or by path prefix."""

    def __init__(self, app, site: str, mode: str):
        self.site, self.mode = site, mode
        if mode == "host":
            self.client = TestClient(app, base_url=f"http://{site}.localhost:8766")
            self.prefix = ""
        else:
            self.client = TestClient(app, base_url="http://localhost:8766")
            self.prefix = f"/{site}"

    def get(self, path: str, **kw):
        return self.client.get(self.prefix + path, **kw)

    def post(self, path: str, data: dict, **kw):
        return self.client.post(self.prefix + path, data=data, **kw)


def make_run(tmp_path: Path, variants: str | None = None, name: str = "run") -> Path:
    return state.init_run_dir(tmp_path / name, variants)


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("EA_FAULTS", raising=False)
    return make_run(tmp_path)


def sites(run: Path, mode: str = "host") -> dict[str, Site]:
    app = create_app(lambda: run)
    return {s: Site(app, s, mode) for s in ("skyway", "stays", "tables")}


def soup(r) -> BeautifulSoup:
    return BeautifulSoup(r.text, "html.parser")


def text(r) -> str:
    s = soup(r)
    for t in s(["style", "script"]):
        t.decompose()
    return " ".join(s.get_text(" ").split())


def holds(run: Path) -> list[dict]:
    return json.loads((run / "state" / "holds.json").read_text())["holds"]


def events(run: Path) -> list[dict]:
    p = run / "site.jsonl"
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


def hold_from(r) -> str:
    assert r.status_code == 200, r.text[:500]
    m = re.search(r"/review/(HOLD-[A-Z0-9]{4})$", r.url.path)
    assert m, f"not redirected to a review page: {r.url}"
    return m.group(1)


def people(prefix: str, *who: tuple[str, str]) -> dict[str, str]:
    out = {}
    for i, (name, email) in enumerate(who, start=1):
        out[f"{prefix}{i}-name"] = name
        out[f"{prefix}{i}-email"] = email
    return out


def flight_hold(site: Site, flight: str = "fl-sk412-y", who=(MAYA,)) -> str:
    n = len(who)
    assert site.get(f"/passengers?flight={flight}&passengers={n}").status_code == 200
    return hold_from(site.post("/review", {"flight": flight, "passengers": str(n), **people("p", *who)}))


def hotel_hold(site: Site, hotel: str = "ht-harbor-block", ci="2026-11-02", co="2026-11-04", who=(MAYA,)) -> str:
    n = len(who)
    assert site.get(f"/guests?hotel={hotel}&check_in={ci}&check_out={co}&rooms={n}").status_code == 200
    return hold_from(site.post("/review", {"hotel": hotel, "check_in": ci, "check_out": co, "rooms": str(n),
                                           **people("g", *who)}))


def table_hold(site: Site, restaurant="rs-ember-oak", day="2026-11-03", time="19:00", party=2,
               name="Maya Chen", phone="") -> str:
    q = f"restaurant={restaurant}&date={day}&time={time}&party_size={party}"
    assert site.get(f"/details?{q}").status_code == 200
    return hold_from(site.post("/review", {"restaurant": restaurant, "date": day, "time": time,
                                           "party_size": str(party), "name": name, "phone": phone}))


def assert_labelled(r) -> None:
    """Every visible input and select has a <label for>, ids are unique, buttons have text."""
    s = soup(r)
    ids = [el["id"] for el in s.find_all(id=True)]
    assert len(ids) == len(set(ids)), f"duplicate ids on {r.url}"
    labels = {lab.get("for"): lab.get_text(strip=True) for lab in s.find_all("label") if lab.get("for")}
    for el in s.find_all(["input", "select", "textarea"]):
        if el.name == "input" and el.get("type") in ("hidden", "submit", "button"):
            continue
        assert el.get("id") in labels, f"{el} on {r.url} has no <label for>"
        assert labels[el["id"]], f"empty label for {el['id']} on {r.url}"
        assert el.get("name"), f"{el} on {r.url} has no name"
    for b in s.find_all("button"):
        assert b.get_text(strip=True), f"button without text on {r.url}"
    for f in s.find_all("form"):
        assert f.get("method") in ("get", "post") and f.get("action"), f"form without method/action on {r.url}"
    assert s.find("h1") and s.find("h1").get_text(strip=True)
    assert s.find("title").get_text(strip=True)


# ------------------------------------------------------------------ routing


@pytest.mark.parametrize("host", ["localhost:8766", "127.0.0.1:8766"])
def test_index_links_the_three_sites(run_dir, host):
    c = TestClient(create_app(lambda: run_dir), base_url=f"http://{host}")
    r = c.get("/")
    assert r.status_code == 200
    hrefs = {a["href"] for a in soup(r).find_all("a")}
    for site in ("skyway", "stays", "tables"):
        assert f"http://{site}.localhost:8766/" in hrefs
        assert f"/{site}/" in hrefs
    assert c.get("/skyway").url.path == "/skyway/"
    assert c.get("/nowhere").status_code == 404


def test_host_routing_ignores_path_prefixes(run_dir):
    s = sites(run_dir)["skyway"]
    assert "Find a flight" in text(s.get("/"))
    assert sites(run_dir)["stays"].get("/").text.count("Find a hotel") >= 1
    assert "Find a table" in text(sites(run_dir)["tables"].get("/"))
    assert s.get("/nope").status_code == 404


def test_links_stay_in_the_same_routing_mode(run_dir):
    for mode in MODES:
        s = sites(run_dir, mode)["skyway"]
        r = s.get("/results?from=DEN&to=ORD&date=2026-11-02&passengers=1")
        actions = {f["action"] for f in soup(r).find_all("form")}
        assert actions == {f"{s.prefix}/passengers"}


# ------------------------------------------------------------------ every page, every variant


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("variant", [None, *VARIANTS])
def test_every_page_renders(tmp_path, monkeypatch, variant, mode):
    monkeypatch.delenv("EA_FAULTS", raising=False)
    run = make_run(tmp_path, variant)
    s = sites(run, mode)
    sky, stays, tables = s["skyway"], s["stays"], s["tables"]

    pages = [
        sky.get("/"),
        sky.get("/results?from=Denver&to=Chicago&date=2026-11-02&passengers=1"),
        sky.get("/results?from=ORD&to=DEN&date=2026-11-04&passengers=2"),
        sky.get("/passengers?flight=fl-sk412-y&passengers=2"),
        stays.get("/"),
        stays.get("/results?city=Chicago&check_in=2026-11-02&check_out=2026-11-04&rooms=1"),
        stays.get("/guests?hotel=ht-loop-inn&check_in=2026-11-02&check_out=2026-11-04&rooms=2"),
        tables.get("/"),
        tables.get("/results?city=Chicago&date=2026-11-03&time=19:00&party_size=2"),
        tables.get("/details?restaurant=rs-harbor-bistro&date=2026-11-03&time=18:30&party_size=4"),
    ]
    for r in pages:
        assert r.status_code == 200, (r.url, r.text[:300])
        assert_labelled(r)

    fh = flight_hold(sky)
    hh = hotel_hold(stays, "ht-loop-inn")
    th = table_hold(tables, "rs-harbor-bistro", time="18:30", party=4)
    for site, hid in ((sky, fh), (stays, hh), (tables, th)):
        for path in (f"/review/{hid}", f"/holds/{hid}"):
            r = site.get(path)
            assert r.status_code == 200
            assert hid in text(r) and "Held" in text(r)
            assert_labelled(r)
        r = site.get("/booking/bk-999")
        assert r.status_code == 404 and "Not confirmed" in text(r)


def test_harbor_sold_out_hides_the_block(tmp_path):
    q = "/results?city=Chicago&check_in=2026-11-02&check_out=2026-11-04&rooms=1"
    default = text(sites(make_run(tmp_path, None, "a"))["stays"].get(q))
    assert "Harbor Point Hotel" in default and "MBS26" in default and "$239" in default

    sold_run = make_run(tmp_path, "harbor-sold-out", "b")
    sold = sites(sold_run)["stays"]
    t = text(sold.get(q))
    assert "Harbor Point Hotel" not in t and "MBS26" not in t
    assert "Lakeview Grand" in t and "Loop Inn Express" in t
    r = sold.get("/guests?hotel=ht-harbor-block&check_in=2026-11-02&check_out=2026-11-04&rooms=1")
    assert r.status_code == 404
    r = sold.post("/review", {"hotel": "ht-harbor-block", "check_in": "2026-11-02", "check_out": "2026-11-04",
                              "rooms": "1", **people("g", MAYA)})
    assert r.status_code == 404 and holds(sold_run) == []


# ------------------------------------------------------------------ search results


def test_flight_results_match_the_catalog(run_dir):
    sky = sites(run_dir)["skyway"]
    r = sky.get("/results?from=DEN&to=ORD&date=2026-11-02&passengers=1")
    t = text(r)
    for bit in ("SK412", "PK220", "$286", "$910", "$139", "Recommended", "15:10", "18:35", "Economy", "Business",
                "Basic Economy", "(+1 day)"):
        assert bit in t, bit
    assert "SK418" not in t  # that one is on 3 November
    buttons = [b.get_text(" ", strip=True) for b in soup(r).find_all("button")]
    assert buttons == ["Select SK412 Economy, $286", "Select SK412 Business, $910", "Select PK220 Basic Economy, $139"]
    t3 = text(sky.get("/results?from=Denver&to=Chicago&date=2026-11-03&passengers=1"))
    assert "SK418" in t3 and "Arrives after the 09:00 keynote" in t3
    t_back = text(sky.get("/results?from=Chicago&to=Denver&date=Nov 4 2026&passengers=2"))
    assert "SK431" in t_back and "SK427" in t_back and "$528 for 2" in t_back and "Leaves before the summit ends" in t_back
    none = sky.get("/results?from=DEN&to=ORD&date=2026-11-05&passengers=1")
    assert none.status_code == 200 and "No flights" in text(none)
    assert {a.get_text(strip=True) for a in soup(none).select("main ul a")} == {"Mon 2 Nov 2026", "Tue 3 Nov 2026"}


def test_hotel_results_show_price_distance_tags_and_block_code(run_dir):
    st = sites(run_dir)["stays"]
    t = text(st.get("/results?city=chicago&check_in=2026-11-02&check_out=2026-11-04&rooms=1"))
    for bit in ("Harbor Point Hotel", "$239", "$478 for 2 nights", "0.4 miles", "MBS26", "Summit block (code MBS26)",
                "Lakeview Grand", "$340", "Closest to the venue", "Loop Inn Express", "$189", "2.8 miles", "taxes included"):
        assert bit in t, bit
    assert t.index("Loop Inn Express") < t.index("Harbor Point Hotel") < t.index("Lakeview Grand")  # by price


def test_restaurant_results_show_times_and_party_limits(run_dir):
    tb = sites(run_dir)["tables"]
    r = tb.get("/results?city=Chicago&date=2026-11-03&time=19:00&party_size=6")
    t = text(r)
    assert "Ember & Oak" in t and "Lake Street Chophouse" in t and "Harbor Bistro" in t
    buttons = {b.get_text(" ", strip=True) for b in soup(r).find_all("button")}
    assert buttons == {"19:00 at Ember & Oak", "21:00 at Lake Street Chophouse"}  # Harbor Bistro seats 4
    assert "No table for 6 people" in t
    r2 = tb.get("/results?city=Chicago&date=2026-11-03&time=7pm&party_size=2")
    assert {"18:30 at Harbor Bistro", "19:30 at Harbor Bistro"} <= {b.get_text(" ", strip=True) for b in soup(r2).find_all("button")}


@pytest.mark.parametrize("site,query,field", [
    ("skyway", "/results?from=&to=ORD&date=2026-11-02", "from"),
    ("skyway", "/results?from=DEN&to=ORD&date=someday", "date"),
    ("stays", "/results?city=Chicago&check_in=2026-11-04&check_out=2026-11-02", "check_out"),
    ("tables", "/results?city=Chicago&date=2026-11-03&time=19:00&party_size=40", "party_size"),
])
def test_search_errors_are_shown_next_to_the_field(run_dir, site, query, field):
    r = sites(run_dir)[site].get(query)
    assert r.status_code == 400
    s = soup(r)
    assert s.find(id=f"{field}-error") and s.find(id=field)["aria-invalid"] == "true"
    assert s.select_one(".error-summary a")["href"] == f"#{field}"
    assert_labelled(r)


# ------------------------------------------------------------------ holds


@pytest.mark.parametrize("mode", MODES)
def test_flight_hold_has_the_fare_and_total(run_dir, mode):
    sky = sites(run_dir, mode)["skyway"]
    hid = flight_hold(sky)
    assert HOLD_RE.match(hid)
    r = sky.get(f"/review/{hid}")
    t = text(r)
    assert "Held for 30 minutes; your assistant will confirm." in t
    assert "Fare: $286 × 1 passenger" in t and "Total $286" in t
    (h,) = holds(run_dir)
    assert h["hold_id"] == hid and h["kind"] == "flight" and h["option_id"] == "fl-sk412-y"
    assert h["status"] == "held" and h["booking_id"] is None and h["total_usd"] == 286
    assert h["details"]["travelers"] == ["maya"]
    clock = json.loads((run_dir / "state" / "clock.json").read_text())["now"]
    assert h["created_at"] == clock and h["expires_at"] > clock


def test_two_passengers_hold_both_travellers(tmp_path):
    run = make_run(tmp_path, "kevin-travels-too")
    sky = sites(run)["skyway"]
    page = sky.get("/passengers?flight=fl-sk412-y&passengers=1")
    assert soup(page).find(id="p1-name")["value"] == "Maya Chen"  # signed-in traveller prefilled
    add = soup(page).find("a", string="Add another passenger")["href"]
    two = soup(sky.client.get(add))
    assert "passengers=2" in add and two.find(id="p2-name")
    # repeated fields get distinct accessible names for the browser tools
    names = {lab["for"]: " ".join(lab.get_text(" ").split()) for lab in two.find_all("label")}
    assert names["p1-name"] == "Passenger 1 Full name" and names["p2-email"] == "Passenger 2 Email (optional)"
    r = sky.post("/review", {"flight": "fl-sk412-y", "passengers": "2", **people("p", MAYA), "p2-name": "kevin.osei",
                             "p2-email": ""})  # a contact id works too; a second passenger's email is optional
    hid = hold_from(r)
    (h,) = holds(run)
    assert h["details"]["travelers"] == ["maya", "kevin.osei"] and h["total_usd"] == 572
    assert h["details"]["people"][1] == {"name": "Kevin Osei", "email": KEVIN[1], "contact_id": "kevin.osei"}
    t = text(sky.get(f"/review/{hid}"))
    assert "Passengers Maya Chen, Kevin Osei" in t and "Fare: $286 × 2 passengers" in t and "Total $572" in t


def test_unknown_or_repeated_travellers_are_refused(run_dir):
    sky = sites(run_dir)["skyway"]
    base = {"flight": "fl-sk412-y", "passengers": "2"}
    r = sky.post("/review", {**base, **people("p", MAYA, ("Pat Nobody", "pat@nowhere.example"))})
    assert r.status_code == 400 and "isn't a traveller on the Larkspur Supply account" in text(r)
    assert soup(r).find(id="p2-name")["value"] == "Pat Nobody"  # values kept
    r = sky.post("/review", {**base, **people("p", MAYA, MAYA)})
    assert r.status_code == 400 and "already listed" in text(r)
    r = sky.post("/review", {**base, **people("p", ("Kevin Osei", MAYA[1]))})
    assert r.status_code == 400 and "different people" in text(r)
    r = sky.post("/review", {"flight": "fl-sk412-y", "passengers": "1", "p1-name": "Maya Chen", "p1-email": ""})
    assert r.status_code == 400 and "email" in text(r).lower()
    assert holds(run_dir) == []


def test_resubmitting_the_same_form_reuses_the_hold(run_dir):
    sky = sites(run_dir)["skyway"]
    assert flight_hold(sky) == flight_hold(sky)
    assert len(holds(run_dir)) == 1
    flight_hold(sky, "fl-sk431-y")
    assert len(holds(run_dir)) == 2


@pytest.mark.parametrize("rooms,total", [(1, 478), (2, 956)])
def test_hotel_hold_total_is_nightly_times_nights_times_rooms(run_dir, rooms, total):
    st = sites(run_dir)["stays"]
    who = (MAYA, KEVIN)[:rooms]
    hid = hotel_hold(st, who=who)
    (h,) = holds(run_dir)
    assert h["total_usd"] == total and h["kind"] == "hotel" and h["option_id"] == "ht-harbor-block"
    d = h["details"]
    assert (d["check_in"], d["check_out"], d["nights"], d["rooms"]) == ("2026-11-02", "2026-11-04", 2, rooms)
    assert d["travelers"] == ["maya", "kevin.osei"][:rooms]
    t = text(st.get(f"/review/{hid}"))
    assert f"$239 a night × 2 nights × {rooms} room" in t and f"Total ${total:,}" in t and "MBS26" in t


@pytest.mark.parametrize("restaurant,time,party,total", [
    ("rs-ember-oak", "19:00", 2, 170), ("rs-harbor-bistro", "18:30", 4, 240), ("rs-lake-chop", "21:00", 8, 960)])
def test_restaurant_hold_total_is_cost_per_person_times_party(run_dir, restaurant, time, party, total):
    tb = sites(run_dir)["tables"]
    hid = table_hold(tb, restaurant, time=time, party=party, phone="+1 303 555 0142")
    (h,) = holds(run_dir)
    assert h["total_usd"] == total and h["kind"] == "restaurant"
    assert h["details"] == {"date": "2026-11-03", "time": time, "party_size": party, "guests": [], "name": "Maya Chen",
                            "phone": "+1 303 555 0142", "unit_price_usd": total // party}
    assert f"Estimated total ${total:,}" in text(tb.get(f"/review/{hid}"))


def test_restaurant_rejects_unavailable_slots(run_dir):
    tb = sites(run_dir)["tables"]
    assert tb.get("/details?restaurant=rs-harbor-bistro&date=2026-11-03&time=19:00&party_size=2").status_code == 404
    assert tb.get("/details?restaurant=rs-harbor-bistro&date=2026-11-03&time=18:30&party_size=5").status_code == 404
    r = tb.post("/review", {"restaurant": "rs-ember-oak", "date": "2026-11-03", "time": "19:00", "party_size": "2",
                            "name": "", "phone": ""})
    assert r.status_code == 400 and soup(r).find(id="name-error")
    assert holds(run_dir) == []


def test_a_hold_page_on_the_wrong_site_points_to_the_right_one(run_dir):
    s = sites(run_dir)
    hid = hotel_hold(s["stays"])
    r = s["skyway"].get(f"/holds/{hid}")
    assert "This hold is on Stays" in text(r)
    assert soup(r).find("a", string=re.compile(f"See {hid} on Stays"))["href"] == f"http://stays.localhost:8766/holds/{hid}"
    assert s["skyway"].get("/holds/HOLD-ZZZZ").status_code == 404


# ------------------------------------------------------------------ confirmation through the booking tools


def book(run: Path, tool: str, hold_id: str) -> dict:
    core.configure(run)
    return core.call(tool, {"hold_id": hold_id})


@pytest.mark.parametrize("mode", MODES)
def test_travel_book_confirms_flight_and_hotel_holds(run_dir, mode):
    s = sites(run_dir, mode)
    fh, hh = flight_hold(s["skyway"]), hotel_hold(s["stays"])
    assert "Not confirmed" in text(s["skyway"].get(f"/booking/{fh}"))  # a hold isn't a booking yet

    fb = book(run_dir, "travel_book", fh)
    hb = book(run_dir, "travel_book", hh)
    assert (fb["kind"], fb["total_usd"], hb["kind"], hb["total_usd"]) == ("flight", 286, "hotel", 478)

    for site, hid, b in ((s["skyway"], fh, fb), (s["stays"], hh, hb)):
        r = site.get(f"/booking/{b['booking_id']}")
        t = text(r)
        assert r.status_code == 200 and "Confirmed" in t and "Not confirmed" not in t and b["booking_id"] in t
        assert hid in t
        t_hold = text(site.get(f"/holds/{hid}"))
        assert "Confirmed" in t_hold and b["booking_id"] in t_hold
        # the hold reference also leads to the booking
        assert site.get(f"/booking/{hid}").url.path == f"{site.prefix}/booking/{b['booking_id']}"
    assert "Total $286" in text(s["skyway"].get(f"/booking/{fb['booking_id']}"))
    assert "Total $478" in text(s["stays"].get(f"/booking/{hb['booking_id']}"))
    assert {h["hold_id"]: (h["status"], h["booking_id"]) for h in holds(run_dir)} == {
        fh: ("confirmed", fb["booking_id"]), hh: ("confirmed", hb["booking_id"])}
    bookings = json.loads((run_dir / "state" / "bookings.json").read_text())["bookings"]
    assert {b["hold_id"] for b in bookings} == {fh, hh}
    # a flight booking on the hotel site points to Skyway instead of saying "Confirmed"
    wrong = text(s["stays"].get(f"/booking/{fb['booking_id']}"))
    assert "This booking is on Skyway" in wrong


def test_restaurant_book_confirms_a_table_hold(run_dir):
    tb = sites(run_dir)["tables"]
    hid = table_hold(tb, "rs-harbor-bistro", time="19:30", party=4)
    b = book(run_dir, "restaurant_book", hid)
    assert b["kind"] == "restaurant" and b["total_usd"] == 240
    t = text(tb.get(f"/booking/{b['booking_id']}"))
    assert "Confirmed" in t and "Not confirmed" not in t and b["booking_id"] in t and "19:30" in t and "Harbor Bistro" in t
    (h,) = holds(run_dir)
    assert h["status"] == "confirmed" and h["booking_id"] == b["booking_id"]


def test_cancelled_booking_is_not_confirmed(run_dir):
    sky = sites(run_dir)["skyway"]
    b = book(run_dir, "travel_book", flight_hold(sky))
    core.call("travel_cancel", {"booking_id": b["booking_id"]})
    t = text(sky.get(f"/booking/{b['booking_id']}"))
    assert "Not confirmed" in t and "cancelled" in t


# ------------------------------------------------------------------ event log


def test_site_jsonl_records_views_submissions_and_holds(run_dir):
    sky = sites(run_dir)["skyway"]
    sky.get("/")
    sky.get("/results?from=DEN&to=ORD&date=2026-11-02&passengers=1")
    hid = flight_hold(sky)
    ev = events(run_dir)
    assert all(set(e) == {"ts", "site", "type", "path", "data"} for e in ev)
    assert {e["site"] for e in ev} == {"skyway"}
    kinds = [(e["type"], e["path"]) for e in ev]
    assert ("page_view", "/") in kinds
    assert ("form_submit", "/results") in kinds and ("page_view", "/results") in kinds
    assert ("form_submit", "/passengers") in kinds and ("form_submit", "/review") in kinds
    assert ("page_view", f"/review/{hid}") in kinds
    (created,) = [e for e in ev if e["type"] == "hold_created"]
    assert created["data"]["hold_id"] == hid and created["data"]["total_usd"] == 286
    submit = next(e for e in ev if e["type"] == "form_submit" and e["path"] == "/review")
    assert submit["data"]["flight"] == "fl-sk412-y" and submit["data"]["p1-name"] == "Maya Chen"
    assert kinds.index(("form_submit", "/review")) < kinds.index(("hold_created", "/review")) < kinds.index(("page_view", f"/review/{hid}"))


def test_path_mode_logs_site_relative_paths(run_dir):
    tb = sites(run_dir, "path")["tables"]
    tb.get("/results?city=Chicago&date=2026-11-03&time=19:00&party_size=2")
    ev = events(run_dir)
    assert {(e["site"], e["type"], e["path"]) for e in ev} == {("tables", "form_submit", "/results"),
                                                              ("tables", "page_view", "/results")}


# ------------------------------------------------------------------ run directory


def test_run_dir_is_resolved_on_every_request(tmp_path, monkeypatch):
    monkeypatch.delenv("EA_RUN_DIR", raising=False)
    a, b = make_run(tmp_path, None, "a"), make_run(tmp_path, "harbor-sold-out", "b")
    kit = tmp_path / "kit"
    monkeypatch.setenv("EA_KIT_ROOT", str(kit))
    state.write_current(a)
    st = Site(create_app(), "stays", "host")  # default resolver: EA_RUN_DIR, else runs/CURRENT
    q = "/results?city=Chicago&check_in=2026-11-02&check_out=2026-11-04&rooms=1"
    assert "Harbor Point Hotel" in text(st.get(q))
    state.write_current(b)
    assert "Harbor Point Hotel" not in text(st.get(q))
    monkeypatch.setenv("EA_RUN_DIR", str(a))
    assert "Harbor Point Hotel" in text(st.get(q))
    assert events(a) and events(b)


def test_no_world_gives_a_clear_page(tmp_path):
    app = create_app(lambda: None)
    r = Site(app, "skyway", "host").get("/")
    assert r.status_code == 503 and "No world is loaded" in text(r)
    r = Site(create_app(tmp_path / "missing"), "tables", "path").get("/")
    assert r.status_code == 503
    assert TestClient(app, base_url="http://localhost:8766").get("/").status_code == 200
