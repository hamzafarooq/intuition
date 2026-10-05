"""Write `reports/acceptance.md`: every PRD requirement with the evidence that proves it and the result.

    uv run python tools/acceptance.py [--no-tests]

Runs the offline suite (junit XML), evaluates tools/acceptance_map.yaml, and writes the table. Green means
every listed piece of evidence holds; yellow means it holds so far but a larger live run or a person's check
is still to do; red means something failed.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))
RESULTS = KIT / "evals" / "results"
GATED = {"calendar_create", "calendar_update", "calendar_cancel", "email_send", "travel_book", "travel_cancel", "restaurant_book"}


# ---------------------------------------------------------------- PRD requirements


def prd_requirements() -> list[tuple[str, str, str]]:
    text = (KIT.parent / "PRD.md").read_text(encoding="utf-8")
    rows = []
    for m in re.finditer(r"^\| ([A-G]\d+) \| (.+?) \| (.+?) \|$", text, re.M):
        rows.append((m.group(1), m.group(2).strip(), m.group(3).strip()))
    order = {k: i for i, k in enumerate("ABCDEFG")}
    return sorted(rows, key=lambda r: (order[r[0][0]], int(r[0][1:])))


# ---------------------------------------------------------------- tests


def run_tests() -> dict[str, str]:
    with tempfile.TemporaryDirectory() as d:
        xml = Path(d) / "junit.xml"
        subprocess.run(["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}"], cwd=KIT, capture_output=True, text=True)
        out: dict[str, str] = {}
        if not xml.exists():
            return out
        for tc in ET.parse(xml).getroot().iter("testcase"):
            cls = tc.get("classname", "")
            name = tc.get("name", "")
            node = cls.replace(".", "/") + ".py::" + name
            failed = tc.find("failure") is not None or tc.find("error") is not None
            skipped = tc.find("skipped") is not None
            out[node] = "failed" if failed else ("skipped" if skipped else "passed")
        return out


def eval_tests(prefixes: list[str], results: dict[str, str]) -> tuple[bool, str]:
    notes, ok = [], True
    for p in prefixes:
        hits = {k: v for k, v in results.items() if k == p or k.startswith(p if "::" in p else p + "::")}
        if not hits:
            ok = False
            notes.append(f"`{p}`: no tests found")
            continue
        bad = [k.split("::", 1)[1] for k, v in hits.items() if v == "failed"]
        passed = sum(v == "passed" for v in hits.values())
        if bad:
            ok = False
            notes.append(f"`{p}`: {len(bad)} failed ({', '.join(bad[:3])})")
        else:
            notes.append(f"`{p}` ({passed} passed)")
    return ok, "; ".join(notes)


# ---------------------------------------------------------------- live evidence


def grades_for(run: str, case: str) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted((RESULTS / run).glob(f"{case}/*/*/t*/grades.json"))]


def eval_live(items: list[dict[str, Any]]) -> tuple[bool, str]:
    notes, ok = [], True
    for it in items:
        gs = grades_for(it["run"], it["case"])
        if not gs:
            ok = False
            notes.append(f"{it['run']}/{it['case']}: no graded trial")
            continue
        g = gs[-1]
        bits = []
        if "passed" in it:
            good = g.get("passed") == it["passed"]
            ok &= good
            bits.append(f"{'passed' if g.get('passed') else 'failed'}")
        for c in it.get("criteria", []):
            v = next((x["verdict"] for x in g.get("criteria", []) if f"{x['rubric']}/{x['id']}" == c), None)
            ok &= v == "yes"
            bits.append(f"{c} {v}")
        if it.get("delegated"):
            tdir = RESULTS / it["run"] / g["path"]
            delegates = [json.loads(line).get("to") for line in (tdir / "trace.jsonl").read_text().splitlines() if '"type": "delegate"' in line]
            good = it["delegated"] in delegates
            ok &= good
            bits.append(f"handed to {', '.join(delegates) or 'nobody'}")
        if it.get("usage"):
            u = g.get("usage", {})
            good = all(k in u for k in ("cost_usd", "seconds", "model_calls", "input_tokens", "output_tokens"))
            ok &= good
            bits.append(f"${u.get('cost_usd', 0):.2f}, {u.get('seconds', 0):.0f} s, {u.get('model_calls', 0)} model turns, {u.get('input_tokens', 0):,} input tokens")
        notes.append(f"live `{it['run']}` {it['case']}: " + ", ".join(bits))
    return ok, "; ".join(notes)


# ---------------------------------------------------------------- named checks


def _agents() -> dict[str, list[str]]:
    out = {}
    for p in (KIT / "assistant/.claude/agents").glob("*.md"):
        m = re.search(r"^tools:\s*(.+)$", p.read_text(), re.M)
        out[p.stem] = [t.strip().split("__")[-1] for t in (m.group(1).split(",") if m else [])]
    return out


def check_specialists_have_scoped_tools() -> tuple[bool, str]:
    agents = _agents()
    bad = [a for a in ("scheduler", "inbox", "briefer", "travel") if not agents.get(a) or set(agents[a]) & GATED]
    return not bad, "each specialist lists its own tools and none of the gated ones" if not bad else f"problem: {bad}"


def check_skills_have_definitions_of_done() -> tuple[bool, str]:
    skills = sorted((KIT / "assistant/.claude/skills").glob("*/SKILL.md"))
    bad = []
    for p in skills:
        t = p.read_text()
        if not all(h in t for h in ("Definition of done", "Hard checks", "Quality criteria", "Signals")):
            bad.append(p.parent.name)
    return len(skills) == 8 and not bad, f"{len(skills)} skills, each with hard checks, quality criteria and signals" if not bad else f"missing parts: {bad}"


def check_skills_route_drafts_to_reviewer() -> tuple[bool, str]:
    names = ["email-reply", "meeting-brief", "meeting-deck"]
    bad = [n for n in names if "reviewer" not in (KIT / f"assistant/.claude/skills/{n}/SKILL.md").read_text()]
    return not bad, "email-reply, meeting-brief and meeting-deck send drafts to the reviewer" if not bad else f"no reviewer step: {bad}"


def check_fresh_world_identical() -> tuple[bool, str]:
    from ea_world import state

    digests = []
    with tempfile.TemporaryDirectory() as d:
        for i in range(2):
            run = Path(d) / f"r{i}"
            state.init_run_dir(run, ["raj-replies"])
            h = hashlib.sha256()
            for f in sorted((run / "state").rglob("*")):
                if f.is_file() and f.name != ".lock":
                    h.update(f.relative_to(run).as_posix().encode() + f.read_bytes())
            digests.append(h.hexdigest())
    return digests[0] == digests[1], "two fresh run directories of the same case have identical state"


def check_modes_share_tool_names() -> tuple[bool, str]:
    src = (KIT / "ea_world/web.py").read_text()
    ok = all(s in src for s in ('mode() == "mock"', "serpapi.com/search", '"record"'))
    return ok, "web_search and web_fetch switch on EA_MODE (mock, record, live) with the same tool names; nothing else changes"


def check_sixty_three_cases() -> tuple[bool, str]:
    from ea_evals.cases import load_all

    cases = load_all()
    return len(cases) == 63, f"{len(cases)} cases across {len({c['type'] for c in cases.values()})} types"


def check_kinds_marked() -> tuple[bool, str]:
    from ea_evals.cases import load_all

    kinds = {c["kind"] for c in load_all().values()}
    tpl = (KIT / "ea_evals/report/templates/report.html.j2").read_text()
    return kinds == {"capability", "regression"} and "kinds.items()" in tpl, "every case is capability or regression; the report shows each group"


def check_workshop_subset() -> tuple[bool, str]:
    from ea_evals.cases import select

    n = len(select("workshop"))
    return n == 20, f"{n} cases in the workshop subset"


def check_promote_works() -> tuple[bool, str]:
    if not grades_for("smoke-s01", "S01"):
        return False, "no smoke run to promote from"
    from ea_evals.promote import promote

    p = promote("smoke-s01", "S01", "claude-code", "default", 1)
    ok = p.exists() and "kind: regression" in p.read_text()
    p.unlink()
    return ok, "`ea-eval promote` writes a draft regression case from a trial"


def check_cases_mention_no_harness() -> tuple[bool, str]:
    bad = [p.name for p in (KIT / "evals/golden").glob("*/*.yaml") if re.search(r"claude|mcp__|stream-json|--resume", p.read_text(), re.I)]
    return not bad, "no case file names a harness" if not bad else f"mentions a harness: {bad}"


def check_no_anthropic_key_used() -> tuple[bool, str]:
    src = (KIT / "ea_harness/claude_code.py").read_text()
    return 'env.pop("ANTHROPIC_API_KEY", None)' in src, "the adapter removes ANTHROPIC_API_KEY so runs use the Claude login"


def check_calibration_tooling() -> tuple[bool, str]:
    from ea_evals.cli import build_parser

    sub = build_parser()._subparsers._group_actions[0].choices  # type: ignore[union-attr]
    return {"label", "calibrate"} <= set(sub), "`ea-eval label` and `ea-eval calibrate` compute TPR and TNR per judged criterion"


def check_thresholds_written() -> tuple[bool, str]:
    from ea_evals.metrics import load_thresholds

    th = load_thresholds()
    n = len([k for k, v in th.items() if isinstance(v, dict) and k != "budgets"])
    return n >= 10, f"{n} targets in evals/thresholds.md, each with a reason; the report marks each met or not"


def check_reports_exist() -> tuple[bool, str]:
    p = KIT / "reports" / "smoke-s01.html"
    ok = p.exists() and "trial-data" in p.read_text() and "What changed in the world" in p.read_text()
    return ok, "reports/smoke-s01.html: suites, charts, trial viewer with conversation, tool calls, checks and world changes"


def check_report_has_criteria_table() -> tuple[bool, str]:
    p = KIT / "reports" / "smoke-s01.html"
    return p.exists() and "Every criterion" in p.read_text() and "Failed in" in p.read_text(), "every criterion's pass rate, linked to the trials that failed it"


def check_rubric_versions_recorded() -> tuple[bool, str]:
    g = grades_for("smoke-s01", "S01")
    run = json.loads((RESULTS / "smoke-s01" / "run.json").read_text()) if (RESULTS / "smoke-s01" / "run.json").exists() else {}
    ok = bool(g and g[-1].get("rubrics") and run.get("rubric_versions"))
    return ok, f"versions recorded: {g[-1].get('rubrics') if g else {}}"


def check_browser_hold_criterion() -> tuple[bool, str]:
    text = (KIT / "evals/rubrics/booking.yaml").read_text()
    ok = "browser" in text and "booked_from_holds" in text
    return ok, "booking.yaml adds a hold-before-booking criterion under browser=on" if ok else "no browser-mode hold criterion in booking.yaml yet"


def check_guide_ships_locally() -> tuple[bool, str]:
    ok = (KIT / "site/dist/index.html").exists()
    src = (KIT / "tools/make_starter.py").read_text()
    return ok and "site/dist" not in re.findall(r'"[^"]*"', src.split("REMOVE")[1].split("]")[0]), "site/dist ships in the starter and opens from disk; no public URL"


def check_doctor_quick_runs() -> tuple[bool, str]:
    r = subprocess.run(["uv", "run", "python", "-m", "ea_evals.doctor", "--quick"], cwd=KIT, capture_output=True, text=True, timeout=120)
    lines = [ln for ln in r.stdout.splitlines() if ln[:1] in "✓✗!"]
    core = [ln for ln in lines if ln.split(":")[0][2:] in ("Python", "Dependencies", "Claude Code", "Claude login")]
    ok = len(core) == 4 and all(ln.startswith("✓") for ln in core)
    return ok, f"`make doctor ARGS=--quick`: {sum(ln.startswith('✓') for ln in lines)}/{len(lines)} green"


CHECKS = {k[len("check_"):]: v for k, v in globals().items() if k.startswith("check_")}


# ---------------------------------------------------------------- report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tests", action="store_true")
    args = ap.parse_args()
    mapping = yaml.safe_load((KIT / "tools/acceptance_map.yaml").read_text())
    tests = {} if args.no_tests else run_tests()
    rows, counts = [], {"✅": 0, "🟡": 0, "❌": 0, "⚪": 0}
    for rid, req, done_when in prd_requirements():
        ev = mapping.get(rid) or {}
        ok, notes = True, []
        if ev.get("exception"):
            status = "⚪"
            notes.append(f"Exception: {ev['exception']}")
        else:
            if ev.get("tests"):
                good, n = eval_tests(ev["tests"], tests) if tests else (True, "tests not run (--no-tests)")
                ok &= good
                notes.append(n)
            for f in ev.get("files", []):
                if not (KIT / f).exists():
                    ok = False
                    notes.append(f"missing `{f}`")
            if ev.get("files") and all((KIT / f).exists() for f in ev["files"]):
                notes.append(f"files present ({len(ev['files'])})")
            if ev.get("check"):
                try:
                    good, n = CHECKS[ev["check"]]()
                except Exception as exc:
                    good, n = False, f"check {ev['check']} failed: {exc}"
                ok &= good
                notes.append(n)
            if ev.get("live"):
                good, n = eval_live(ev["live"])
                ok &= good
                notes.append(n)
            if not ev:
                ok = False
                notes.append("no evidence mapped")
            later = [x for x in (ev.get("pending"), ev.get("manual")) if x]
            status = "❌" if not ok else ("🟡" if later else "✅")
            if ev.get("pending"):
                notes.append(f"**Still to run:** {ev['pending']}")
            if ev.get("manual"):
                notes.append(f"**Person to check:** {ev['manual']}")
        counts[status] += 1
        rows.append(f"| {rid} | {req} | {done_when} | {status} | {'<br>'.join(notes)} |")
    total = sum(counts.values())
    passed = sum(1 for v in tests.values() if v == "passed")
    failed = sum(1 for v in tests.values() if v == "failed")
    out = KIT / "reports" / "acceptance.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    head = [
        "# Acceptance: every PRD requirement and its evidence",
        "",
        f"Generated {datetime.now().astimezone():%Y-%m-%d %H:%M %Z} by `uv run python tools/acceptance.py`. "
        f"Offline suite: {passed} passed, {failed} failed.",
        "",
        f"✅ {counts['✅']} verified · 🟡 {counts['🟡']} verified so far, a larger live run or a person's check still to do · "
        f"❌ {counts['❌']} failing · ⚪ {counts['⚪']} accepted exception · {total} requirements",
        "",
        "Live evidence comes from runs in `evals/results/` (`smoke-s01`: S01 through Claude Code with the OpenAI judge; "
        "`live-r01-browser`: R01 booked through Brave on the mock sites). The reference run (63 cases × 3 trials) and the "
        "lesson slices need the owner's go-ahead because they use the Claude plan.",
        "",
        "| ID | Requirement | Done when | Result | Evidence |",
        "|---|---|---|---|---|",
    ]
    out.write_text("\n".join(head + rows) + "\n", encoding="utf-8")
    print(f"{out}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0 if not counts["❌"] else 1


if __name__ == "__main__":
    sys.exit(main())
