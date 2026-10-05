"""The Intuition app (spec/14-app.md): API, sessions, SSE, approvals, connectors, world, outputs, reset.

Offline and fast: Starlette's TestClient with the scripted FakeHarness (no Claude), which does real
work against the mock world and waits on the real app-mode approval file protocol.
"""

import json
import time
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from ea_app.fake import FakeHarness
from ea_app.server import create_app

KIT = Path(__file__).resolve().parents[1]
TRACE_TYPES = {
    "user", "assistant", "thinking_summary", "tool_call", "tool_result", "skill_loaded", "delegate",
    "handoff_result", "approval_request", "approval_response", "check", "signal", "stop_check_block", "status",
    "usage", "budget_exceeded", "error", "turn_end", "site_event", "browser_step",
}  # fmt: skip


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EA_KIT_ROOT", str(tmp_path))  # run dirs and runs/CURRENT go under tmp_path
    monkeypatch.setenv("EA_WORLD_DIR", str(KIT / "world"))
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("EA_FAULTS", raising=False)
    app = create_app(lambda name: FakeHarness(delay=0, poll=0.02),
                     claude={"installed": True, "logged_in": True, "version": "test", "message": ""},
                     browser={"available": False, "debug_port_open": False, "installed": [], "default": False,
                              "message": "Install Brave or Chrome to see bookings happen in a browser."})
    with TestClient(app) as c:
        yield c


def start(client: TestClient, **kw: Any) -> str:
    body = {"harness": "fake", "search_mode": "mock", "auto_approve": True, "model": "opus", "browser": False, **kw}
    r = client.post("/api/sessions", json=body)
    assert r.status_code == 201, r.text
    return r.json()["session_id"]


def parse_sse(text: str) -> list[dict[str, Any]]:
    out = []
    for block in text.split("\n\n"):
        data = [line[len("data: "):] for line in block.splitlines() if line.startswith("data: ")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


def events(client: TestClient, sid: str) -> list[dict[str, Any]]:
    r = client.get(f"/api/sessions/{sid}/events", params={"once": 1})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    return [e for e in parse_sse(r.text) if e.get("type") != "session_state"]


def wait_until(client: TestClient, sid: str, pred, timeout: float = 10.0) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        evs = events(client, sid)
        if pred(evs):
            return evs
        time.sleep(0.03)
    raise AssertionError(f"timed out; last events: {[e.get('type') for e in evs]}")


def idle(client: TestClient, sid: str) -> bool:
    info = client.get(f"/api/sessions/{sid}").json()
    return not info["busy"] and info["queued"] == 0


def send(client: TestClient, sid: str, text: str, wait: bool = True) -> list[dict[str, Any]]:
    r = client.post(f"/api/sessions/{sid}/messages", json={"text": text})
    assert r.status_code == 202, r.text
    if not wait:
        return []
    deadline = time.monotonic() + 10
    while not idle(client, sid):
        assert time.monotonic() < deadline, "turn didn't finish"
        time.sleep(0.03)
    return events(client, sid)


def run_dir(client: TestClient, sid: str) -> Path:
    return Path(client.get(f"/api/sessions/{sid}").json()["run_dir"])


def of(evs: list[dict[str, Any]], etype: str, **match: Any) -> list[dict[str, Any]]:
    return [e for e in evs if e.get("type") == etype and all(e.get(k) == v for k, v in match.items())]


# ---------------------------------------------------------------- config and sessions


def test_config_reports_capabilities_without_secrets(client, monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "sk-secret-do-not-leak")
    r = client.get("/api/config")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["app_name"] == "Intuition"
    assert {h["id"] for h in cfg["harnesses"]} == {"claude_code", "fake"}
    assert cfg["search"]["live_available"] is True
    assert cfg["claude"]["installed"] is True
    assert cfg["browser"]["available"] is False
    assert [c["id"] for c in cfg["connectors"]] == ["email", "calendar", "contacts", "docs", "web", "travel"]
    assert cfg["persona"]["initials"] == "MC"
    assert "sk-secret-do-not-leak" not in r.text


def test_index_and_static_are_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()
    for name in ("app.js", "app.css", "tokens.css", "icons.svg"):
        assert client.get(f"/static/{name}").status_code == 200, name


def test_session_creation(client, tmp_path):
    sid = start(client, connectors={"travel": False})
    info = client.get(f"/api/sessions/{sid}").json()
    rd = Path(info["run_dir"])
    assert rd.parent == tmp_path / "runs" and rd.name.startswith("app-")
    assert (rd / "state" / "persona.json").exists()
    assert (tmp_path / "runs" / "CURRENT").read_text().strip() == str(rd)
    ctx = json.loads((rd / "approval_context.json").read_text())
    assert ctx == {"auto_approve": True, "explicit_yes": False, "last_maya_message": ""}
    assert info["options"]["harness"] == "fake" and info["options"]["connectors"]["travel"] is False
    # A connector left off at setup is disconnected right away.
    assert json.loads((rd / "state" / "connectors.json").read_text())["travel"] == "disconnected"
    evs = events(client, sid)
    assert evs[0]["type"] == "session_started"
    assert of(evs, "signal", kind="disconnect", object_id="travel")


@pytest.mark.parametrize("body,status", [
    ({"harness": "nope"}, 400),
    ({"harness": "fake", "search_mode": "live"}, 400),  # no SERPAPI key
    ({"harness": "fake", "connectors": {"fax": True}}, 400),
    ({"harness": "fake", "world": ["no-such-variant"]}, 400),
])
def test_session_creation_rejects_bad_options(client, body, status):
    assert client.post("/api/sessions", json=body).status_code == status


def test_unknown_session_is_404(client):
    assert client.get("/api/sessions/nope/world").status_code == 404
    assert client.post("/api/sessions/nope/messages", json={"text": "hi"}).status_code == 404


# ---------------------------------------------------------------- streaming


def test_event_stream_order(client):
    sid = start(client)
    r = client.post(f"/api/sessions/{sid}/messages", json={"text": "What needs me today?"})
    assert r.status_code == 202
    mid = r.json()["message_id"]
    # until_idle streams live events and closes when the turn is over.
    raw = client.get(f"/api/sessions/{sid}/events", params={"until_idle": 1}).text
    assert raw.startswith("retry:")
    assert "\nid: " in raw or raw.count("id: ") > 3
    evs = [e for e in parse_sse(raw) if e["type"] not in ("session_state", "world_changed")]
    types = [e["type"] for e in evs]
    assert types[:4] == ["session_started", "message_queued", "turn_start", "user"]
    assert types[-4:] == ["assistant", "status", "usage", "turn_end"]
    assert of(evs, "message_queued")[0]["message_id"] == mid
    assert of(evs, "turn_start")[0]["message_id"] == mid
    assert of(evs, "skill_loaded")[0]["skill"] == "inbox-triage"
    assert types.index("skill_loaded") < types.index("tool_call")
    ids = [e["_i"] for e in evs]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    calls = {e["call_id"] for e in of(evs, "tool_call")}
    results = {e["call_id"] for e in of(evs, "tool_result")}
    assert calls == results and len(calls) >= 4
    assert of(evs, "status")[0]["status"] == "done"
    assert all(e.get("world_ts", "").startswith("2026-10-26T08:") for e in evs if e["type"] != "session_started")
    # Reading mail is a world change: the UI is told to refresh.
    assert any(e["type"] == "world_changed" for e in parse_sse(raw))
    # Reconnecting with Last-Event-ID replays only what came after it.
    later = client.get(f"/api/sessions/{sid}/events", params={"once": 1},
                       headers={"Last-Event-ID": str(ids[-3])}).text
    assert [e["type"] for e in parse_sse(later) if e["type"] != "session_state"] == types[-2:]


def test_messages_sent_mid_turn_are_queued(client):
    sid = start(client, auto_approve=False)
    send(client, sid, "Find 30 minutes with Dan and Lisa this week", wait=False)
    wait_until(client, sid, lambda evs: of(evs, "approval_request"))
    r = client.post(f"/api/sessions/{sid}/messages", json={"text": "Thanks!"})
    assert r.json()["queued"] is True
    info = client.get(f"/api/sessions/{sid}").json()
    assert info["busy"] and info["queued"] == 1
    call_id = of(events(client, sid), "approval_request")[0]["call_id"]
    assert client.post(f"/api/sessions/{sid}/approvals/{call_id}", json={"decision": "allow"}).status_code == 204
    evs = wait_until(client, sid, lambda evs: len(of(evs, "turn_end")) == 2)
    assert [e["turn"] for e in of(evs, "turn_start")] == [1, 2]
    assert [e["text"] for e in of(evs, "user")] == ["Find 30 minutes with Dan and Lisa this week", "Thanks!"]


def test_every_trace_event_type_is_streamed(client):
    sid = start(client)
    evs = send(client, sid, "Show me every event type")
    assert TRACE_TYPES <= {e["type"] for e in evs}


# ---------------------------------------------------------------- approvals


def test_approval_round_trip(client):
    sid = start(client, auto_approve=False)
    send(client, sid, "Find 30 minutes with Dan and Lisa this week", wait=False)
    evs = wait_until(client, sid, lambda evs: of(evs, "approval_request"))
    req = of(evs, "approval_request")[0]
    assert req["tool"] == "calendar_create" and "dan.okafor" in req["summary"]
    assert req["card"]["title"] == "Create this event?"
    assert ["With", "Dan Okafor, Lisa Park"] in req["card"]["rows"]
    rd = run_dir(client, sid)
    assert (rd / "approvals" / "pending" / f"{req['call_id']}.json").exists()
    assert not of(evs, "tool_result", call_id=req["call_id"])  # still waiting
    assert client.get(f"/api/sessions/{sid}/world").json()["pending_approvals"] == [req["call_id"]]

    assert client.post(f"/api/sessions/{sid}/approvals/{req['call_id']}", json={"decision": "maybe"}).status_code == 400
    for bad in ("no-such-call", "%2e%2e", "bad%20id"):
        assert client.post(f"/api/sessions/{sid}/approvals/{bad}", json={"decision": "allow"}).status_code == 404
    assert client.post(f"/api/sessions/{sid}/approvals/{req['call_id']}", json={"decision": "allow"}).status_code == 204
    evs = wait_until(client, sid, lambda evs: of(evs, "turn_end"))
    resp = of(evs, "approval_response", call_id=req["call_id"])
    assert len(resp) == 1 and resp[0]["decision"] == "allow" and resp[0]["by"] == "human"
    assert of(evs, "tool_result", call_id=req["call_id"])[0]["ok"] is True
    assert of(evs, "check", verifier="check_event")[0]["ok"] is True
    assert of(evs, "delegate")[0]["to"] == "scheduler"
    assert of(evs, "handoff_result")[0]["to"] == "scheduler"
    assert any(e["agent"] == "scheduler" and e["parent_call_id"] for e in of(evs, "tool_call"))
    assert of(evs, "status")[0]["status"] == "done"
    lines = [json.loads(x) for x in (rd / "approvals.jsonl").read_text().splitlines()]
    assert [x["kind"] for x in lines] == ["request", "response"] and lines[1]["by"] == "human"
    assert not (rd / "approvals" / "pending" / f"{req['call_id']}.json").exists()
    world = client.get(f"/api/sessions/{sid}/world").json()
    assert any(e["title"].startswith("Forecast sync") for e in world["calendar"])
    # Deciding twice is refused.
    assert client.post(f"/api/sessions/{sid}/approvals/{req['call_id']}", json={"decision": "deny"}).status_code == 409


def test_deny_is_recorded_and_nothing_is_created(client):
    sid = start(client, auto_approve=False)
    send(client, sid, "Find 30 minutes with Dan and Lisa this week", wait=False)
    req = of(wait_until(client, sid, lambda evs: of(evs, "approval_request")), "approval_request")[0]
    assert client.post(f"/api/sessions/{sid}/approvals/{req['call_id']}", json={"decision": "deny"}).status_code == 204
    evs = wait_until(client, sid, lambda evs: of(evs, "turn_end"))
    assert of(evs, "approval_response")[0]["decision"] == "deny"
    assert of(evs, "signal", kind="deny")[0]["object_id"] == req["call_id"]
    assert of(evs, "tool_result", call_id=req["call_id"])[0]["ok"] is False
    assert of(evs, "status")[0]["status"] == "waiting"
    world = client.get(f"/api/sessions/{sid}/world").json()
    assert not any(e["title"].startswith("Forecast sync") for e in world["calendar"])


def test_auto_approve_after_an_explicit_yes(client):
    sid = start(client, auto_approve=True)
    evs = send(client, sid, "Book my Chicago trip")
    assert of(evs, "status")[0]["status"] == "waiting"
    assert not of(evs, "approval_request")  # only a plan so far
    evs = send(client, sid, "Yes, book it")
    rd = run_dir(client, sid)
    ctx = json.loads((rd / "approval_context.json").read_text())
    assert ctx["explicit_yes"] is True and ctx["auto_approve"] is True
    resp = of(evs, "approval_response")
    assert len(resp) == 3
    assert all(r["decision"] == "allow" and r["by"] == "app" and r["reason"] == "Approved by your 'yes'" for r in resp)
    assert len(of(evs, "check", verifier="check_booking")) == 3
    world = client.get(f"/api/sessions/{sid}/world").json()
    assert [b["total_usd"] for b in world["bookings"]] == [286, 478, 264]


def test_without_auto_approve_a_yes_still_needs_a_click(client):
    sid = start(client, auto_approve=False)
    send(client, sid, "Book my Chicago trip")
    send(client, sid, "Yes, book it", wait=False)
    evs = wait_until(client, sid, lambda evs: of(evs, "approval_request"))
    req = of(evs, "approval_request")[0]
    assert req["card"]["title"] == "Book this?" and ["Price", "$286"] in req["card"]["rows"]
    time.sleep(0.2)
    assert not of(events(client, sid), "approval_response")  # the typed yes isn't enough
    # Turning auto-approve on mid-session leaves the waiting card waiting; later calls go through on the "yes".
    info = client.post(f"/api/sessions/{sid}/options", json={"auto_approve": True}).json()
    assert info["options"]["auto_approve"] is True
    time.sleep(0.1)
    assert not of(events(client, sid), "approval_response")
    assert client.post(f"/api/sessions/{sid}/approvals/{req['call_id']}", json={"decision": "allow"}).status_code == 204
    evs = wait_until(client, sid, lambda evs: len(of(evs, "turn_end")) == 2)
    assert [r["by"] for r in of(evs, "approval_response")] == ["human", "app", "app"]
    assert of(evs, "option_changed")[0]["auto_approve"] is True


def test_browser_mode_shows_steps_holds_and_screenshots(client):
    sid = start(client, browser=True)
    evs = send(client, sid, "Book my Chicago trip")
    assert {e["action"] for e in of(evs, "browser_step")} >= {"navigate_page", "click", "take_screenshot"}
    assert len(of(evs, "site_event", kind="hold_created")) == 3
    world = client.get(f"/api/sessions/{sid}/world").json()
    assert len(world["holds"]) == 3 and world["links"]["browser"]
    shot = world["links"]["browser"][0]["path"]
    r = client.get(f"/api/sessions/{sid}/outputs/{shot}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    evs = send(client, sid, "Yes, go ahead")
    assert [b["hold_id"] is not None for b in client.get(f"/api/sessions/{sid}/world").json()["bookings"]] == [True] * 3


# ---------------------------------------------------------------- connectors, world, outputs, reset


def test_connector_disconnect_via_api(client):
    sid = start(client)
    r = client.post(f"/api/sessions/{sid}/connectors/email", json={"connected": False})
    assert r.status_code == 200 and r.json() == {"status": "disconnected"}
    evs = events(client, sid)
    sig = of(evs, "signal", kind="disconnect")
    assert len(sig) == 1 and sig[0]["object_id"] == "email"
    world = client.get(f"/api/sessions/{sid}/world").json()
    assert world["connectors"]["email"] == "disconnected"
    evs = send(client, sid, "What needs me today?")
    res = of(evs, "tool_result", tool="email_search")[0]
    assert res["ok"] is False and "disconnected" in res["error"]
    # The assistant was told about the disconnect with the next message.
    assert "turned off the email connector" in of(evs, "user")[0]["text"]
    assert of(evs, "status")[0]["status"] == "partial"
    assert client.post(f"/api/sessions/{sid}/connectors/email", json={"connected": True}).status_code == 409
    assert client.post(f"/api/sessions/{sid}/connectors/fax", json={"connected": False}).status_code == 404


def test_world_snapshot(client):
    sid = start(client)
    w = client.get(f"/api/sessions/{sid}/world").json()
    for key in ("clock", "calendar", "inbox", "outbox", "drafts", "bookings", "holds", "connectors", "outputs", "week"):
        assert key in w
    days = w["week"]["days"]
    assert [d["date"] for d in days] == ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]
    assert days[0]["today"] and days[0]["events"][0]["time_label"] == "09:00–09:30"
    assert w["contacts"]["lisa.park"]["name"] == "Lisa Park"
    assert w["new_arrivals"] == []
    send(client, sid, "Set up a call with Lisa on Friday", wait=False)
    req = of(wait_until(client, sid, lambda evs: of(evs, "approval_request")), "approval_request")[0]
    client.post(f"/api/sessions/{sid}/approvals/{req['call_id']}", json={"decision": "allow"})
    wait_until(client, sid, lambda evs: of(evs, "turn_end"))
    w = client.get(f"/api/sessions/{sid}/world").json()
    friday = [e for e in w["week"]["days"][4]["events"] if e["title"] == "Call with Lisa"][0]
    assert friday["rsvps"]["lisa.park"] == "declined"
    assert len(w["new_arrivals"]) == 1  # the decline notice
    evs = events(client, sid)
    assert of(evs, "signal", kind="decline")[0]["detail"] == "Lisa Park declined: I'm out of office"
    assert of(evs, "check", verifier="check_event")[0]["ok"] is False


def test_outputs_are_served_and_traversal_is_blocked(client):
    sid = start(client)
    evs = send(client, sid, "Prep me for my 2pm")
    assert of(evs, "check", verifier="check_brief")[0]["ok"] is True
    w = client.get(f"/api/sessions/{sid}/world").json()
    link = w["links"]["briefs"][0]
    r = client.get(f"/api/sessions/{sid}/outputs/{link['path']}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "sandbox" in r.headers["content-security-policy"]
    for bad in ("%2e%2e/state/persona.json", "briefs/%2e%2e/%2e%2e/state/persona.json", "..%2fapproval_context.json",
                "%2fetc%2fpasswd", "briefs/.hidden", "nope.html", "briefs"):
        assert client.get(f"/api/sessions/{sid}/outputs/{bad}").status_code == 404, bad


def test_reset_gives_a_new_run_dir(client):
    sid = start(client, connectors={"web": False})
    first = run_dir(client, sid)
    send(client, sid, "What needs me today?")
    client.post(f"/api/sessions/{sid}/connectors/email", json={"connected": False})
    r = client.post(f"/api/sessions/{sid}/reset")
    assert r.status_code == 200
    second = Path(r.json()["run_dir"])
    assert second != first and (second / "state").exists()
    evs = events(client, sid)
    assert [e["type"] for e in evs[:2]] == ["reset", "session_started"]
    assert not of(evs, "message_queued")
    conn = client.get(f"/api/sessions/{sid}/world").json()["connectors"]
    assert conn["email"] == "connected" and conn["web"] == "disconnected"  # setup choices are re-applied
    evs = send(client, sid, "hello")
    assert of(evs, "turn_start")[0]["turn"] == 1
