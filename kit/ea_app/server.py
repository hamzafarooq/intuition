"""The Intuition web app: Starlette, server-sent events and a static front end (spec/14-app.md).

    GET  /phone/{id}                              the phone alone, for its own window (same session, same stream)
    GET  /api/config                              app name, harnesses, Claude Code status, search, browser, voice
    POST /api/sessions                            {harness, search_mode, auto_approve, model, browser, connectors, variants?, world?}
    GET  /api/sessions/{id}                       session info
    DELETE /api/sessions/{id}                     close a session
    GET  /api/sessions/{id}/events                SSE: replay, then live events (?once=1, ?until_idle=1, ?after=<id>)
    POST /api/sessions/{id}/messages              {text} -> 202
    POST /api/sessions/{id}/approvals/{call_id}   {decision: allow|deny} -> 204
    POST /api/sessions/{id}/connectors/{name}     {connected: false} -> {status}
    POST /api/sessions/{id}/options               {auto_approve} -> session info
    GET  /api/sessions/{id}/world                 Maya's world
    GET  /api/sessions/{id}/outputs/{path}        briefs, decks and browser screenshots
    POST /api/sessions/{id}/reset                 new run directory and harness
"""

import asyncio
import contextlib
import html
import json
import logging
import mimetypes
import os
import re
import threading
import urllib.parse
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ea_world import paths, state
from ea_world.state import CONNECTORS

from . import checks
from .sessions import HarnessFactory, Session, SessionError, SessionManager, SessionOptions
from .views import world_view

log = logging.getLogger("ea_app")

STATIC_DIR = Path(__file__).parent / "static"
MODELS = [{"id": "opus", "label": "Opus"}, {"id": "sonnet", "label": "Sonnet"}]
MODEL_RE = re.compile(r"^[A-Za-z0-9.\-_\[\]]{1,80}$")
CONNECTOR_INFO = [
    {"id": "email", "label": "Email", "sees": "Your inbox, sent mail and drafts.",
     "does": "Writes drafts. Sends only after you approve."},
    {"id": "calendar", "label": "Calendar", "sees": "Your week, and teammates' free and busy times.",
     "does": "Creates, moves or cancels events after you approve."},
    {"id": "contacts", "label": "Contacts", "sees": "People's roles, time zones and working hours.",
     "does": "Looks people up. Changes nothing."},
    {"id": "docs", "label": "Documents", "sees": "Account notes, policies and the forecast.",
     "does": "Searches and reads. Changes nothing."},
    {"id": "web", "label": "Web", "sees": "Search results and the pages it opens.",
     "does": "Treats what it reads as information, never as instructions."},
    {"id": "travel", "label": "Travel", "sees": "Flights, hotels and restaurant tables.",
     "does": "Holds options. Books only after you approve the price."},
]
SUGGESTIONS = ["What needs me today?", "Prep me for my 2pm", "Find 30 minutes with Dan and Lisa this week"]
FAKE_EXTRA_SUGGESTIONS = ["Book my Chicago trip", "Set up a call with Lisa on Friday", "Show me every event type"]


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _persona() -> dict[str, Any]:
    p = state.read_json(paths.world_dir() / "persona.json", {}) or {}
    name = p.get("name", "Maya Chen")
    return {
        "name": name,
        "initials": "".join(w[0] for w in name.split()[:2]).upper(),
        "title": p.get("title", ""),
        "company": p.get("company", ""),
        "email": p.get("email", ""),
        "timezone": p.get("timezone", ""),
        "now": p.get("now", ""),
    }


def create_app(
    harness_factory: HarnessFactory | None = None,
    *,
    fake: bool = False,
    claude: dict[str, Any] | Callable[[], dict[str, Any]] | None = None,
    browser: dict[str, Any] | Callable[[], dict[str, Any]] | None = None,
    app_name: str | None = None,
    sites_port: int | None = None,
) -> Starlette:
    """Build the app. Tests pass a `harness_factory` (and `claude=`/`browser=` dicts to skip the probes).
    With `sites_port`, the mock booking sites (`ea_sites`) also run on 127.0.0.1:<port> in a background
    thread, unless that port is already taken (another copy is running); they read runs/CURRENT, which
    every app session writes."""
    name = app_name or os.environ.get("APP_NAME", "").strip() or "Intuition"
    manager = SessionManager(harness_factory)
    fake_enabled = fake or harness_factory is not None
    cache: dict[str, Any] = {}

    async def claude_info() -> dict[str, Any]:
        if "claude" not in cache:
            if isinstance(claude, dict):
                cache["claude"] = claude
            else:
                probe = claude if callable(claude) else checks.claude_status
                cache["claude"] = await asyncio.to_thread(probe)
        return cache["claude"]

    async def browser_info() -> dict[str, Any]:
        if isinstance(browser, dict):
            return browser
        probe = browser if callable(browser) else checks.browser_status
        return await asyncio.to_thread(probe)

    def claude_ready(info: dict[str, Any]) -> bool:
        return bool(info.get("installed")) and info.get("logged_in") is not False

    def session_or_404(request: Request) -> Session | Response:
        s = manager.get(request.path_params["sid"])
        if s is None:
            return _error(404, "No such session. Start a new one.")
        return s

    async def read_body(request: Request) -> dict[str, Any] | Response:
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return _error(400, "The body must be JSON.")
        if not isinstance(body, dict):
            return _error(400, "The body must be a JSON object.")
        return body

    # ------------------------------------------------------------ pages

    async def index(request: Request) -> Response:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    async def phone_page(request: Request) -> Response:
        """The same app page in phone-only mode, for a pop-out window or a second tab."""
        sid = request.path_params.get("sid") or request.query_params.get("session") or ""
        if "sid" not in request.path_params and sid:
            return RedirectResponse("/phone/" + urllib.parse.quote(sid, safe=""), status_code=307)
        if manager.get(sid) is None:
            return HTMLResponse(
                f"<!doctype html><meta charset='utf-8'><title>{html.escape(name)}</title>"
                "<link rel='stylesheet' href='/static/tokens.css'><link rel='stylesheet' href='/static/app.css'>"
                "<main class='gone'><h1 class='display'>That conversation has ended.</h1>"
                "<p class='lede'>Start a new one from the main window.</p><p><a class='btn btn-primary btn-pill' href='/'>Open "
                f"{html.escape(name)}</a></p></main>", status_code=404)
        page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        page = page.replace('<body data-view="landing">',
                            f'<body data-view="chat" data-page="phone" data-session="{html.escape(sid, quote=True)}">', 1)
        page = page.replace("<title>Intuition</title>", f"<title>{html.escape(name)} · phone</title>", 1)
        return HTMLResponse(page, headers={"Cache-Control": "no-cache"})

    async def config(request: Request) -> Response:
        c = await claude_info()
        b = await browser_info()
        live = checks.live_search_available()
        harnesses = [{"id": "claude_code", "label": "Claude Code", "available": claude_ready(c),
                      "note": c.get("message", "")}]
        if fake_enabled:
            harnesses.append({"id": "fake", "label": "Scripted demo", "available": True,
                              "note": "Plays scripted replies against the real mock world. No Claude needed."})
        guide = paths.kit_root() / "site" / "dist"
        return JSONResponse({
            "app_name": name,
            "harnesses": harnesses,
            "default_harness": "fake" if fake else "claude_code",
            "claude": {k: c.get(k) for k in ("installed", "logged_in", "version", "message")},
            "search": {"live_available": live, "default": "live" if live else "mock"},
            "models": MODELS,
            "default_model": "opus",
            "browser": b,
            "voice": {"enabled": checks.voice_enabled()},
            "connectors": CONNECTOR_INFO,
            "persona": _persona(),
            "guide": {"available": (guide / "index.html").exists(), "url": "/guide/"},
            "suggestions": SUGGESTIONS,
            "extra_suggestions": FAKE_EXTRA_SUGGESTIONS if fake_enabled else [],
            "fake": fake,
            "sites": dict(sites),
        })

    # ------------------------------------------------------------ sessions

    async def create_session(request: Request) -> Response:
        body = await read_body(request)
        if isinstance(body, Response):
            return body
        allowed = {"claude_code"} | ({"fake"} if fake_enabled else set())
        harness = str(body.get("harness") or ("fake" if fake else "claude_code"))
        if harness not in allowed:
            return _error(400, f"Unknown harness '{harness}'. Available: {', '.join(sorted(allowed))}.")
        if harness == "claude_code" and harness_factory is None:
            c = await claude_info()
            if not claude_ready(c):
                return _error(409, c.get("message") or "Claude Code isn't ready.")
        search = str(body.get("search_mode") or "mock")
        if search not in ("live", "mock"):
            return _error(400, "search_mode must be 'live' or 'mock'.")
        if search == "live" and not checks.live_search_available():
            return _error(400, "Live search needs SERPAPI_API_KEY in .env. Use mock search, or add the key and restart.")
        model = str(body.get("model") or "opus")
        if not MODEL_RE.match(model):
            return _error(400, "Bad model name.")
        conn = body.get("connectors")
        connectors_on = {c: True for c in CONNECTORS}
        if conn is not None:
            if not isinstance(conn, dict) or any(k not in CONNECTORS for k in conn):
                return _error(400, f"connectors must map names ({', '.join(CONNECTORS)}) to true or false.")
            connectors_on.update({k: bool(v) for k, v in conn.items()})
        variants = body.get("variants") or {}
        if not isinstance(variants, dict):
            return _error(400, "variants must be an object such as {\"hooks\": \"off\"}.")
        world = state.parse_variants(body.get("world"))
        for w in world:
            if not state.variant_path(w).exists():
                return _error(400, f"Unknown world variant '{w}'.")
        opts = SessionOptions(harness=harness, search_mode=search, auto_approve=bool(body.get("auto_approve", True)),
                              model=model, browser=bool(body.get("browser", False)), connectors=connectors_on,
                              variants={str(k): str(v) for k, v in variants.items()}, world=world)
        try:
            s = await manager.create(opts)
        except SessionError as exc:
            return _error(500, str(exc))
        return JSONResponse({"session_id": s.id, **s.info()}, status_code=201)

    async def get_session(request: Request) -> Response:
        s = session_or_404(request)
        return s if isinstance(s, Response) else JSONResponse(s.info())

    async def delete_session(request: Request) -> Response:
        ok = await manager.remove(request.path_params["sid"])
        return Response(status_code=204) if ok else _error(404, "No such session.")

    async def events(request: Request) -> Response:
        s = session_or_404(request)
        if isinstance(s, Response):
            return s
        once = request.query_params.get("once") in ("1", "true")
        until_idle = request.query_params.get("until_idle") in ("1", "true")
        last = request.headers.get("last-event-id") or request.query_params.get("after") or ""
        try:
            last_i = int(last)
        except ValueError:
            last_i = -1
        q = s.subscribe()

        def fmt(ev: dict[str, Any]) -> str:
            data = json.dumps(ev, ensure_ascii=False, default=str)
            head = f"id: {ev['_i']}\n" if "_i" in ev else ""
            return f"{head}data: {data}\n\n"

        async def stream() -> AsyncIterator[str]:
            try:
                yield "retry: 2000\n\n"
                sent = last_i
                # Ids never repeat, even across a reset, so "after the last id I saw" is always right.
                for e in [e for e in s.events if e["_i"] > last_i]:
                    yield fmt(e)
                    sent = e["_i"]
                yield fmt(s.state_event())
                if once or (until_idle and s.idle):
                    return
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if ev is None:
                        return
                    if "_i" in ev:
                        if ev["_i"] <= sent:
                            continue
                        sent = ev["_i"]
                    yield fmt(ev)
                    if until_idle and ev.get("type") == "session_state" and s.idle:
                        return
            finally:
                s.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def post_message(request: Request) -> Response:
        s = session_or_404(request)
        if isinstance(s, Response):
            return s
        body = await read_body(request)
        if isinstance(body, Response):
            return body
        text = str(body.get("text") or "").strip()
        if not text:
            return _error(400, "Type a message first.")
        if len(text) > 8000:
            return _error(400, "That message is too long (8,000 characters at most).")
        mid, queued = s.submit(text)
        return JSONResponse({"message_id": mid, "queued": queued}, status_code=202)

    async def post_approval(request: Request) -> Response:
        s = session_or_404(request)
        if isinstance(s, Response):
            return s
        body = await read_body(request)
        if isinstance(body, Response):
            return body
        decision = str(body.get("decision") or "")
        if decision not in ("allow", "deny"):
            return _error(400, "decision must be 'allow' or 'deny'.")
        try:
            result = s.decide(request.path_params["call_id"], decision)
        except KeyError:
            return _error(404, "No approval is waiting with that id.")
        if result == "already":
            return _error(409, "That approval has already been decided.")
        return Response(status_code=204)

    async def post_connector(request: Request) -> Response:
        s = session_or_404(request)
        if isinstance(s, Response):
            return s
        body = await read_body(request)
        if isinstance(body, Response):
            return body
        cname = request.path_params["name"]
        if cname not in CONNECTORS:
            return _error(404, f"Unknown connector '{cname}'.")
        if body.get("connected", False) is not False:
            current = (state.read_json(s.run_dir / "state" / "connectors.json", {}) or {}) if s.run_dir else {}
            if current.get(cname) == "disconnected":
                return _error(409, "A connector can't be reconnected in this session. Use Reset world.")
            return JSONResponse({"status": "connected"})
        status = await s.disconnect(cname)
        return JSONResponse({"status": status})

    async def post_options(request: Request) -> Response:
        s = session_or_404(request)
        if isinstance(s, Response):
            return s
        body = await read_body(request)
        if isinstance(body, Response):
            return body
        if "auto_approve" in body:
            s.set_auto_approve(bool(body["auto_approve"]))
        return JSONResponse(s.info())

    async def get_world(request: Request) -> Response:
        s = session_or_404(request)
        if isinstance(s, Response):
            return s
        if not s.run_dir:
            return _error(409, "The session has no world yet.")
        view = await asyncio.to_thread(world_view, s.run_dir)
        pending = [e for e in s.events if e.get("type") == "approval_request"]
        answered = {e.get("call_id") for e in s.events if e.get("type") == "approval_response"}
        view["pending_approvals"] = [e.get("call_id") for e in pending if e.get("call_id") not in answered]
        view["world_now"] = s.world_now()
        view["run_name"] = s.run_dir.name
        return JSONResponse(view)

    async def get_output(request: Request) -> Response:
        s = session_or_404(request)
        if isinstance(s, Response):
            return s
        if not s.run_dir:
            return _error(404, "Not found.")
        rel = request.path_params["path"]
        base = (s.run_dir / "outputs").resolve()
        if "\x00" in rel or any(part.startswith(".") for part in Path(rel).parts):
            return _error(404, "Not found.")
        target = (base / rel).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            return _error(404, "Not found.")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return FileResponse(target, media_type=ctype, headers={
            "Content-Security-Policy": "sandbox allow-popups; default-src 'none'; style-src 'unsafe-inline'; "
                                       "img-src 'self' data:; font-src data:",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache",
        })

    async def reset_session(request: Request) -> Response:
        s = session_or_404(request)
        if isinstance(s, Response):
            return s
        try:
            await s.reset()
        except SessionError as exc:
            return _error(500, str(exc))
        return JSONResponse(s.info())

    # ------------------------------------------------------------ app

    sites: dict[str, Any] = {"running": False, "port": sites_port, "note": "not started"}

    async def start_sites() -> Any:
        if not sites_port:
            return None
        if checks.port_in_use(sites_port):
            sites["note"] = f"port {sites_port} is busy; using the booking sites already running there"
            print(f"Booking sites: {sites['note']}.", flush=True)
            return None
        import uvicorn

        from ea_sites.server import create_app as create_sites

        server = uvicorn.Server(uvicorn.Config(create_sites(), host="127.0.0.1", port=sites_port,
                                               log_level="warning", lifespan="off"))
        threading.Thread(target=server.run, name="ea-sites", daemon=True).start()
        for _ in range(40):  # up to 2 s
            if server.started:
                break
            await asyncio.sleep(0.05)
        sites["running"] = bool(server.started)
        sites["note"] = (f"running at http://skyway.localhost:{sites_port}" if server.started
                         else f"couldn't start on port {sites_port}")
        print(f"Booking sites: {sites['note']}.", flush=True)
        return server

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        sites_server = await start_sites()
        yield
        await manager.close_all()
        if sites_server is not None:
            sites_server.should_exit = True

    routes: list[Any] = [
        Route("/", index),
        Route("/phone", phone_page),
        Route("/phone/{sid}", phone_page),
        Route("/api/config", config),
        Route("/api/sessions", create_session, methods=["POST"]),
        Route("/api/sessions/{sid}", get_session, methods=["GET"]),
        Route("/api/sessions/{sid}", delete_session, methods=["DELETE"]),
        Route("/api/sessions/{sid}/events", events),
        Route("/api/sessions/{sid}/messages", post_message, methods=["POST"]),
        Route("/api/sessions/{sid}/approvals/{call_id}", post_approval, methods=["POST"]),
        Route("/api/sessions/{sid}/connectors/{name}", post_connector, methods=["POST"]),
        Route("/api/sessions/{sid}/options", post_options, methods=["POST"]),
        Route("/api/sessions/{sid}/world", get_world),
        Route("/api/sessions/{sid}/outputs/{path:path}", get_output),
        Route("/api/sessions/{sid}/reset", reset_session, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=STATIC_DIR), name="static"),
    ]
    guide = paths.kit_root() / "site" / "dist"
    if guide.is_dir():
        routes.append(Mount("/guide", app=StaticFiles(directory=guide, html=True), name="guide"))
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.manager = manager
    app.state.app_name = name
    app.state.sites = sites
    return app
