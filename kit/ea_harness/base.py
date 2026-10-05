"""The one interface the runner and the app use to talk to a harness (Claude Code now; others later)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

TraceEvent = dict[str, Any]
Status = Literal["done", "partial", "failed", "waiting", "missing"]
StoppedBy = Literal["end_turn", "max_turns", "error", "usage_limit", "budget", "refusal"]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    api_equivalent_cost_usd: float = 0.0
    seconds: float = 0.0
    num_turns: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.api_equivalent_cost_usd += other.api_equivalent_cost_usd
        self.seconds += other.seconds
        self.num_turns += other.num_turns

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class TurnResult:
    final_text: str
    status: Status
    status_reason: str
    usage: Usage
    stopped_by: StoppedBy
    error: str = ""


@dataclass
class RunConfig:
    assistant_dir: Path
    on_event: Callable[[TraceEvent], None] = lambda e: None
    model: str = "opus"
    effort: str | None = None
    approval_mode: Literal["script", "app", "allow_all", "record_and_deny"] = "script"
    max_turns: int = 40
    idempotency: Literal["auto", "off"] = "off"
    env: dict[str, str] = field(default_factory=dict)
    browser: bool = False
    turn_timeout_s: float = 600.0
    trace_fields: dict[str, Any] = field(default_factory=dict)  # run_id, case_id, trial, harness, variant
    extra_args: list[str] = field(default_factory=list)


class Harness(Protocol):
    async def start(self, run_dir: Path, config: RunConfig) -> None: ...

    async def send(self, user_message: str, source: str = "maya") -> TurnResult: ...

    async def close(self) -> None: ...
