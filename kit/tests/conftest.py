"""Shared fixtures for the offline `ea_world` tests.

`run` gives a fresh run directory (default world), already configured in `ea_world.core`.
`make_run(variants=..., faults=..., seed=..., approval_mode=..., clock=...)` builds others.
Every helper goes through `core.call`, the same dispatcher the MCP server uses.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from ea_world import core, server, state  # noqa: F401  (server import registers every tool)

KIT = Path(__file__).resolve().parents[1]
WORLD = KIT / "world"

EA_ENV = ("EA_FAULTS", "EA_SEED", "EA_APPROVAL_MODE", "EA_IDEMPOTENCY", "EA_MODE", "EA_WORLD_VARIANT", "EA_RUN_DIR")


class Run:
    """A run directory plus small helpers for calling tools and reading what they left behind."""

    def __init__(self, run_dir: Path):
        self.dir = Path(run_dir)

    # tools ------------------------------------------------------------------
    def call(self, tool: str, **args: Any) -> Any:
        core.configure(self.dir)
        return core.call(tool, args)

    def error(self, tool: str, **args: Any) -> str:
        """Call a tool that must fail; return its ToolError message."""
        core.configure(self.dir)
        with pytest.raises(ToolError) as exc:
            core.call(tool, args)
        return str(exc.value)

    # state ------------------------------------------------------------------
    def path(self, name: str) -> Path:
        return self.dir / "state" / state.STATE_FILES[name]

    def load(self, name: str) -> Any:
        return json.loads(self.path(name).read_text(encoding="utf-8"))

    def save(self, name: str, data: Any) -> None:
        state.write_json_atomic(self.path(name), data)

    def events(self) -> dict[str, dict[str, Any]]:
        return {e["id"]: e for e in self.load("calendars")["maya"]}

    def inbox(self) -> list[dict[str, Any]]:
        return self.load("inbox")["emails"]

    def calls(self) -> list[dict[str, Any]]:
        return state.read_jsonl(self.dir / "calls.jsonl")

    def last_call(self) -> dict[str, Any]:
        return self.calls()[-1]

    def approvals(self) -> list[dict[str, Any]]:
        return state.read_jsonl(self.dir / "approvals.jsonl")

    def set_clock(self, now: str) -> None:
        clock = self.load("clock")
        clock["now"] = now
        self.save("clock", clock)

    def snapshot(self) -> dict[str, str]:
        """Every file under state/ (path -> text), to prove what a call did and didn't change."""
        root = self.dir / "state"
        return {
            str(p.relative_to(root)): p.read_text(encoding="utf-8")
            for p in sorted(root.rglob("*"))
            if p.is_file() and not p.name.startswith(".")
        }


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No EA_* leaks in from the developer's shell; the kit root points at a temp dir (runs/CURRENT).

    Requested by `make_run` (and so `run`), not autouse, so other suites in this folder are unaffected."""
    for name in EA_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EA_WORLD_DIR", str(WORLD))
    monkeypatch.setenv("EA_KIT_ROOT", str(tmp_path / "kit-root"))
    monkeypatch.setenv("EA_MODE", "mock")


@pytest.fixture
def make_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_env: None) -> Callable[..., Run]:
    counter = {"n": 0}

    def _make(
        variants: list[str] | str | None = None,
        faults: str = "",
        seed: str | int = "0",
        approval_mode: str | None = None,
        idempotency: str | None = None,
        clock: str | None = None,
        name: str | None = None,
    ) -> Run:
        counter["n"] += 1
        run_dir = tmp_path / (name or f"run{counter['n']}")
        if faults:
            monkeypatch.setenv("EA_FAULTS", faults)
        else:
            monkeypatch.delenv("EA_FAULTS", raising=False)
        monkeypatch.setenv("EA_SEED", str(seed))
        if approval_mode:
            monkeypatch.setenv("EA_APPROVAL_MODE", approval_mode)
        if idempotency:
            monkeypatch.setenv("EA_IDEMPOTENCY", idempotency)
        state.init_run_dir(run_dir, variants)
        core.configure(run_dir)
        r = Run(run_dir)
        if clock:
            r.set_clock(clock)
        return r

    return _make


@pytest.fixture
def run(make_run: Callable[..., Run]) -> Run:
    return make_run()


def codes(result: dict[str, Any]) -> list[str]:
    return [p["code"] for p in result["problems"]]


def problems(result: dict[str, Any], code: str) -> list[dict[str, Any]]:
    return [p for p in result["problems"] if p["code"] == code]
