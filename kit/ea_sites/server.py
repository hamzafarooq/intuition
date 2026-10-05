"""The mock booking sites on port 8766: Skyway (flights), Stays (hotels) and Tables (restaurants).

Routed by host name (``skyway.localhost:8766``, ``stays.localhost:8766``, ``tables.localhost:8766``) and,
as a fallback, by path (``localhost:8766/skyway/…``, ``/stays/…``, ``/tables/…``). A bare
``localhost:8766/`` lists the three sites. See spec/16-browser-bookings.md.

    uv run ea-sites [--port 8766] [--host 127.0.0.1]
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Host, Mount, Route

from ea_world.state import read_json

from . import skyway, stays, tables
from .common import (
    ENV,
    PORT,
    SITE_HOSTS,
    SITES,
    NoWorld,
    RunDirResolver,
    SiteRequest,
    log_event,
    resolve_run_dir,
)

log = logging.getLogger("ea_sites")

INDEX_SITE = "index"


def _site_of(request: Request) -> str:
    host = (request.url.hostname or "").lower()
    if host in SITE_HOSTS:
        return SITE_HOSTS[host]
    first = request.url.path.strip("/").split("/", 1)[0]
    return first if first in SITES else INDEX_SITE


def _plain_page(request: Request, title: str, heading: str, message: str, status: int) -> HTMLResponse:
    """A page that doesn't need a world (no run directory, or an internal error)."""
    site = _site_of(request)
    meta = SITES.get(site, {"key": INDEX_SITE, "name": "Intuition booking sites", "kind": "", "accent": "#3b4252",
                            "tint": "#eceff4", "tagline": "", "host": "localhost"})
    root = request.scope.get("root_path", "") if site != INDEX_SITE else ""
    html = ENV.get_template("error.html").render(site=meta, base=root, title=title, heading=heading, message=message,
                                                 user=None, sr=None)
    return HTMLResponse(html, status_code=status)


async def index(request: Request) -> Response:
    if request.url.path not in ("", "/"):
        try:
            sr = SiteRequest(request, INDEX_SITE)
            return sr.not_found("Page not found", "There's no page at this address. Pick one of the booking sites.")
        except NoWorld:
            return _plain_page(request, "Page not found", "Page not found", "There's no page at this address.", 404)
    run_dir = None
    meta: dict[str, Any] = {}
    try:
        run_dir = resolve_run_dir(request)
        meta = read_json(run_dir / "state" / "meta.json", {}) or {}
    except NoWorld:
        pass
    port = request.url.port or PORT
    sites = [{**m, "host_url": f"{request.url.scheme}://{m['host']}:{port}/", "path_url": f"/{key}/"}
             for key, m in SITES.items()]
    html = ENV.get_template("index.html").render(
        site={"key": INDEX_SITE, "name": "Intuition booking sites", "accent": "#3b4252", "tint": "#eceff4", "kind": ""},
        base="", title="Booking sites", sites=sites, run_dir=run_dir, variants=meta.get("variants", []), user=None, sr=None)
    if run_dir is not None:
        log_event(run_dir, INDEX_SITE, "page_view", "/", {"title": "Booking sites", "status": 200, "url": str(request.url)})
    return HTMLResponse(html)


async def _no_world(request: Request, exc: Exception) -> Response:
    return _plain_page(request, "No world loaded", "No world is loaded",
                       f"{exc} Start the Intuition app or a run, then reload this page.", 503)


async def _server_error(request: Request, exc: Exception) -> Response:
    log.exception("ea_sites error on %s", request.url, exc_info=exc)
    return _plain_page(request, "Something went wrong", "Something went wrong",
                       "The site couldn't show this page. Go back and try again.", 500)


def create_app(run_dir_resolver: RunDirResolver | Path | str | None = None) -> Starlette:
    """Build the app. `run_dir_resolver` is a callable returning the run directory (re-evaluated on every
    request), or a fixed path; by default EA_RUN_DIR, else runs/CURRENT."""
    if isinstance(run_dir_resolver, (str, Path)):
        fixed = Path(run_dir_resolver)

        def resolver() -> Path:
            return fixed
    else:
        resolver = run_dir_resolver  # type: ignore[assignment]

    routers = {"skyway": skyway.router(), "stays": stays.router(), "tables": tables.router()}
    routes: list[Any] = [Host(SITES[key]["host"], app=r) for key, r in routers.items()]
    routes += [Mount(f"/{key}", app=r) for key, r in routers.items()]
    routes += [Route(f"/{key}", lambda request, k=key: RedirectResponse(f"/{k}/", status_code=307)) for key in routers]
    routes += [Route("/", index), Route("/{path:path}", index, methods=["GET", "POST"])]
    app = Starlette(routes=routes, exception_handlers={NoWorld: _no_world, 500: _server_error})
    app.state.run_dir_resolver = resolver
    return app


app = create_app()


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(prog="ea-sites", description="Run the mock booking sites (Skyway, Stays, Tables).")
    parser.add_argument("--port", type=int, default=int(os.environ.get("EA_SITES_PORT", PORT)))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--run-dir", default=None, help="Serve this run directory (default: EA_RUN_DIR, else runs/CURRENT)")
    parser.add_argument("--log-level", default="warning")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    target = create_app(args.run_dir) if args.run_dir else app
    print(f"Booking sites on http://skyway.localhost:{args.port}  http://stays.localhost:{args.port}  "
          f"http://tables.localhost:{args.port}  (or http://localhost:{args.port}/)", flush=True)
    uvicorn.run(target, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
