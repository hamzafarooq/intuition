"""The trace writer (`trace.jsonl`) and status-line parsing. Schema: spec/06-trace.md."""

import json
import re
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

STATUS_RX = re.compile(r"^\s*\**STATUS:\**\s*(done|partial|failed|waiting)\b\s*(?:[—–-]+\s*(.*))?$", re.IGNORECASE | re.MULTILINE)
OUTPUT_CAP = 4000


def parse_status(text: str | None) -> tuple[str, str]:
    """(status, reason) from the last STATUS line of a reply; ("missing", "") if there is none."""
    if not text:
        return "missing", ""
    matches = list(STATUS_RX.finditer(text))
    if not matches:
        return "missing", ""
    m = matches[-1]
    return m.group(1).lower(), (m.group(2) or "").strip().rstrip("*").strip()


def strip_status(text: str) -> str:
    return STATUS_RX.sub("", text or "").rstrip()


def truncate(value: Any, cap: int = OUTPUT_CAP) -> Any:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= cap:
        return value
    return text[:cap] + "…"


class TraceWriter:
    """Appends events with the common fields (v, seq, ts, run/case/trial/harness/variant, turn, agent)."""

    def __init__(self, path: Path, common: dict[str, Any] | None = None, on_event: Callable[[dict[str, Any]], None] | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.common = {"run_id": "", "case_id": "", "trial": 0, "harness": "claude-code", "variant": ""}
        self.common.update(common or {})
        self.on_event = on_event
        self.turn = 0
        self._lock = threading.Lock()
        self.seq = 0
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(line)
                    self.seq = max(self.seq, int(ev.get("seq", 0)))
                    self.turn = max(self.turn, int(ev.get("turn", 0)))
                except (ValueError, TypeError):
                    continue

    def emit(self, type_: str, agent: str = "main", parent_call_id: str | None = None, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self.seq += 1
            ev = {
                "v": 1,
                "seq": self.seq,
                "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                **self.common,
                "turn": self.turn,
                "agent": agent,
                "parent_call_id": parent_call_id,
                "type": type_,
                **fields,
            }
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:  # a UI callback must never break a trial
                pass
        return ev


def read_trace(path: Path) -> list[dict[str, Any]]:
    out = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        pass
    return out
