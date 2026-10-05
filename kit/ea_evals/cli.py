"""`ea-eval`: run golden cases and component suites, build reports, promote failures, calibrate the judge."""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .env import load_dotenv

USAGE_EXAMPLES = """examples:
  ea-eval run --slice setup-baseline
  ea-eval run --cases S01 --trials 1
  ea-eval run --cases workshop --variant skills=off
  ea-eval resume <run-id>
  ea-eval report <run-id> --compare <other-run-id>
  ea-eval promote <run-id> S01 claude-code default 1
  ea-eval component llm_dates --trials 5
  ea-eval list cases
  ea-eval estimate --cases workshop --trials 3
"""

# Rough per-trial figures until the reference run measures them (PRD section 13)
EST_CLAUDE_USD = 0.35
EST_OPENAI_USD = 0.02
EST_SECONDS = 75


def _run_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _judge(args: argparse.Namespace):
    from .costs import CostMeter
    from .judge import OpenAIJudge

    meter = CostMeter(max_openai_usd=float(args.max_cost))
    if getattr(args, "no_judge", False):
        return None, meter
    if not os.environ.get("OPENAI_API_KEY"):
        print("No OPENAI_API_KEY: judged criteria are skipped (add the key to .env, or pass --no-judge to silence this).", file=sys.stderr)
        return None, meter
    return OpenAIJudge(model=args.judge_model, meter=meter), meter


def cmd_run(args: argparse.Namespace, resume_meta: dict | None = None) -> int:
    from . import variants as V
    from .cases import load_all, select, slice_plan
    from .report.build import build
    from .runner import Runner, RunOptions

    if args.sim_model:
        os.environ["EA_SIM_MODEL"] = args.sim_model
    cases = load_all()
    extra_variant = V.parse(args.variant)
    compare_pairs: list = []
    if resume_meta:
        pairs = [(cases[p["case_id"]], p["variant"]) for p in resume_meta["plan"]]
        run_id = resume_meta["run_id"]
        slice_name = resume_meta.get("slice")
        selector = resume_meta.get("selector")
        trials = int(resume_meta.get("trials") or 1)
        compare_pairs = resume_meta.get("compare") or []
    elif args.slice:
        plan, trials, sdef = slice_plan(args.slice, cases)
        pairs = [(c, {**v, **extra_variant}) for c, v in plan]
        trials = args.trials or trials
        slice_name, selector = args.slice, None
        compare_pairs = sdef.get("compare") or [] if isinstance(sdef.get("compare"), list) else []
        run_id = args.run_id or _run_id(args.slice)
    else:
        if not args.cases:
            print("Give --cases <selector> or --slice <name>.", file=sys.stderr)
            return 2
        chosen = select(args.cases, cases)
        pairs = [(c, dict(extra_variant)) for c in chosen]
        trials = args.trials or 1
        slice_name, selector = None, args.cases
        run_id = args.run_id or _run_id("run")
    judge, meter = _judge(args)
    opts = RunOptions(run_id=run_id, claude_model=args.claude_model or os.environ.get("EA_CLAUDE_MODEL", "opus"), effort=args.effort,
                      parallel=args.parallel, judge_on=judge is not None, slice=slice_name, selector=selector, trials=trials,
                      compare_pairs=compare_pairs)
    runner = Runner(opts, judge=judge, meter=meter)
    if resume_meta:
        from .runner import TrialSpec

        specs = [TrialSpec(cases[p["case_id"]], p["variant"], p["trial"], p.get("harness", "claude-code")) for p in resume_meta["plan"]]
    else:
        specs = runner.plan_from(pairs, trials)
    print(f"Run {run_id}: {len(specs)} trials (parallel {opts.parallel}, Claude {opts.claude_model}, judge {getattr(judge, 'model', 'off')})")
    asyncio.run(runner.run(specs))
    report = build(run_id)
    print(f"\nReport: {report}")
    print(f"Claude API-equivalent ${meter.claude_api_equivalent_usd:.2f} (plan usage, not billed) · OpenAI ${meter.openai_usd:.2f}")
    if runner.paused:
        print(f"\nPaused: {runner.paused}\nResume later with: uv run ea-eval resume {run_id}")
        return 3
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    from .runner import results_dir

    p = results_dir() / args.run_id / "run.json"
    if not p.exists():
        print(f"No run {args.run_id}", file=sys.stderr)
        return 2
    meta = json.loads(p.read_text())
    return cmd_run(args, resume_meta=meta)


def cmd_report(args: argparse.Namespace) -> int:
    from .report.build import build

    print(build(args.run_id, args.compare or []))
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    from .promote import promote

    path = promote(args.run_id, args.case_id, args.harness, args.variant, args.trial)
    print(f"Draft case written: {path}\nReview it, then move it next to the other cases.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    from . import variants as V
    from .cases import load_all, load_slices

    if args.what == "cases":
        for c in load_all().values():
            print(f"{c['id']:4} {c['type']:10} {c['kind']:10} {'W' if c.get('workshop') else ' '}  {c['request']}")
    elif args.what == "slices":
        for name, s in load_slices().items():
            cases = s["cases"] if isinstance(s["cases"], str) else ",".join(s["cases"])
            variants = "; ".join(V.label(v or {}) for v in s.get("variants") or [{}])
            print(f"{name:18} trials {s.get('trials', 1)}  cases {cases}  variants {variants}")
    else:
        for k in sorted(V.KNOWN):
            print(f"{k}={V.DEFAULTS.get(k, '…')}")
        print("faults=timeout_after_write (per case: S01 calendar_create, R08 travel_book)")
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    from .cases import load_all, select, slice_plan

    if args.slice:
        plan, trials, _ = slice_plan(args.slice)
        n = len(plan) * (args.trials or trials)
    else:
        n = len(select(args.cases or "workshop", load_all())) * (args.trials or 1)
    par = max(1, args.parallel)
    print(f"{n} trials ≈ ${n * EST_CLAUDE_USD:.2f} of Claude plan usage (API-equivalent) + ${n * EST_OPENAI_USD:.2f} OpenAI, "
          f"about {n * EST_SECONDS / par / 60:.0f} min at --parallel {par}. Figures are rough until the reference run measures them.")
    return 0


def cmd_component(args: argparse.Namespace) -> int:
    from .components import run_component

    run_id = args.run_id or _run_id(f"component-{args.suite}")
    ids = [i.strip() for i in (args.ids or "").split(",") if i.strip()] or None
    path = run_component(args.suite, args.trials, args.claude_model or os.environ.get("EA_CLAUDE_MODEL", "opus"), run_id, args.skill, args.items, ids=ids)
    data = json.loads(path.read_text())
    keys = [k for k in data if k.startswith("pass") or k.endswith("accuracy")]
    print(f"\n{args.suite}: " + ", ".join(f"{k} {data[k]:.0%}" for k in keys if isinstance(data[k], (int, float))))
    if args.suite == "skill_triggers":
        def pct(v: float | None) -> str:
            return "—" if v is None else f"{v:.0%}"

        for name, s in data["skills"].items():
            print(f"  {name}: precision {pct(s['precision'])} · recall {pct(s['recall'])}")
    print(f"Results: {path}")
    return 0


def cmd_label(args: argparse.Namespace) -> int:
    from .calibration import make_labels

    p = make_labels(args.run_id, args.rubric, [c.strip() for c in (args.criteria or "").split(",") if c.strip()] or None, args.n)
    print(f"Labels to fill in: {p}")
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    from .calibration import calibrate

    for crit, r in calibrate(args.run_id, args.rubric).items():
        tpr = "—" if r["tpr"] is None else f"{r['tpr']:.0%}"
        tnr = "—" if r["tnr"] is None else f"{r['tnr']:.0%}"
        print(f"{crit}: TPR {tpr} · TNR {tnr} · {r['labelled']} labels")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ea-eval", description="Run the golden dataset and component suites; build reports.",
                                epilog=USAGE_EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def run_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--harness", default="claude-code", choices=["claude-code"])
        sp.add_argument("--trials", type=int, default=None)
        sp.add_argument("--variant", action="append", default=[], help="key=value, e.g. skills=off (repeatable)")
        sp.add_argument("--claude-model", "--model", dest="claude_model", default=None, help="Claude Code model: opus or sonnet")
        sp.add_argument("--judge-model", default=None)
        sp.add_argument("--sim-model", default=None)
        sp.add_argument("--effort", default=None)
        sp.add_argument("--parallel", type=int, default=2)
        sp.add_argument("--max-cost", default=os.environ.get("EA_MAX_COST_USD", "5.00"), help="OpenAI spend cap for this invocation")
        sp.add_argument("--no-judge", action="store_true")

    r = sub.add_parser("run", help="Run golden cases")
    r.add_argument("--cases", default=None, help="workshop, all, type:<t>, tag:<t>, kind:<k>, or ids")
    r.add_argument("--slice", default=None)
    r.add_argument("--run-id", default=None)
    run_args(r)
    r.set_defaults(fn=cmd_run)
    rs = sub.add_parser("resume", help="Resume a paused run")
    rs.add_argument("run_id")
    run_args(rs)
    rs.set_defaults(fn=cmd_resume)
    rp = sub.add_parser("report", help="Build or rebuild a report")
    rp.add_argument("run_id")
    rp.add_argument("--compare", action="append", default=[])
    rp.set_defaults(fn=cmd_report)
    pr = sub.add_parser("promote", help="Turn a failing trial into a draft golden case")
    for a in ("run_id", "case_id", "harness", "variant", "trial"):
        pr.add_argument(a)
    pr.set_defaults(fn=cmd_promote)
    ls = sub.add_parser("list", help="List cases, slices or variants")
    ls.add_argument("what", choices=["cases", "slices", "variants"])
    ls.set_defaults(fn=cmd_list)
    es = sub.add_parser("estimate", help="Cost and time estimate before running")
    es.add_argument("--cases", default=None)
    es.add_argument("--slice", default=None)
    es.add_argument("--trials", type=int, default=None)
    es.add_argument("--parallel", type=int, default=2)
    es.set_defaults(fn=cmd_estimate)
    co = sub.add_parser("component", help="Run a component suite")
    co.add_argument("suite", choices=["llm_dates", "tool_use", "skill_triggers"])
    co.add_argument("--trials", type=int, default=1)
    co.add_argument("--skill", default=None)
    co.add_argument("--items", default="workshop", choices=["workshop", "all"])
    co.add_argument("--ids", default="", help="only these item ids, comma-separated")
    co.add_argument("--claude-model", "--model", dest="claude_model", default=None)
    co.add_argument("--run-id", default=None)
    co.add_argument("--variant", action="append", default=[])
    co.set_defaults(fn=cmd_component)
    lb = sub.add_parser("label", help="Write a labelling file for judge calibration")
    lb.add_argument("run_id")
    lb.add_argument("--rubric", required=True)
    lb.add_argument("--criteria", default="")
    lb.add_argument("--n", type=int, default=20)
    lb.set_defaults(fn=cmd_label)
    ca = sub.add_parser("calibrate", help="Judge TPR and TNR against your labels")
    ca.add_argument("run_id")
    ca.add_argument("--rubric", required=True)
    ca.set_defaults(fn=cmd_calibrate)
    return p


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    os.environ["PATH"] = os.pathsep.join([str(Path.home() / ".local" / "bin"), os.environ.get("PATH", "")])
    args = build_parser().parse_args(argv)
    sys.exit(args.fn(args))
