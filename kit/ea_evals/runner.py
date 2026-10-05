"""The eval runner: one fresh world and one Claude Code conversation per trial, graded by rubric."""

import asyncio
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ea_harness.base import Harness, RunConfig, Usage
from ea_harness.overlay import build_overlay
from ea_world import paths
from ea_world import state as world_state
from ea_world.approvals import EXPLICIT_YES  # noqa: F401  (documented policy lives there)

from . import variants as V
from .context import TrialContext
from .costs import CostMeter
from .rubrics import case_rubrics, grade_trial, load_rubric
from .sim_user import SimMaya, script_line

HARNESS_NAME = "claude-code"


class UsageLimit(Exception):
    pass


def results_dir() -> Path:
    return paths.kit_root() / "evals" / "results"


@dataclass
class TrialSpec:
    case: dict[str, Any]
    variant: dict[str, str]
    trial: int
    harness: str = HARNESS_NAME

    @property
    def label(self) -> str:
        return V.label(self.variant)

    @property
    def key(self) -> str:
        return f"{self.case['id']}/{self.harness}/{self.label}/t{self.trial}"


def trial_dir(run_dir: Path, spec: TrialSpec) -> Path:
    return run_dir / spec.case["id"] / spec.harness / spec.label / f"t{spec.trial}"


def git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=paths.kit_root(), capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


@dataclass
class RunOptions:
    run_id: str
    claude_model: str = os.environ.get("EA_CLAUDE_MODEL", "opus")
    effort: str | None = None
    parallel: int = 2
    max_turns: int = 40
    judge_on: bool = True
    rejudge_fraction: float = 0.10
    slice: str | None = None
    selector: str | None = None
    trials: int = 1
    compare_pairs: list[Any] = field(default_factory=list)


class Runner:
    def __init__(self, options: RunOptions, harness_factory: Callable[[], Harness] | None = None, judge: Any = None,
                 meter: CostMeter | None = None, sim_use_model: bool | None = None, out: Callable[[str], None] = print):
        self.o = options
        self.run_dir = results_dir() / options.run_id
        self.meter = meter or CostMeter()
        self.judge = judge
        self.harness_factory = harness_factory
        self.sim_use_model = sim_use_model
        self.out = out
        self.sites_proc: subprocess.Popen | None = None
        self.paused: str | None = None

    # ------------------------------------------------------------ the plan
    def plan_from(self, pairs: list[tuple[dict[str, Any], dict[str, str]]], trials: int) -> list[TrialSpec]:
        specs = []
        for case, variant in pairs:
            for k in range(1, trials + 1):
                specs.append(TrialSpec(case, dict(variant), k))
        seen, unique = set(), []
        for s in specs:
            if s.key not in seen:
                seen.add(s.key)
                unique.append(s)
        return unique

    # ------------------------------------------------------------ one trial
    def _approval_context(self, tdir: Path, spec: TrialSpec, message: str, explicit_yes: bool) -> None:
        ctx = {
            "case_id": spec.case["id"],
            "fill_mode": (spec.case.get("fill") or {}).get("mode"),
            "pre_authorized": spec.case.get("pre_authorized") or [],
            "gate": "off" if V.gate_off(spec.variant) else "on",
            "explicit_yes": explicit_yes,
            "last_maya_message": message,
        }
        world_state.write_json_atomic(tdir / "approval_context.json", ctx)

    def _harness(self) -> Harness:
        if self.harness_factory:
            return self.harness_factory()
        from ea_harness.claude_code import ClaudeCodeHarness

        return ClaudeCodeHarness()

    async def run_trial(self, spec: TrialSpec) -> dict[str, Any]:
        tdir = trial_dir(self.run_dir, spec)
        if (tdir / "grades.json").exists():
            return json.loads((tdir / "grades.json").read_text(encoding="utf-8"))
        if tdir.exists():
            shutil.rmtree(tdir)
        tdir.mkdir(parents=True)
        case, variant = spec.case, spec.variant
        browser = V.browser_on(variant)
        if browser and not port_open(9222):
            g = {"case_id": case["id"], "trial": spec.trial, "harness": spec.harness, "variant": spec.label,
                 "skipped": "browser=on needs Brave or Chrome on port 9222 (run `make browser`)"}
            (tdir / "grades.json").write_text(json.dumps(g, indent=2), encoding="utf-8")
            return g
        world_variants = (case.get("world") or {}).get("variants") or []
        world_state.init_run_dir(tdir, world_variants)
        shutil.copytree(tdir / "state", tdir / "initial_state", ignore=shutil.ignore_patterns(".lock"))
        overlay = build_overlay(tdir, V.overlay_variants(variant), browser=browser)
        env = {
            "EA_RUN_DIR": str(tdir),
            "EA_MODE": "mock",
            "EA_WORLD_VARIANT": ",".join(world_variants),
            "EA_FAULTS": V.faults_for(case, variant),
            "EA_SEED": str(spec.trial),
            "EA_APPROVAL_MODE": "allow_all" if V.gate_off(variant) else "script",
            "EA_IDEMPOTENCY": variant.get("idempotency", "off"),
            "EA_WRITE_CURRENT": "1" if browser else "0",  # the booking sites read runs/CURRENT
        }
        config = RunConfig(
            assistant_dir=overlay,
            model=V.model_for(variant, self.o.claude_model),
            effort=variant.get("effort") or self.o.effort,
            approval_mode="allow_all" if V.gate_off(variant) else "script",  # type: ignore[arg-type]
            idempotency=variant.get("idempotency", "off"),  # type: ignore[arg-type]
            env=env,
            browser=browser,
            max_turns=self.o.max_turns,
            trace_fields={"run_id": self.o.run_id, "case_id": case["id"], "trial": spec.trial, "harness": spec.harness, "variant": spec.label},
        )
        h = self._harness()
        sim = SimMaya(case, meter=self.meter, use_model=self.sim_use_model)
        yes_lines = {script_line({"approvals": "explicit_yes"})}
        if (case.get("maya") or {}).get("approvals") == "edit_then_yes":
            yes_lines.add(script_line(case["maya"]))
        usage = Usage()
        started = time.monotonic()
        stopped = "end_turn"
        error = ""
        await h.start(tdir, config)
        turns = list(case.get("turns") or [])
        message, source = case["request"], "maya"
        end_after = False  # the simulated Maya's reply was her last word ("I'll decide later.", "Thanks.")
        try:
            while True:
                self._approval_context(tdir, spec, message, explicit_yes=(source == "sim_maya" and message in yes_lines))
                r = await h.send(message, source)
                usage.add(r.usage)
                self.meter.add_claude(r.usage.api_equivalent_cost_usd, r.usage.num_turns)
                if r.stopped_by == "usage_limit":
                    raise UsageLimit(r.error or "Claude plan usage limit reached")
                if r.stopped_by == "error":
                    stopped, error = "error", r.error
                    break
                if end_after:
                    break
                if r.status in ("done", "partial", "failed"):
                    if turns:
                        message, source = turns.pop(0), "maya"
                        continue
                    break
                reply = sim.reply(r.final_text, r.status)
                if reply is None:
                    break
                trace = getattr(h, "trace", None)
                if reply.signal and trace is not None:
                    trace.emit("signal", **reply.signal)
                message, source = reply.text, "sim_maya"
                end_after = reply.end
        finally:
            await h.close()
        world_state.copy_final_state(tdir)
        seconds = round(time.monotonic() - started, 1)
        grades = self.grade(spec, tdir, usage, seconds, stopped, error, sim.classifier)
        (tdir / "grades.json").write_text(json.dumps(grades, indent=2, ensure_ascii=False), encoding="utf-8")
        return grades

    def grade(self, spec: TrialSpec, tdir: Path, usage: Usage, seconds: float, stopped: str, error: str, classifier: str) -> dict[str, Any]:
        ctx = TrialContext(spec.case, tdir, variant=spec.variant)
        g = grade_trial(ctx, self.judge)
        status = ctx.final_status()
        budgets = spec.case.get("budgets") or {}
        cost = round(usage.api_equivalent_cost_usd, 4)
        out: dict[str, Any] = {
            "case_id": spec.case["id"], "trial": spec.trial, "harness": spec.harness, "variant": spec.label,
            "variant_set": V.normalized(spec.variant), "type": spec.case.get("type"), "kind": spec.case.get("kind"),
            "tags": spec.case.get("tags") or [], "workshop": bool(spec.case.get("workshop")),
            "rubrics": g.rubric_versions,
            "criteria": [c.as_dict() for c in g.criteria],
            "must_pass": g.must_pass, "scored": None if g.scored is None else round(g.scored, 3), "passed": g.passed,
            "status": status, "overclaim": (not g.passed) if status == "done" else None,
            "usage": {"cost_usd": cost, "seconds": seconds, "model_calls": usage.num_turns,
                      "input_tokens": usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens,
                      "output_tokens": usage.output_tokens, "claude_seconds": round(usage.seconds, 1)},
            "budget": {"cost_ok": cost <= float(budgets.get("max_cost_usd", 1e9)), "time_ok": seconds <= float(budgets.get("max_seconds", 1e9))},
            "judge_calls": g.judge_calls, "stopped_by": stopped, "error": error[:500], "sim_classifier": classifier,
            "model": V.model_for(spec.variant, self.o.claude_model),
            "path": str(tdir.relative_to(self.run_dir)),
        }
        if self.judge is not None and self.o.rejudge_fraction > 0:
            out["rejudge"] = self.rejudge(ctx, spec, g)
        return out

    def rejudge(self, ctx: TrialContext, spec: TrialSpec, g: Any) -> list[dict[str, Any]]:
        """Re-judge a deterministic ~10% sample without the cache, to measure the judge's stability."""
        out = []
        every = max(1, round(1 / self.o.rejudge_fraction))
        for c in g.criteria:
            if c.grader == "code" or c.verdict in ("skipped", "na"):
                continue
            h = int(hashlib.sha256(f"{spec.key}/{c.rubric}/{c.id}".encode()).hexdigest(), 16)
            if h % every:
                continue
            crit = next(x for x in load_rubric(c.rubric)["criteria"] if x["id"] == c.id)
            from .rubrics import build_reference

            cache = getattr(self.judge, "cache", None)
            if cache is not None:
                self.judge.cache = False
            try:
                again = self.judge.grade(ctx, crit, c.rubric, build_reference(ctx, crit))
            finally:
                if cache is not None:
                    self.judge.cache = cache
            out.append({"criterion": f"{c.rubric}/{c.id}", "first": c.verdict, "second": again.get("verdict")})
        return out

    # ------------------------------------------------------------ the run
    def write_run_json(self, specs: list[TrialSpec], status: str, started: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        rubric_names = sorted({n for s in specs for n in case_rubrics(s.case)})
        meta = {
            "run_id": self.o.run_id, "status": status, "started": started,
            "ended": datetime.now().astimezone().isoformat(timespec="seconds"),
            "slice": self.o.slice, "selector": self.o.selector, "trials": self.o.trials,
            "harnesses": sorted({s.harness for s in specs}),
            "variants": sorted({s.label for s in specs}),
            "models": {"claude": self.o.claude_model, "judge": getattr(self.judge, "model", None) if self.judge else None,
                       "sim": os.environ.get("EA_SIM_MODEL", "gpt-5.6-luna")},
            "effort": self.o.effort, "parallel": self.o.parallel, "git_commit": git_commit(),
            "rubric_versions": {n: int(load_rubric(n).get("version", 1)) for n in rubric_names},
            "plan": [{"case_id": s.case["id"], "variant": s.variant, "trial": s.trial, "harness": s.harness, "key": s.key} for s in specs],
            "compare": self.o.compare_pairs,
            "costs": self.meter.as_dict(),
            "personal_settings": personal_settings_present(),
        }
        meta.update(extra or {})
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "run.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return meta

    def ensure_sites(self) -> None:
        if port_open(8766):
            return
        self.sites_proc = subprocess.Popen([sys.executable, "-m", "ea_sites.server", "--port", "8766"],
                                           cwd=paths.kit_root(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            if port_open(8766):
                return
            time.sleep(0.25)

    async def run(self, specs: list[TrialSpec]) -> list[dict[str, Any]]:
        started = datetime.now().astimezone().isoformat(timespec="seconds")
        existing = self.run_dir / "run.json"
        if existing.exists():
            started = json.loads(existing.read_text()).get("started", started)
        parallel = 1 if any(V.browser_on(s.variant) for s in specs) else max(1, self.o.parallel)
        if any(V.browser_on(s.variant) for s in specs):
            self.ensure_sites()
        self.write_run_json(specs, "running", started)
        sem = asyncio.Semaphore(parallel)
        results: dict[str, dict[str, Any]] = {}
        stop = asyncio.Event()

        async def one(spec: TrialSpec) -> None:
            async with sem:
                if stop.is_set():
                    return
                if self.meter.over_cap():
                    self.paused = self.paused or f"cost cap reached (OpenAI ${self.meter.openai_usd:.2f})"
                    stop.set()
                    return
                self.out(f"  {spec.key} …")
                try:
                    g = await self.run_trial(spec)
                except UsageLimit as exc:
                    self.paused = f"Claude usage limit: {exc}"
                    stop.set()
                    shutil.rmtree(trial_dir(self.run_dir, spec), ignore_errors=True)
                    return
                results[spec.key] = g
                verdict = "skipped" if g.get("skipped") else ("PASS" if g.get("passed") else "FAIL")
                self.out(f"  {spec.key}: {verdict} · status {g.get('status', '-')} · ${g.get('usage', {}).get('cost_usd', 0):.2f} · {g.get('usage', {}).get('seconds', 0):.0f}s")

        await asyncio.gather(*(one(s) for s in specs))
        if self.sites_proc:
            self.sites_proc.terminate()
        status = "paused" if self.paused else "complete"
        self.write_run_json(specs, status, started, {"pause_reason": self.paused} if self.paused else None)
        return [results[s.key] for s in specs if s.key in results]


def personal_settings_present() -> dict[str, bool]:
    home = Path.home() / ".claude"
    hooks = False
    try:
        hooks = bool(json.loads((home / "settings.json").read_text()).get("hooks"))
    except Exception:
        pass
    return {"claude_md": (home / "CLAUDE.md").exists(), "user_hooks": hooks}


def load_grades(run_dir: Path) -> list[dict[str, Any]]:
    out = []
    for p in sorted(Path(run_dir).glob("*/*/*/t*/grades.json")):
        try:
            g = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not g.get("skipped"):
            out.append(g)
    return out
