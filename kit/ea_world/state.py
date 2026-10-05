"""Run directories and world state.

Each run works on its own copy of `world/` in `<run_dir>/state/`. Every writer (the tool server,
the mock booking sites, the app) takes an exclusive lock on `state/.lock` and writes atomically,
and the tool server re-reads state at the start of every call. See `world/SCHEMA.md`.
"""

import contextlib
import copy
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from . import paths
from .timeutil import parse_dt

try:  # POSIX only; WSL2 is the supported route on Windows.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger("ea_world.state")

STATE_FILES: dict[str, str] = {
    "persona": "persona.json",
    "contacts": "contacts.json",
    "calendars": "calendars.json",
    "inbox": "inbox.json",
    "sent": "sent.json",
    "docs": "docs/index.json",
    "flights": "travel/flights.json",
    "hotels": "travel/hotels.json",
    "restaurants": "restaurants.json",
    "search": "cassettes/search.json",
    "pages": "cassettes/pages.json",
    "clock": "clock.json",
    "drafts": "drafts.json",
    "outbox": "outbox.json",
    "bookings": "bookings.json",
    "holds": "holds.json",
    "connectors": "connectors.json",
    "reactions": "reactions.json",
    "meta": "meta.json",
    "fetched": "fetched.json",
    "idempotency": "idempotency.json",
}

CONNECTORS = ["email", "calendar", "contacts", "docs", "web", "travel"]

DEFAULTS: dict[str, Any] = {
    "sent": {"sent": []},
    "drafts": {"drafts": []},
    "outbox": {"sent": []},
    "bookings": {"bookings": []},
    "holds": {"holds": []},
    "connectors": {name: "connected" for name in CONNECTORS},
    "reactions": {"reactions": []},
    "meta": {"variants": [], "travelers": ["maya"]},
    "fetched": {"pages": []},
    "idempotency": {},
}

SKIP_ON_COPY = {"gold", "variants", "SCHEMA.md", "README.md"}


# ---------------------------------------------------------------- JSON helpers


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(default)


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    with contextlib.suppress(json.JSONDecodeError):
                        out.append(json.loads(line))
    except FileNotFoundError:
        pass
    return out


@contextlib.contextmanager
def file_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+") as fh:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------- variants


def variant_path(name: str, world: Path | None = None) -> Path:
    return (world or paths.world_dir()) / "variants" / f"{name}.yaml"


def load_variant(name: str, world: Path | None = None) -> dict[str, Any]:
    p = variant_path(name, world)
    if not p.exists():
        raise ValueError(f"Unknown world variant '{name}' (no {p})")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_variants(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [v for v in value if v]


_ADD_TARGETS = {
    # variant key -> (state name, list key)
    "inbox": ("inbox", "emails"),
    "maya_events": ("calendars", "maya"),
    "bookings": ("bookings", "bookings"),
    "contacts": ("contacts", "contacts"),
    "flights": ("flights", "flights"),
    "hotels": ("hotels", "hotels"),
    "restaurants": ("restaurants", "restaurants"),
    "sent": ("sent", "sent"),
}


def apply_variant(state: "State", variant: dict[str, Any]) -> None:
    if variant.get("clock"):
        clock = state.load("clock")
        clock["now"] = variant["clock"]
        state.save("clock", clock)
    for key, records in (variant.get("add") or {}).items():
        if key == "busy":
            cal = state.load("calendars")
            for person, blocks in (records or {}).items():
                cal.setdefault("busy", {}).setdefault(person, []).extend(blocks or [])
            state.save("calendars", cal)
            continue
        if key not in _ADD_TARGETS:
            raise ValueError(f"Variant '{variant.get('name')}' adds unknown collection '{key}'")
        name, list_key = _ADD_TARGETS[key]
        data = state.load(name)
        data.setdefault(list_key, []).extend(copy.deepcopy(records or []))
        state.save(name, data)
    for key, ids in (variant.get("remove") or {}).items():
        if key not in _ADD_TARGETS:
            raise ValueError(f"Variant '{variant.get('name')}' removes from unknown collection '{key}'")
        name, list_key = _ADD_TARGETS[key]
        data = state.load(name)
        drop = set(ids or [])
        data[list_key] = [r for r in data.get(list_key, []) if r.get("id") not in drop]
        state.save(name, data)
    if variant.get("reactions"):
        rx = state.load("reactions")
        rx["reactions"].extend(copy.deepcopy(variant["reactions"]))
        state.save("reactions", rx)
    if variant.get("travelers"):
        meta = state.load("meta")
        meta["travelers"] = list(variant["travelers"])
        state.save("meta", meta)


# ---------------------------------------------------------------- run directories


def new_run_dir(prefix: str = "interactive") -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = paths.runs_dir() / f"{prefix}-{stamp}"
    run_dir, n = base, 1
    while run_dir.exists():
        n += 1
        run_dir = Path(f"{base}-{n}")
    return run_dir


def resolve_run_dir() -> Path:
    env = os.environ.get("EA_RUN_DIR")
    return Path(env).resolve() if env else new_run_dir().resolve()


def init_run_dir(
    run_dir: Path, variants: list[str] | str | None = None, world: Path | None = None
) -> Path:
    """Create `<run_dir>/state/` from the world seed and the variants, if it doesn't exist yet."""
    run_dir = Path(run_dir).resolve()
    world = Path(world or paths.world_dir())
    state_dir = run_dir / "state"
    for sub in ("outputs/briefs", "outputs/decks", "outputs/browser", "approvals/pending", "approvals/decided"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    if state_dir.exists():
        return run_dir
    tmp = run_dir / ".state-init"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(world, tmp, ignore=lambda d, names: [n for n in names if n in SKIP_ON_COPY and Path(d) == world])
    os.replace(tmp, state_dir)
    st = State(run_dir)
    with st.session():
        persona = st.load("persona")
        st.save("clock", {"now": persona["now"], "timezone": persona["timezone"]})
        for name, default in DEFAULTS.items():
            if not (state_dir / STATE_FILES[name]).exists():
                st.save(name, copy.deepcopy(default))
        names = parse_variants(variants)
        for name in names:
            apply_variant(st, load_variant(name, world))
        meta = st.load("meta")
        meta["variants"] = names
        st.save("meta", meta)
    return run_dir


def write_current(run_dir: Path) -> None:
    cur = paths.current_file()
    cur.parent.mkdir(parents=True, exist_ok=True)
    cur.write_text(str(Path(run_dir).resolve()) + "\n", encoding="utf-8")


def read_current() -> Path | None:
    try:
        text = paths.current_file().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return Path(text) if text else None


def update_meta(run_dir: Path, **fields: Any) -> dict[str, Any]:
    p = Path(run_dir) / "meta.json"
    with file_lock(Path(run_dir) / ".meta.lock"):
        meta = read_json(p, {}) or {}
        meta.update(fields)
        write_json_atomic(p, meta)
    return meta


# ---------------------------------------------------------------- state access


class State:
    """Read and write the JSON files in `<run_dir>/state/`.

    Use `with state.session():` around a read-modify-write. Inside a session, loads are cached and
    saves are buffered; they are flushed atomically when the session ends without an exception.
    """

    def __init__(self, run_dir: Path, subdir: str = "state"):
        self.run_dir = Path(run_dir)
        self.state_dir = self.run_dir / subdir
        self._cache: dict[str, Any] = {}
        self._dirty: set[str] = set()
        self._depth = 0

    def path(self, name: str) -> Path:
        return self.state_dir / STATE_FILES[name]

    @contextlib.contextmanager
    def session(self) -> Iterator["State"]:
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return
        with file_lock(self.state_dir / ".lock"):
            self._depth = 1
            self._cache.clear()
            self._dirty.clear()
            try:
                yield self
                self.flush()
            finally:
                self._depth = 0
                self._cache.clear()
                self._dirty.clear()

    def flush(self) -> None:
        for name in sorted(self._dirty):
            write_json_atomic(self.path(name), self._cache[name])
        self._dirty.clear()

    def discard(self) -> None:
        for name in list(self._dirty):
            self._cache.pop(name, None)
        self._dirty.clear()

    def load(self, name: str) -> Any:
        if name not in self._cache:
            self._cache[name] = read_json(self.path(name), DEFAULTS.get(name))
        return self._cache[name]

    def save(self, name: str, data: Any) -> None:
        self._cache[name] = data
        self._dirty.add(name)
        if not self._depth:
            self.flush()

    # convenience -----------------------------------------------------------
    def now(self) -> datetime:
        return parse_dt(self.load("clock")["now"], "clock")

    def timezone(self) -> str:
        return self.load("clock").get("timezone", "America/Denver")


def world_snapshot(run_dir: Path) -> dict[str, Any]:
    """Everything the app's "Maya's world" tab shows."""
    st = State(run_dir)
    with st.session():
        cal = st.load("calendars")
        inbox = st.load("inbox")
        outputs: dict[str, list[str]] = {}
        for kind in ("briefs", "decks", "browser"):
            d = Path(run_dir) / "outputs" / kind
            outputs[kind] = sorted(p.name for p in d.glob("*") if p.is_file()) if d.exists() else []
        return {
            "clock": st.load("clock"),
            "calendar": sorted(cal.get("maya", []), key=lambda e: e.get("start", "")),
            "inbox": sorted(inbox.get("emails", []), key=lambda e: e.get("received_at", ""), reverse=True),
            "drafts": st.load("drafts").get("drafts", []),
            "outbox": st.load("outbox").get("sent", []),
            "bookings": st.load("bookings").get("bookings", []),
            "holds": st.load("holds").get("holds", []),
            "connectors": st.load("connectors"),
            "outputs": outputs,
        }


def copy_final_state(run_dir: Path) -> None:
    src = Path(run_dir) / "state"
    dst = Path(run_dir) / "final_state"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".lock", "*.tmp"))
