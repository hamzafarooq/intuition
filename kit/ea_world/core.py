"""The tool dispatcher: connector checks, faults, idempotency, state sessions and the call log.

Every tool is a plain function registered with `@tool(...)`. `call(name, args)` runs it the way the
MCP server does, so unit tests, the app and the server share one code path.
"""

import contextvars
import hashlib
import json
import logging
import os
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from .state import State, append_jsonl, file_lock, read_json, read_jsonl, write_json_atomic

log = logging.getLogger("ea_world")

RESULT_CAP = 4000

SERVICE_NAMES = {
    "calendar": "the calendar service",
    "email": "the email service",
    "contacts": "the contacts service",
    "docs": "the documents service",
    "web": "the web service",
    "travel": "the travel service",
    "outputs": "the documents service",
    "verify": "the checking service",
    "core": "the workspace service",
}


@dataclass
class ToolSpec:
    name: str
    fn: Callable[..., Any]
    group: str
    write: bool = False


REGISTRY: dict[str, ToolSpec] = {}


def tool(group: str, write: bool = False) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        REGISTRY[fn.__name__] = ToolSpec(fn.__name__, fn, group, write)
        return fn

    return deco


# ---------------------------------------------------------------- per-call context


@dataclass
class Ctx:
    run_dir: Path
    state: State
    tool: str
    args: dict[str, Any]
    side_effects: list[dict[str, Any]] = field(default_factory=list)
    signals: list[dict[str, Any]] = field(default_factory=list)
    object_id: str | None = None

    def effect(self, kind: str, obj_id: str) -> None:
        self.side_effects.append({"kind": kind, "id": obj_id})

    def signal(self, kind: str, object_id: str | None, detail: str) -> None:
        self.signals.append({"kind": kind, "object_id": object_id, "detail": detail})


_CTX: contextvars.ContextVar[Ctx] = contextvars.ContextVar("ea_world_ctx")
_RUN_DIR: Path | None = None


def configure(run_dir: Path) -> None:
    """Point the dispatcher at a run directory (the server does this at start-up)."""
    global _RUN_DIR
    _RUN_DIR = Path(run_dir).resolve()


def run_dir() -> Path:
    if _RUN_DIR is None:
        raise RuntimeError("ea_world.core.configure(run_dir) hasn't been called")
    return _RUN_DIR


def ctx() -> Ctx:
    return _CTX.get()


def st() -> State:
    return _CTX.get().state


# ---------------------------------------------------------------- faults


@dataclass
class FaultRule:
    tool: str
    fault: str
    selector: str  # "every", "<n>", or "p=<float>"


FAULTS = {"timeout_before_write", "timeout_after_write", "rate_limit", "malformed", "error_500"}


def parse_faults(spec: str | None) -> list[FaultRule]:
    rules: list[FaultRule] = []
    for part in (spec or "").split(";"):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":")
        if len(bits) < 2:
            raise ValueError(f"Bad fault spec '{part}': use tool:fault[:selector]")
        tool_name, fault = bits[0].strip(), bits[1].strip()
        selector = ":".join(bits[2:]).strip() or "every"
        if fault not in FAULTS:
            raise ValueError(f"Unknown fault '{fault}' in '{part}'")
        if not (selector == "every" or selector.isdigit() or selector.startswith("p=")):
            raise ValueError(f"Bad fault selector '{selector}' in '{part}'")
        rules.append(FaultRule(tool_name, fault, selector))
    return rules


def pick_fault(rules: list[FaultRule], tool_name: str, nth: int, seed: str) -> str | None:
    for r in rules:
        if r.tool != tool_name:
            continue
        if r.selector == "every":
            return r.fault
        if r.selector.isdigit() and int(r.selector) == nth:
            return r.fault
        if r.selector.startswith("p="):
            p = float(r.selector[2:])
            if random.Random(f"{seed}:{tool_name}:{nth}").random() < p:
                return r.fault
    return None


def fault_error(fault: str, group: str) -> ToolError:
    if fault in ("timeout_before_write", "timeout_after_write"):
        return ToolError(f"Timed out contacting {SERVICE_NAMES.get(group, 'the service')}. The request may not have completed.")
    if fault == "rate_limit":
        return ToolError("Rate limited. Retry after 2 seconds.")
    return ToolError("Internal server error")


# ---------------------------------------------------------------- helpers


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def idempotency_key(tool_name: str, args: dict[str, Any]) -> str:
    clean = {k: v for k, v in args.items() if k != "idempotency_key" and v is not None}
    return hashlib.sha256((tool_name + canonical_json(clean)).encode()).hexdigest()[:16]


def _truncate(value: Any) -> Any:
    text = value if isinstance(value, str) else canonical_json(value)
    if len(text) <= RESULT_CAP:
        return value
    return text[:RESULT_CAP] + "…"


def _connector_state(run: Path) -> dict[str, str]:
    return read_json(run / "state" / "connectors.json", {}) or {}


def calls_path(run: Path) -> Path:
    return run / "calls.jsonl"


def _count_calls(run: Path, tool_name: str) -> int:
    return sum(1 for c in read_jsonl(calls_path(run)) if c.get("tool") == tool_name)


def _log_call(run: Path, record: dict[str, Any]) -> dict[str, Any]:
    with file_lock(run / ".calls.lock"):
        seq = len(read_jsonl(calls_path(run))) + 1
        record = {"seq": seq, "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"), **record}
        append_jsonl(calls_path(run), record)
    return record


# ---------------------------------------------------------------- dispatch


def call(name: str, args: dict[str, Any] | None = None, run: Path | None = None) -> Any:
    """Run one tool call against the run directory, with logging, faults and idempotency."""
    args = {k: v for k, v in (args or {}).items() if v is not None}
    run = Path(run or run_dir())
    spec = REGISTRY.get(name)
    if spec is None:
        raise ToolError(f"Unknown tool: {name}")

    record: dict[str, Any] = {"tool": name, "args": args, "ok": False}
    fault: str | None = None
    try:
        group = spec.group
        if group not in ("core", "verify", "outputs"):
            if _connector_state(run).get(group) == "disconnected":
                raise ToolError(f"The {group} connector is disconnected by the user.")

        nth = _count_calls(run, name) + 1
        fault = pick_fault(parse_faults(os.environ.get("EA_FAULTS")), name, nth, os.environ.get("EA_SEED", "0"))
        if fault:
            record["fault"] = fault
        if fault in ("timeout_before_write", "rate_limit", "error_500"):
            raise fault_error(fault, group)
        if fault == "malformed":
            record.update(ok=True, result={"raw": "<<<garbled response 0x1f…>>>"})
            return {"raw": "<<<garbled response 0x1f…>>>"}

        state = State(run)
        c = Ctx(run, state, name, args)
        token = _CTX.set(c)
        try:
            with state.session():
                key = args.get("idempotency_key") if spec.write else None
                if key:
                    store = read_json(run / "state" / "idempotency.json", {}) or {}
                    slot = f"{name}:{key}"
                    if slot in store:
                        record.update(ok=True, result=store[slot], idempotent_replay=True)
                        record["object_id"] = store[slot].get("_object_id") if isinstance(store[slot], dict) else None
                        result = store[slot]
                        if isinstance(result, dict):
                            result = {k: v for k, v in result.items() if k != "_object_id"}
                        return result
                call_args = {k: v for k, v in args.items() if k != "idempotency_key"}
                try:
                    result = spec.fn(**call_args)
                except TypeError as exc:
                    state.discard()
                    raise ToolError(f"Bad arguments for {name}: {exc}") from exc
                except ToolError:
                    state.discard()
                    raise
                except Exception as exc:  # never leak a bare exception through MCP
                    state.discard()
                    log.exception("tool %s failed", name)
                    raise ToolError(f"{name} failed: {exc}") from exc
                if key:
                    store = read_json(run / "state" / "idempotency.json", {}) or {}
                    stored = dict(result) if isinstance(result, dict) else result
                    if isinstance(stored, dict):
                        stored["_object_id"] = c.object_id
                    store[f"{name}:{key}"] = stored
                    write_json_atomic(run / "state" / "idempotency.json", store)
        finally:
            _CTX.reset(token)
        record.update(ok=True, result=_truncate(result))
        record["side_effects"] = c.side_effects
        record["signals"] = c.signals
        record["object_id"] = c.object_id
        if fault == "timeout_after_write":
            record["ok"] = False
            record.pop("result", None)
            raise fault_error(fault, group)
        return result
    except ToolError as exc:
        record["ok"] = False
        record["error"] = str(exc)
        raise
    finally:
        _log_call(run, record)

