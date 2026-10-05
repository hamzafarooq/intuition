"""Build a graded-trial directory from a checked-in fixture trace (`evals/fixtures/traces/<name>/`).

A fixture holds what a real trial leaves behind, minus the world copies:

    case.txt              the golden case id it is graded against (one line, e.g. S01)
    trace.jsonl           the harness trace (StreamMapper shapes)
    calls.jsonl           the tool server's call log (core.call shapes)
    approvals.jsonl       optional: the approval_prompt log
    final_patch.json      optional: edits that turn the initial world into the final one
    outputs/              optional: saved briefs and decks
    expected_grades.yaml  the verdicts the graders must give (tests/test_checks.py)

`final_patch.json` is a list of edits applied to the run's `state/`:

    {"file": "calendars.json", "op": "append", "path": ["maya"], "value": {...}}
    {"file": "calendars.json", "op": "set",    "path": ["maya", {"id": "ev-n1"}, "status"], "value": "cancelled"}
    {"file": "drafts.json",    "op": "update", "path": ["drafts", {"id": "dr-1"}], "value": {"status": "sent"}}

`path` is a list of segments: a string is a dict key; a one-key dict such as {"id": "ev-n1"} selects the
list element whose field has that value. `append` adds to the list at `path` (creating it if missing),
`set` replaces the value at `path` (an empty path replaces the whole file), `update` merges a dict into
the dict at `path`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from ea_evals.cases import load_all
from ea_world import state as world_state

KIT = Path(__file__).resolve().parents[1]
FIXTURES = KIT / "evals" / "fixtures" / "traces"
LOGS = ("trace.jsonl", "calls.jsonl", "approvals.jsonl", "stopcheck.jsonl")

_CASES: dict[str, dict[str, Any]] | None = None


def cases() -> dict[str, dict[str, Any]]:
    global _CASES
    if _CASES is None:
        _CASES = load_all()
    return _CASES


def fixture_names() -> list[str]:
    return sorted(p.name for p in FIXTURES.iterdir() if p.is_dir() and (p / "case.txt").exists())


def case_id(fixture_dir: Path) -> str:
    return (Path(fixture_dir) / "case.txt").read_text(encoding="utf-8").strip().splitlines()[0].strip()


def load_case(fixture_dir: Path) -> dict[str, Any]:
    return cases()[case_id(fixture_dir)]


def expected(fixture_dir: Path) -> dict[str, Any]:
    with open(Path(fixture_dir) / "expected_grades.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------- patches


def _select(items: list[Any], seg: dict[str, Any]) -> int:
    (key, value), = seg.items()
    for i, item in enumerate(items):
        if isinstance(item, dict) and item.get(key) == value:
            return i
    raise KeyError(f"no element with {key}={value!r}")


def _step(node: Any, seg: Any, create: Any = None) -> Any:
    if isinstance(seg, dict):
        return node[_select(node, seg)]
    if seg not in node:
        if create is None:
            raise KeyError(seg)
        node[seg] = create
    return node[seg]


def apply_edit(data: Any, edit: dict[str, Any]) -> Any:
    """Apply one edit to a loaded JSON document; returns the (possibly replaced) document."""
    path, op, value = list(edit.get("path") or []), edit["op"], edit.get("value")
    if op == "set" and not path:
        return value
    if op == "set":
        node = data
        for seg in path[:-1]:
            node = _step(node, seg, {})
        last = path[-1]
        if isinstance(last, dict):
            node[_select(node, last)] = value
        else:
            node[last] = value
        return data
    node = data
    for i, seg in enumerate(path):
        node = _step(node, seg, [] if (op == "append" and i == len(path) - 1) else {})
    if op == "append":
        if not isinstance(node, list):
            raise TypeError(f"append target {path} is not a list")
        node.append(value)
    elif op == "update":
        if not isinstance(node, dict) or not isinstance(value, dict):
            raise TypeError(f"update needs a dict at {path}")
        node.update(value)
    else:
        raise ValueError(f"unknown op {op!r}")
    return data


def apply_patch(state_dir: Path, patch: list[dict[str, Any]]) -> None:
    docs: dict[str, Any] = {}
    for edit in patch:
        name = edit["file"]
        if name not in docs:
            p = state_dir / name
            docs[name] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        docs[name] = apply_edit(docs[name], edit)
    for name, data in docs.items():
        world_state.write_json_atomic(state_dir / name, data)


# ---------------------------------------------------------------- materialize


def materialize(fixture_dir: Path, tmp_path: Path) -> Path:
    """A trial directory (initial_state/, state/, final_state/, logs, outputs/) for the fixture."""
    fixture_dir = Path(fixture_dir)
    case = load_case(fixture_dir)
    trial = Path(tmp_path) / fixture_dir.name / "t1"
    if trial.exists():
        shutil.rmtree(trial)
    world_state.init_run_dir(trial, (case.get("world") or {}).get("variants") or [])
    shutil.copytree(trial / "state", trial / "initial_state", ignore=shutil.ignore_patterns(".lock"))
    patch_file = fixture_dir / "final_patch.json"
    if patch_file.exists():
        apply_patch(trial / "state", json.loads(patch_file.read_text(encoding="utf-8")))
    world_state.copy_final_state(trial)
    for name in LOGS:
        if (fixture_dir / name).exists():
            shutil.copy(fixture_dir / name, trial / name)
    if (fixture_dir / "outputs").is_dir():
        shutil.copytree(fixture_dir / "outputs", trial / "outputs", dirs_exist_ok=True)
    return trial
