"""Sessions: one run directory, one assistant overlay, one harness and one event log per conversation.

The harness calls `on_event` live (from the same event loop) with trace events (spec/06-trace.md).
The session adds a few app events of its own and fans everything out to SSE subscribers:

    message_queued  {message_id, text, queued}      Maya pressed send
    turn_start      {message_id, turn}               the message went to the harness
    session_state   {busy, queued, turn, world_now}  broadcast only (not stored)
    world_changed   {reason}                         broadcast only: refetch the world view
    session_started {options, run_dir, ...}          first event of a session (and after a reset)
    reset           {}                               the world and conversation were reset
    option_changed  {auto_approve}                   an option changed mid-session

Every stored event gets `_i` (its place in the session's log, used as the SSE id) and `world_ts`
(the world clock: the run's starting time plus wall time elapsed since the session started).
"""

import asyncio
import contextlib
import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ea_harness.base import RunConfig
from ea_world import (  # noqa: F401 (register tools)
    approvals,
    calendar,
    connectors,
    contacts,
    core,
    docs,
    email,
    outputs,
    paths,
    state,
    travel,
    verifiers,
    web,
)
from ea_world.state import CONNECTORS

from .views import describe_action

log = logging.getLogger("ea_app.sessions")

HarnessFactory = Callable[[str], Any]

# Tools whose successful result changes something the world view shows.
WORLD_TOOLS = {name for name, spec in core.REGISTRY.items() if spec.write} | {
    "email_draft", "email_update_draft", "email_read", "connector_disconnect",
}  # fmt: skip
CALL_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,160}$")
CONNECTOR_LABELS = {"email": "Email", "calendar": "Calendar", "contacts": "Contacts", "docs": "Documents",
                    "web": "Web", "travel": "Travel"}
BROADCAST_ONLY = {"session_state", "world_changed"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def bare_tool(name: str) -> str:
    """`mcp__ea-world__email_send` -> `email_send`; `mcp__browser__click` -> `click`."""
    return name.rsplit("__", 1)[-1] if name.startswith("mcp__") else name


def default_harness_factory(name: str) -> Any:
    if name == "fake":
        from .fake import FakeHarness

        return FakeHarness()
    if name == "claude_code":
        from ea_harness.claude_code import ClaudeCodeHarness  # written by the lead; imported lazily

        return ClaudeCodeHarness()
    raise ValueError(f"Unknown harness '{name}'")


class SessionError(Exception):
    """A session couldn't start (shown to the user as is)."""


@dataclass
class SessionOptions:
    harness: str = "claude_code"
    search_mode: str = "mock"
    auto_approve: bool = True
    model: str = "opus"
    browser: bool = False
    connectors: dict[str, bool] = field(default_factory=lambda: {c: True for c in CONNECTORS})
    variants: dict[str, str] = field(default_factory=dict)  # harness configuration variants (overlay)
    world: list[str] = field(default_factory=list)  # world variants (state.init_run_dir)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class Session:
    def __init__(self, sid: str, options: SessionOptions, factory: HarnessFactory, run_prefix: str = "app"):
        self.id = sid
        self.options = options
        self._factory = factory
        self._prefix = run_prefix
        self.created_at = now_iso()
        self.run_dir: Path | None = None
        self.assistant_dir: Path | None = None
        self.harness: Any = None
        self.events: list[dict[str, Any]] = []
        self.turn = 0
        self.generation = 0
        self._next_i = 0
        self._subscribers: set[asyncio.Queue[dict[str, Any] | None]] = set()
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._pending = 0  # messages queued or running
        self._busy = False
        self._notes: list[str] = []  # told to the assistant with Maya's next message
        self._seen_approvals: set[tuple[str, str]] = set()
        self._approvals_offset = 0
        self._turn_ended = False
        self._last_text = ""
        self._worker: asyncio.Task[None] | None = None
        self._watcher: asyncio.Task[None] | None = None
        self._wall_start = time.time()
        self._world_start: datetime | None = None
        self.closed = False

    # ------------------------------------------------------------ properties

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def queued(self) -> int:
        return max(0, self._pending - (1 if self._busy else 0))

    @property
    def idle(self) -> bool:
        return self._pending == 0

    def world_now(self) -> str:
        if self._world_start is None:
            return ""
        return (self._world_start + timedelta(seconds=time.time() - self._wall_start)).isoformat(timespec="seconds")

    def info(self) -> dict[str, Any]:
        return {
            "session_id": self.id,
            "options": self.options.as_dict(),
            "run_dir": str(self.run_dir) if self.run_dir else "",
            "run_name": self.run_dir.name if self.run_dir else "",
            "busy": self._busy,
            "queued": self.queued,
            "turn": self.turn,
            "world_now": self.world_now(),
            "created_at": self.created_at,
            "generation": self.generation,
        }

    def state_event(self) -> dict[str, Any]:
        return {"type": "session_state", "busy": self._busy, "queued": self.queued, "turn": self.turn,
                "world_now": self.world_now(), "generation": self.generation}

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self.run_dir = state.new_run_dir(self._prefix).resolve()
        try:
            await asyncio.to_thread(state.init_run_dir, self.run_dir, self.options.world or None)
        except ValueError as exc:
            raise SessionError(str(exc)) from exc
        state.write_current(self.run_dir)
        clock = state.read_json(self.run_dir / "state" / "clock.json", {}) or {}
        try:
            self._world_start = datetime.fromisoformat(clock["now"])
        except (KeyError, ValueError):
            self._world_start = None
        self._wall_start = time.time()
        self._approvals_offset = 0
        self._write_approval_context("")
        self.assistant_dir = await self._build_overlay()
        self.record({"type": "session_started", "options": self.options.as_dict(), "run_dir": str(self.run_dir),
                     "run_name": self.run_dir.name, "world_start": clock.get("now", ""),
                     "timezone": clock.get("timezone", "")}, persist=True)
        try:
            self.harness = self._factory(self.options.harness)
            await self.harness.start(self.run_dir, self._run_config())
        except Exception as exc:
            log.exception("harness start failed")
            self.record({"type": "error", "where": "harness_start", "message": str(exc) or type(exc).__name__}, persist=True)
            raise SessionError(f"The {self.options.harness} harness didn't start: {exc}") from exc
        off = [name for name, on in self.options.connectors.items() if not on]
        for name in off:
            await self.disconnect(name, at_setup=True)
        self._worker = asyncio.create_task(self._run_worker(), name=f"session-{self.id}-worker")
        self._watcher = asyncio.create_task(self._watch_approvals(), name=f"session-{self.id}-approvals")
        self._broadcast(self.state_event())

    def _run_config(self) -> RunConfig:
        assert self.run_dir is not None
        env = {
            "EA_MODE": "live" if self.options.search_mode == "live" else "mock",
            "EA_RUN_DIR": str(self.run_dir),
            "EA_APPROVAL_MODE": "app",
            "EA_BROWSER": "on" if self.options.browser else "off",
        }
        if self.options.world:
            env["EA_WORLD_VARIANT"] = ",".join(self.options.world)
        return RunConfig(
            assistant_dir=self.assistant_dir or paths.kit_root() / "assistant",
            on_event=self._on_harness_event,
            model=self.options.model,
            approval_mode="app",
            env=env,
            browser=self.options.browser,
            trace_fields={"harness": self.options.harness},
        )

    async def _build_overlay(self) -> Path:
        base = paths.kit_root() / "assistant"
        if self.options.harness == "fake":
            return base
        try:
            from ea_harness.overlay import build_overlay  # written by the lead; imported lazily
        except ImportError:
            self.record({"type": "error", "where": "overlay",
                         "message": "ea_harness.overlay isn't available yet; using assistant/ unchanged."}, persist=True)
            return base
        assert self.run_dir is not None
        return Path(await asyncio.to_thread(build_overlay, self.run_dir, dict(self.options.variants), self.options.browser))

    async def _stop(self) -> None:
        for task in (self._worker, self._watcher):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._worker = self._watcher = None
        self._release_pending("The session was reset.")
        if self.harness is not None:
            try:
                await asyncio.wait_for(self.harness.close(), timeout=15)
            except Exception:
                log.exception("harness close failed")
            self.harness = None

    def _release_pending(self, reason: str) -> None:
        """Deny every approval still waiting, so a tool server blocked on it lets go."""
        if not self.run_dir:
            return
        pending = self.run_dir / "approvals" / "pending"
        for f in pending.glob("*.json") if pending.exists() else []:
            decided = self.run_dir / "approvals" / "decided" / f.name
            if not decided.exists():
                with contextlib.suppress(OSError):
                    state.write_json_atomic(decided, {"decision": "deny", "by": "app", "reason": reason})

    async def reset(self) -> None:
        """New run directory and harness; the conversation starts over."""
        await self._stop()
        self.events.clear()
        self._seen_approvals.clear()
        self._notes.clear()
        self._queue = asyncio.Queue()
        self._pending = 0
        self._busy = False
        self.turn = 0
        self.generation += 1
        self.record({"type": "reset", "generation": self.generation})
        await self.start()

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self._stop()
        for q in list(self._subscribers):
            q.put_nowait(None)

    # ------------------------------------------------------------ events

    def subscribe(self) -> "asyncio.Queue[dict[str, Any] | None]":
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: "asyncio.Queue[dict[str, Any] | None]") -> None:
        self._subscribers.discard(q)

    def _broadcast(self, ev: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            q.put_nowait(ev)

    def _on_harness_event(self, event: dict[str, Any]) -> None:
        try:
            self.record(event)
        except Exception:  # the harness must never die because the UI couldn't take an event
            log.exception("couldn't record event %r", event.get("type"))

    def record(self, event: dict[str, Any], persist: bool = False) -> dict[str, Any] | None:
        ev = dict(event)
        etype = ev.get("type", "")
        if etype in BROADCAST_ONLY:
            self._broadcast(ev)
            return ev
        if etype in ("approval_request", "approval_response") or (etype == "signal" and ev.get("kind") == "deny"):
            # The app and the harness can both report these; keep the first.
            key = (etype if etype != "signal" else "signal:deny", str(ev.get("call_id") if etype != "signal" else ev.get("object_id")))
            if key in self._seen_approvals:
                return None
            self._seen_approvals.add(key)
            if etype == "approval_request" and "card" not in ev and self.run_dir:
                ev["card"] = describe_action(self.run_dir, bare_tool(str(ev.get("tool", ""))), ev.get("input") or {})
        if etype == "turn_end":
            self._turn_ended = True
        ev.setdefault("ts", now_iso())
        ev.setdefault("turn", self.turn)
        ev.setdefault("agent", "main")
        ev["world_ts"] = self.world_now()
        ev["_i"] = self._next_i
        self._next_i += 1
        self.events.append(ev)
        if persist and self.run_dir:
            with contextlib.suppress(OSError):
                state.append_jsonl(self.run_dir / "app_events.jsonl", ev)
        self._broadcast(ev)
        reason = self._world_reason(ev)
        if reason:
            self._broadcast({"type": "world_changed", "reason": reason})
        return ev

    @staticmethod
    def _world_reason(ev: dict[str, Any]) -> str | None:
        etype = ev.get("type")
        tool = bare_tool(str(ev.get("tool", "")))
        if etype == "tool_result" and ev.get("ok") is not False and (tool in WORLD_TOOLS or "screenshot" in tool):
            return f"tool:{tool}"
        if etype == "signal":
            return f"signal:{ev.get('kind', '')}"
        if etype == "browser_step" and "screenshot" in str(ev.get("action") or ev.get("tool") or ""):
            return "browser:screenshot"
        if etype == "site_event" and "hold" in str(ev.get("kind") or ev.get("event") or ""):
            return "site:hold"
        if etype in ("turn_end", "approval_response"):
            return etype
        return None

    # ------------------------------------------------------------ messages and turns

    def submit(self, text: str) -> tuple[str, bool]:
        mid = "m-" + secrets.token_hex(4)
        queued = self._pending > 0
        self._pending += 1
        self.record({"type": "message_queued", "message_id": mid, "text": text, "queued": queued, "turn": None},
                    persist=True)
        self._queue.put_nowait((mid, text))
        self._broadcast(self.state_event())
        return mid, queued

    async def _run_worker(self) -> None:
        while True:
            mid, text = await self._queue.get()
            try:
                await self._run_turn(mid, text)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("turn failed")
            finally:
                self._pending = max(0, self._pending - 1)
                self._busy = False
                self._broadcast(self.state_event())

    async def _run_turn(self, mid: str, text: str) -> None:
        self._busy = True
        self.turn += 1
        self._turn_ended = False
        self._last_text = text
        self._write_approval_context(text)
        self.record({"type": "turn_start", "message_id": mid, "turn": self.turn}, persist=True)
        self._broadcast(self.state_event())
        send_text = text
        if self._notes:
            send_text = "\n".join(self._notes) + "\n\n" + text
            self._notes.clear()
        stopped_by = "end_turn"
        try:
            result = await self.harness.send(send_text)
            stopped_by = getattr(result, "stopped_by", "end_turn") or "end_turn"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("harness.send failed")
            self.record({"type": "error", "where": "harness", "message": str(exc) or type(exc).__name__}, persist=True)
            stopped_by = "error"
        if not self._turn_ended:
            self.record({"type": "turn_end", "stopped_by": stopped_by}, persist=True)

    def _write_approval_context(self, text: str) -> None:
        if not self.run_dir:
            return
        state.write_json_atomic(self.run_dir / "approval_context.json", {
            "auto_approve": bool(self.options.auto_approve),
            "explicit_yes": approvals.is_explicit_yes(text),
            "last_maya_message": text,
        })

    def set_auto_approve(self, on: bool) -> None:
        self.options.auto_approve = bool(on)
        self._write_approval_context(self._last_text)
        self.record({"type": "option_changed", "auto_approve": self.options.auto_approve}, persist=True)

    # ------------------------------------------------------------ approvals

    def _known_request(self, call_id: str) -> dict[str, Any] | None:
        for e in reversed(self.events):
            if e.get("type") == "approval_request" and str(e.get("call_id")) == call_id:
                return e
        return None

    def decide(self, call_id: str, decision: str) -> str:
        """Write the decision file the tool server polls. Returns 'ok' or 'already'."""
        if not CALL_ID_RE.match(call_id or ""):
            raise KeyError(call_id)
        if decision not in ("allow", "deny"):
            raise ValueError("decision must be 'allow' or 'deny'")
        assert self.run_dir is not None
        pending = self.run_dir / "approvals" / "pending" / f"{call_id}.json"
        request = self._known_request(call_id)
        if request is None and not pending.exists():
            raise KeyError(call_id)
        if ("approval_response", call_id) in self._seen_approvals:
            return "already"
        decided = self.run_dir / "approvals" / "decided" / f"{call_id}.json"
        if decided.exists():
            return "already"
        reason = "Approved in Intuition" if decision == "allow" else "Maya denied this in the app."
        state.write_json_atomic(decided, {"decision": decision, "by": "human", "reason": reason})
        if decision == "deny":
            summary = (request or {}).get("summary") or (request or {}).get("tool") or call_id
            self.record({"type": "signal", "kind": "deny", "object_id": call_id, "source": "app",
                         "detail": f"Maya denied: {summary}"}, persist=True)
        return "ok"

    async def _watch_approvals(self) -> None:
        """Tail `<run_dir>/approvals.jsonl` so approval cards appear even if the harness only reports
        approvals after the turn. Duplicates of what the harness sends are dropped by call_id."""
        while True:
            try:
                self._read_approvals()
            except Exception:
                log.exception("approval watcher")
            await asyncio.sleep(0.25)

    def _read_approvals(self) -> None:
        if not self.run_dir:
            return
        p = self.run_dir / "approvals.jsonl"
        try:
            with open(p, "rb") as f:
                f.seek(self._approvals_offset)
                chunk = f.read()
        except FileNotFoundError:
            return
        if not chunk:
            return
        end = chunk.rfind(b"\n")
        if end < 0:
            return
        self._approvals_offset += end + 1
        for line in chunk[: end + 1].splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            call_id = str(rec.get("call_id", ""))
            if rec.get("kind") == "request":
                self.record({"type": "approval_request", "call_id": call_id, "tool": bare_tool(str(rec.get("tool", ""))),
                             "input": rec.get("input") or {}, "summary": rec.get("summary", ""),
                             "source": "approvals.jsonl"})
            elif rec.get("kind") == "response":
                self.record({"type": "approval_response", "call_id": call_id, "decision": rec.get("decision", ""),
                             "by": rec.get("by", ""), "reason": rec.get("reason", ""), "source": "approvals.jsonl"})

    # ------------------------------------------------------------ connectors

    async def disconnect(self, name: str, at_setup: bool = False) -> str:
        if name not in CONNECTORS:
            raise KeyError(name)
        assert self.run_dir is not None
        current = state.read_json(self.run_dir / "state" / "connectors.json", {}) or {}
        if current.get(name) == "disconnected":
            return "disconnected"
        await asyncio.to_thread(core.call, "connector_disconnect", {"name": name}, run=self.run_dir)
        label = CONNECTOR_LABELS.get(name, name)
        detail = f"{label} wasn't connected at setup" if at_setup else f"Maya turned off the {label} connector"
        self.record({"type": "signal", "kind": "disconnect", "object_id": name, "detail": detail, "source": "app"},
                    persist=True)
        if at_setup:
            self._notes.append(f"[Intuition app: Maya didn't connect {name}. Those tools are off for this session.]")
        else:
            self._notes.append(f"[Intuition app: Maya turned off the {name} connector. It stays off for the rest "
                               "of this session.]")
        return "disconnected"


class SessionManager:
    def __init__(self, factory: HarnessFactory | None = None):
        self.factory = factory or default_harness_factory
        self.sessions: dict[str, Session] = {}

    async def create(self, options: SessionOptions) -> Session:
        sid = secrets.token_urlsafe(8)
        s = Session(sid, options, self.factory)
        try:
            await s.start()
        except BaseException:
            await s.close()
            raise
        self.sessions[sid] = s
        return s

    def get(self, sid: str) -> Session | None:
        return self.sessions.get(sid)

    async def remove(self, sid: str) -> bool:
        s = self.sessions.pop(sid, None)
        if s is None:
            return False
        await s.close()
        return True

    async def close_all(self) -> None:
        for sid in list(self.sessions):
            await self.remove(sid)
