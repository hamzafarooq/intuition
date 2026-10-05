"""Build `reports/<run-id>.html`: one self-contained file (inline CSS, JS and data) that opens from disk."""

import html
import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ea_harness.trace import read_trace
from ea_world import paths
from ea_world.state import State

from .. import metrics as M
from ..cases import TYPES
from ..runner import load_grades, results_dir
from .radar import RADAR_CSS, legend_html, radar_svg, table_html

TEMPLATES = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape(["html", "j2"]))
OUTPUT_CAP = 900


def reports_dir() -> Path:
    return paths.kit_root() / "reports"


def load_run(run_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rd = results_dir() / run_id
    meta = json.loads((rd / "run.json").read_text(encoding="utf-8")) if (rd / "run.json").exists() else {"run_id": run_id}
    return meta, load_grades(rd)


def assistant_axes() -> list[tuple[str, str]]:
    return [(a, M.AXIS_LABELS[a]) for a in M.CHART_AXES]


def team_axes() -> list[tuple[str, str]]:
    return [(a, M.TEAM_LABELS[a]) for a in M.TEAM_AXES]


def _scores(axes_scores: dict[str, dict[str, Any]]) -> tuple[dict[str, float | None], dict[str, int]]:
    return {k: v["score"] for k, v in axes_scores.items()}, {k: v["n"] for k, v in axes_scores.items()}


def must_flags(grades: list[dict[str, Any]]) -> dict[str, str]:
    """Mark each axis that has a must-pass failure (Safety included), with the first failure as the tooltip."""
    out: dict[str, str] = {}
    counts: dict[str, int] = {}
    for f in M.must_failures(grades):
        axis = f["axis"] or "correct"
        counts[axis] = counts.get(axis, 0) + 1
        out.setdefault(axis, f"{f['case_id']} {f['criterion']}: {f['evidence'][:80]}")
    for axis, n in counts.items():
        out[axis] = f"{n} must-pass failure{'s' if n != 1 else ''} · " + out[axis]
    return out


def chart_block(chart_id: str, title: str, axes: list[tuple[str, str]], runs: list[tuple[str, list[dict[str, Any]]]],
                team: bool = False, budgets: dict[str, float] | None = None) -> dict[str, Any]:
    series, counts, trials = [], [], []
    for name, gs in runs[:3]:
        sc, n = _scores(M.team_scores(gs) if team else M.axis_scores(gs, budgets))
        series.append({"name": name, "scores": sc})
        counts.append(n)
        trials.append(len(gs))
    flagged = must_flags(runs[0][1]) if runs and not team else {}
    banner = M.must_failures(runs[0][1]) if runs and not team else []
    return {
        "id": chart_id,
        "title": title,
        "svg": radar_svg(axes, series, title, f"{title}: {', '.join(f'{s['name']} ({t} trials)' for s, t in zip(series, trials, strict=True))}",
                         flagged_axes=flagged, chart_id=chart_id),
        "legend": legend_html(series) if len(series) > 1 else "",
        "table": table_html(axes, series, counts),
        "trials": trials,
        "banner": banner,
        "empty": all(not gs for _, gs in runs[:1]),
    }


def world_changes(tdir: Path) -> dict[str, Any]:
    if not (tdir / "initial_state").exists():
        return {}
    a, b = State(tdir, "initial_state"), State(tdir, "final_state" if (tdir / "final_state").exists() else "state")
    ev_a = {e["id"]: e for e in a.load("calendars").get("maya", [])}
    ev_b = {e["id"]: e for e in b.load("calendars").get("maya", [])}
    events = []
    for eid, e in ev_b.items():
        if eid not in ev_a:
            events.append({"change": "created", "id": eid, "title": e.get("title"), "start": e.get("start"), "end": e.get("end"),
                           "attendees": e.get("attendees"), "rsvps": e.get("rsvps"), "status": e.get("status")})
        elif any(ev_a[eid].get(k) != e.get(k) for k in ("start", "end", "title", "attendees", "status", "description")):
            events.append({"change": "cancelled" if e.get("status") == "cancelled" else "changed", "id": eid, "title": e.get("title"),
                           "start": e.get("start"), "end": e.get("end"), "attendees": e.get("attendees"), "rsvps": e.get("rsvps"),
                           "was": {k: ev_a[eid].get(k) for k in ("start", "end", "attendees")}})
    inbox_a = {e["id"] for e in a.load("inbox").get("emails", [])}
    arrivals = [{"id": e["id"], "from": e.get("from_name") or e.get("from"), "subject": e.get("subject")} for e in b.load("inbox").get("emails", []) if e["id"] not in inbox_a]
    sent = [{"id": m["id"], "to": m.get("to"), "subject": m.get("subject"), "body": m.get("body", "")[:1200]} for m in b.load("outbox").get("sent", [])]
    drafts = [{"id": d["id"], "to": d.get("to"), "subject": d.get("subject"), "status": d.get("status"), "body": d.get("body", "")[:800]} for d in b.load("drafts").get("drafts", [])]
    bk_a = {x["id"]: x for x in a.load("bookings").get("bookings", [])}
    bookings = []
    for x in b.load("bookings").get("bookings", []):
        if x["id"] not in bk_a:
            bookings.append({"change": "created", **{k: x.get(k) for k in ("id", "kind", "option_id", "total_usd", "status", "travelers")}})
        elif bk_a[x["id"]].get("status") != x.get("status"):
            bookings.append({"change": x.get("status"), **{k: x.get(k) for k in ("id", "kind", "option_id", "total_usd", "status")}})
    conn = {k: v for k, v in b.load("connectors").items() if a.load("connectors").get(k) != v}
    return {"events": events, "inbox_arrivals": arrivals, "sent": sent, "drafts": drafts, "bookings": bookings, "connectors": conn}


def _trim(e: dict[str, Any]) -> dict[str, Any]:
    keep = {k: v for k, v in e.items() if k not in ("v", "ts", "run_id", "harness", "case_id", "trial", "variant")}
    for k in ("output", "input", "text", "task"):
        if k in keep:
            v = keep[k]
            s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
            if len(s) > OUTPUT_CAP and k in ("output", "input"):
                keep[k] = s[:OUTPUT_CAP] + "…"
    return keep


def trial_payload(run_dir: Path, g: dict[str, Any]) -> dict[str, Any]:
    tdir = run_dir / g["path"]
    return {
        "key": f"{g['case_id']}/{g['harness']}/{g['variant']}/t{g['trial']}",
        "case_id": g["case_id"], "variant": g["variant"], "trial": g["trial"], "harness": g["harness"],
        "passed": g["passed"], "status": g["status"], "overclaim": g.get("overclaim"),
        "criteria": g["criteria"], "usage": g.get("usage", {}),
        "trace": [_trim(e) for e in read_trace(tdir / "trace.jsonl")],
        "world": world_changes(tdir),
        "promote": f"uv run ea-eval promote {run_dir.name} {g['case_id']} {g['harness']} {g['variant']} {g['trial']}",
        "path": str(tdir),
    }


def comparison_pairs(meta: dict[str, Any], grades: list[dict[str, Any]]) -> list[tuple[str, str]]:
    labels = []
    for g in grades:
        if g["variant"] not in labels:
            labels.append(g["variant"])
    if len(labels) < 2:
        return []
    from ..variants import label as vlabel

    pairs = []
    for p in meta.get("compare") or []:
        if isinstance(p, list) and len(p) == 2:
            a, b = vlabel(p[0] or {}), vlabel(p[1] or {})
            if a in labels and b in labels:
                pairs.append((a, b))
    if not pairs:
        if "default" in labels:
            pairs = [("default", lbl) for lbl in labels if lbl != "default"]
        else:
            pairs = [(labels[i], labels[i + 1]) for i in range(0, len(labels) - 1, 2)]
    return pairs


def build(run_id: str, compare: list[str] | None = None, out: Path | None = None) -> Path:
    meta, grades = load_run(run_id)
    run_dir = results_dir() / run_id
    th = M.load_thresholds()
    budgets = M.budgets(th)
    others = [(cid, load_run(cid)[1]) for cid in (compare or [])[:2]]
    calibration = {}
    calib_path = run_dir / "calibration.json"
    if calib_path.exists():
        calibration = json.loads(calib_path.read_text())
    summ = M.summary(grades, budgets)
    runs_overall = [(run_id, grades)] + others
    overview = chart_block("radar-overview", "The assistant", assistant_axes(), runs_overall, budgets=budgets)
    by_type = []
    for t in TYPES:
        gs = [g for g in grades if g.get("type") == t]
        if not gs:
            continue
        runs = [(run_id, gs)] + [(cid, [g for g in og if g.get("type") == t]) for cid, og in others]
        block = chart_block(f"radar-{t}", t.capitalize(), assistant_axes(), runs, budgets=budgets)
        block["criteria"] = M.criterion_rates(gs)
        block["cases"] = M.per_case(gs)
        block["type"] = t
        by_type.append(block)
    team_grades = [g for g in grades if g.get("type") == "team" or any(c.get("team_axis") for c in g.get("criteria", []))]
    team = chart_block("radar-team", "The team", team_axes(), [(run_id, team_grades)] + [(c, [g for g in og if g.get("type") == "team"]) for c, og in others], team=True) if team_grades else None
    comparisons = []
    for a, b in comparison_pairs(meta, grades):
        ga = [g for g in grades if g["variant"] == a]
        gb = [g for g in grades if g["variant"] == b]
        block = chart_block(f"radar-cmp-{len(comparisons)}", f"{a} vs {b}", assistant_axes(), [(a, ga), (b, gb)], budgets=budgets)
        sa, sb = M.summary(ga, budgets), M.summary(gb, budgets)
        block["stats"] = [
            ("Pass rate", sa["pass_rate"], sb["pass_rate"], "pct"), ("Overclaim rate", sa["overclaim_rate"], sb["overclaim_rate"], "pct"),
            ("Mean cost (API-equivalent)", sa["mean_cost_usd"], sb["mean_cost_usd"], "usd"), ("Mean time", sa["mean_seconds"], sb["mean_seconds"], "s"),
            ("Trials", sa["trials"], sb["trials"], "n"),
        ]
        if any(g.get("type") == "team" for g in ga + gb):
            block["team"] = chart_block(f"radar-cmp-team-{len(comparisons)}", f"Team: {a} vs {b}", team_axes(),
                                        [(a, [g for g in ga if g.get("type") == "team"]), (b, [g for g in gb if g.get("type") == "team"])], team=True)
        comparisons.append(block)
    selfcheck_rows = []
    for label, gs in M.by(grades, "variant").items():
        s = M.summary(gs, budgets)
        selfcheck_rows.append({"variant": label, "trials": s["trials"], "overclaim": s["overclaim_rate"], "underclaim": s["underclaim_rate"],
                               "pass": s["pass_rate"], "cost": s["mean_cost_usd"], "seconds": s["mean_seconds"]})
    trials = [trial_payload(run_dir, g) for g in grades]
    ctx = {
        "meta": meta, "summary": summ, "overview": overview, "by_type": by_type, "team": team, "comparisons": comparisons,
        "thresholds": M.evaluate_thresholds(grades, th, meta.get("slice"), calibration),
        "must_failures": M.must_failures(grades), "criteria": M.criterion_rates(grades), "cases": M.per_case(grades),
        "selfcheck": selfcheck_rows, "trials": trials, "compare": [c for c, _ in others],
        "kinds": {k: M.summary(v, budgets) for k, v in M.by(grades, "kind").items()},
        "radar_css": RADAR_CSS, "trials_json": json.dumps(trials, ensure_ascii=False, default=str).replace("</", "<\\/"),
        "esc": html.escape,
    }
    out = Path(out or reports_dir() / f"{run_id}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATES.get_template("report.html.j2").render(**ctx), encoding="utf-8")
    return out
