"""Component suites: single-layer tests that don't need the full agent loop (spec/10-component-suites.md).

- llm_dates: date and time reasoning, `claude -p` with no tools; exact match after normalization.
- tool_use: the first tool the assistant reaches for, and its arguments; every ea-world tool is asked
  and denied (record_and_deny), so nothing runs.
- skill_triggers: whether a skill loads for prompts that should and shouldn't trigger it.
"""

import asyncio
import json
import os
import re
import subprocess
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from ea_harness.base import RunConfig
from ea_harness.claude_code import ClaudeCodeHarness, claude_binary
from ea_harness.overlay import build_overlay
from ea_harness.trace import read_trace
from ea_world import paths
from ea_world.core import REGISTRY, register_all

from .metrics import pass_at_k, pass_hat_k
from .runner import results_dir

DENVER = ZoneInfo("America/Denver")
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MAX_TURNS = {"tool_use": 3, "skill_triggers": 4}  # ToolSearch (deferred MCP tools) takes a model turn first


def suite_path(name: str) -> Path:
    return paths.kit_root() / "evals" / "components" / f"{name}.yaml"


def load_suite(name: str) -> dict[str, Any]:
    with open(suite_path(name), encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------- llm_dates


def normalize_answer(text: str, kind: str) -> str:
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip() and not ln.strip().upper().startswith("STATUS:")]
    t = (lines[-1] if lines else "").strip().strip("`'\"").rstrip(".").strip()
    if kind == "date":
        m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
        if m:
            return m.group(0)
        m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", t)
        if m:
            return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        from ea_world.texttimes import DATE_RX, _date_from

        for dm in DATE_RX.finditer(t):
            d, _, _ = _date_from(dm, 2026)
            if d:
                return d.isoformat()
        return t
    if kind == "time":
        m = re.search(r"\b(\d{1,2})(?::(\d{2}))?(?::\d{2})?\s*([ap]\.?m\.?)?", t, re.I)
        if m:
            h, mi = int(m.group(1)), int(m.group(2) or 0)
            ap = (m.group(3) or "").lower().replace(".", "")
            if ap == "pm" and h != 12:
                h += 12
            if ap == "am" and h == 12:
                h = 0
            return f"{h:02d}:{mi:02d}"
        return t
    if kind == "weekday":
        for name in WEEKDAY_NAMES:
            if re.search(rf"\b{name[:3]}", t, re.I):
                return name
        return t
    if kind == "number":
        m = re.search(r"-?\d+(?:\.0+)?", t)
        return str(int(float(m.group(0)))) if m else t
    return t


def ask_claude_no_tools(question: str, system: str, model: str) -> tuple[str, float]:
    binary = claude_binary() or "claude"
    with tempfile.TemporaryDirectory() as cwd:
        cmd = [binary, "-p", question, "--output-format", "json", "--max-turns", "1", "--tools", "",
               "--append-system-prompt", system, "--model", model, "--setting-sources", "project,local"]
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL, env=env)
    try:
        data = json.loads(p.stdout)
    except ValueError:
        return p.stdout.strip() or p.stderr.strip(), 0.0
    return str(data.get("result", "")), float(data.get("total_cost_usd", 0) or 0)


def run_llm_dates(trials: int, model: str, items: str = "workshop", out=print, ids: list[str] | None = None) -> dict[str, Any]:
    suite = load_suite("llm_dates")
    chosen = [i for i in suite["items"] if (items == "all" or i.get("workshop")) and (not ids or i["id"] in ids)]
    rows = []
    for item in chosen:
        results = []
        for _ in range(trials):
            text, cost = ask_claude_no_tools(item["question"], suite["system_prompt"], model)
            got = normalize_answer(text, item["kind"])
            results.append({"answer": got, "raw": text[:200], "ok": got == item["answer"], "cost_usd": cost})
        c = sum(r["ok"] for r in results)
        rows.append({"id": item["id"], "question": item["question"], "expected": item["answer"], "tags": item.get("tags", []),
                     "trials": results, "passes": c, "pass@1": pass_at_k(trials, c, 1), f"pass^{trials}": pass_hat_k(trials, c, trials)})
        out(f"  {item['id']}: {c}/{trials}  expected {item['answer']}, got {', '.join(sorted({r['answer'] for r in results}))}")
    return {"suite": "llm_dates", "items": rows, "pass@1": sum(r["pass@1"] for r in rows) / len(rows) if rows else None,
            f"pass^{trials}": sum(r[f"pass^{trials}"] for r in rows) / len(rows) if rows else None}


# ---------------------------------------------------------------- tool_use and skill_triggers


def _all_ask_overlay(run_dir: Path) -> Path:
    overlay = build_overlay(run_dir, {})
    sp = overlay / ".claude" / "settings.json"
    settings = json.loads(sp.read_text())
    register_all()
    settings.setdefault("permissions", {})["ask"] = [f"mcp__ea-world__{t}" for t in sorted(REGISTRY)]
    settings.pop("hooks", None)  # one short probe turn; no stop check
    sp.write_text(json.dumps(settings, indent=2))
    return overlay


async def probe(prompt: str, run_dir: Path, model: str, max_turns: int) -> list[dict[str, Any]]:
    from ea_world import state as world_state

    world_state.init_run_dir(run_dir)
    overlay = _all_ask_overlay(run_dir)
    h = ClaudeCodeHarness()
    await h.start(run_dir, RunConfig(assistant_dir=overlay, model=model, approval_mode="record_and_deny", max_turns=max_turns,
                                     env={"EA_RUN_DIR": str(run_dir), "EA_APPROVAL_MODE": "record_and_deny", "EA_WRITE_CURRENT": "0"}))
    try:
        await h.send(prompt)
    finally:
        await h.close()
    return read_trace(run_dir / "trace.jsonl")


def first_tool_call(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    approvals = [e for e in events if e.get("type") == "approval_request"]
    calls = [e for e in events if e.get("type") == "tool_call" and not e.get("builtin")]
    first = calls[0] if calls else None
    if approvals and (first is None or approvals[0]["seq"] < first["seq"]):
        return {"tool": approvals[0].get("tool"), "input": approvals[0].get("input") or {}}
    return first


def _arg_ok(value: Any, cond: str) -> bool:
    if value is None:
        return False
    if isinstance(cond, str) and cond.startswith("@instant:"):
        want = date.fromisoformat(cond.split(":", 1)[1])
        try:
            v = str(value)
            if len(v) == 10:
                return date.fromisoformat(v) == want
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            dt = dt.replace(tzinfo=DENVER) if dt.tzinfo is None else dt
            return dt.astimezone(DENVER).date() == want
        except ValueError:
            return False
    vals = value if isinstance(value, list) else [value]
    return any(re.search(str(cond), json.dumps(v) if isinstance(v, (dict, list)) else str(v).lower() if isinstance(v, bool) else str(v), re.I) for v in vals)


def args_match(args: dict[str, Any], spec: dict[str, Any], tool: str) -> bool:
    for key, cond in (spec or {}).items():
        if key == "any_of":
            if not any(args_match(args, alt, tool) for alt in cond):
                return False
        elif key == "when_tool":
            inner = (cond or {}).get(tool)
            if inner and not args_match(args, inner, tool):
                return False
        elif not _arg_ok(args.get(key), cond):
            return False
    return True


PREFLIGHT = {"clock_now"}  # house rule 11: the assistant checks the clock before anything else


def attempted_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every ea-world call the assistant attempted, in order (approval requests stand in for denied calls)."""
    seen, out = set(), []
    for e in events:
        if e.get("type") == "approval_request" and e.get("call_id") not in seen:
            seen.add(e.get("call_id"))
            out.append({"tool": e.get("tool"), "input": e.get("input") or {}})
        elif e.get("type") == "tool_call" and not e.get("builtin") and e.get("call_id") not in seen and not str(e.get("tool", "")).startswith("browser."):
            seen.add(e.get("call_id"))
            out.append({"tool": e.get("tool"), "input": e.get("input") or {}})
    return out


def grade_tool_use(item: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    calls = attempted_calls(events)
    expected = set(item.get("expected_tools", []))
    if calls and calls[0]["tool"] in PREFLIGHT and not (expected & PREFLIGHT):
        calls = [c for c in calls if c["tool"] not in PREFLIGHT] or calls[:0]
    first = calls[0] if calls else None
    if first is None:
        sel = bool(item.get("allow_no_tool"))
        return {"tool": None, "selection": sel, "arguments": sel}
    tool = first.get("tool")
    selection = tool in item.get("expected_tools", [])
    arguments = selection and tool not in item.get("forbidden_tools", []) and args_match(first.get("input") or {}, item.get("args_contain") or {}, tool)
    return {"tool": tool, "input": first.get("input"), "selection": selection, "arguments": bool(arguments)}


def grade_skill(skill: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    first_other = next((e for e in events if e.get("type") in ("tool_call", "delegate")), None)
    loads = [e for e in events if e.get("type") == "skill_loaded"]
    loaded_first = any(e.get("skill") == skill and (first_other is None or e["seq"] < first_other["seq"]) for e in loads)
    return {"loaded": [e.get("skill") for e in loads], "triggered": loaded_first, "loaded_at_all": any(e.get("skill") == skill for e in loads)}


def run_tool_use(trials: int, model: str, run_id: str, items: str = "all", out=print, ids: list[str] | None = None) -> dict[str, Any]:
    suite = load_suite("tool_use")
    base = results_dir() / run_id
    rows = []
    for item in [i for i in suite["items"] if not ids or i["id"] in ids]:
        res = []
        for k in range(1, trials + 1):
            events = asyncio.run(probe(item["request"], base / item["id"] / f"t{k}", model, MAX_TURNS["tool_use"]))
            res.append(grade_tool_use(item, events))
        rows.append({"id": item["id"], "request": item["request"], "trials": res,
                     "selection": sum(r["selection"] for r in res) / trials, "arguments": sum(r["arguments"] for r in res) / trials})
        out(f"  {item['id']}: selection {rows[-1]['selection']:.0%} · arguments {rows[-1]['arguments']:.0%} · first tool {', '.join(str(r['tool']) for r in res)}")
    return {"suite": "tool_use", "items": rows,
            "selection_accuracy": sum(r["selection"] for r in rows) / len(rows), "argument_accuracy": sum(r["arguments"] for r in rows) / len(rows)}


def run_skill_triggers(trials: int, model: str, run_id: str, skill: str | None = None, out=print, ids: list[str] | None = None) -> dict[str, Any]:
    suite = load_suite("skill_triggers")["skills"]
    base = results_dir() / run_id
    per_skill = {}
    for name, sets in suite.items():
        if skill and name != skill:
            continue
        tp = fn = fp = tn = 0
        rows = []
        for group, items in (("should", sets["should"]), ("should_not", sets["should_not"])):
            for item in [i for i in items if not ids or i["id"] in ids]:
                for k in range(1, trials + 1):
                    events = asyncio.run(probe(item["text"], base / name / item["id"] / f"t{k}", model, MAX_TURNS["skill_triggers"]))
                    g = grade_skill(name, events)
                    hit = g["triggered"] if group == "should" else g["loaded_at_all"]
                    if group == "should":
                        tp, fn = tp + hit, fn + (not hit)
                    else:
                        fp, tn = fp + hit, tn + (not hit)
                    rows.append({"id": item["id"], "group": group, "text": item["text"], "trial": k, **g})
                out(f"  {name} {item['id']} ({group}): {'loaded' if rows[-1]['loaded_at_all'] else 'not loaded'}")
        if not rows:
            continue
        per_skill[name] = {"precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
                           "tp": tp, "fn": fn, "fp": fp, "tn": tn, "rows": rows}
    return {"suite": "skill_triggers", "skills": per_skill}


def run_component(name: str, trials: int, model: str, run_id: str, skill: str | None = None, items: str = "workshop", out=print,
                  ids: list[str] | None = None) -> Path:
    if name == "llm_dates":
        result = run_llm_dates(trials, model, items, out, ids)
    elif name == "tool_use":
        result = run_tool_use(trials, model, run_id, items, out, ids)
    elif name == "skill_triggers":
        result = run_skill_triggers(trials, model, run_id, skill, out, ids)
    else:
        raise ValueError(f"Unknown component suite '{name}'")
    d = results_dir() / run_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / "component.json"
    p.write_text(json.dumps({"run_id": run_id, "model": model, "trials": trials, **result}, indent=2, default=str), encoding="utf-8")
    return p
