"""The golden dataset (spec/07-golden-dataset.md): every case validates, every applicable criterion finds the fill
fields it reads, both coverage tables in evals/golden/README.md are complete, the workshop subset has 20 cases,
and every slice in slices.yaml resolves."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from ea_evals import variants as V
from ea_evals.cases import TYPES, golden_dir, load_all, load_slices, slice_plan
from ea_evals.checks import CHECKS
from ea_evals.context import TrialContext
from ea_evals.rubrics import case_rubrics, check_items, eval_when, fill_refs, load_rubric, resolve, rubrics_dir
from ea_world import core
from ea_world import state as world_state
from ea_world.approvals import GATED
from ea_world.paths import world_dir

CASES = load_all()
README = (golden_dir() / "README.md").read_text(encoding="utf-8")
REQUIRED = {"id", "slug", "type", "kind", "tags", "request", "turns", "world", "maya", "pre_authorized", "fill", "budgets", "notes"}
APPROVALS = {"explicit_yes", "thumbs_up", "vague", "deny", "edit_then_yes", "none"}
WORKSHOP = {"S01", "S02", "S07", "S10", "T01", "B01", "B05", "D01", "E01", "E02", "E07", "R01", "R05", "W02", "W03",
            "X01", "X02", "X07", "X09", "M01"}  # the ✓ column of spec/07
COUNTS = {"scheduling": 10, "triage": 6, "brief": 6, "deck": 4, "email": 8, "booking": 8, "research": 6, "safety": 12, "team": 3}


@pytest.fixture(autouse=True)
def _kit_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("EA_KIT_ROOT", "EA_WORLD_DIR", "EA_RUN_DIR", "EA_FAULTS"):
        monkeypatch.delenv(name, raising=False)


def scope(case: dict[str, Any]) -> dict[str, Any]:
    return {"fill": case.get("fill") or {}, "maya": case.get("maya") or {}, "case": case, "variant": {}}


def applicable(case: dict[str, Any]) -> list[tuple[str, dict[str, Any], list[dict[str, Any]]]]:
    """(rubric, criterion, applicable check items) for every criterion whose `when` holds for this case."""
    out = []
    s = scope(case)
    for name in case_rubrics(case):
        for c in load_rubric(name)["criteria"]:
            if eval_when(c.get("when"), s):
                out.append((name, c, [i for i in check_items(c) if eval_when(i.get("when"), s)]))
    return out


# ---------------------------------------------------------------- the cases


def test_case_count_and_types():
    assert len(CASES) == 63
    for t, n in COUNTS.items():
        assert sum(c["type"] == t for c in CASES.values()) == n, t


@pytest.mark.parametrize("cid", sorted(CASES))
def test_case_schema(cid: str):
    c = CASES[cid]
    path = Path(c["_path"])
    assert REQUIRED <= set(c), f"{cid} lacks {REQUIRED - set(c)}"
    assert c["id"] == cid and re.fullmatch(r"[STBDERWXM]\d{2}", cid)
    assert path.name == f"{cid}-{c['slug']}.yaml" and re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", c["slug"])
    assert c["type"] in TYPES and path.parent.name == c["type"]
    assert cid[0] == {"scheduling": "S", "triage": "T", "brief": "B", "deck": "D", "email": "E", "booking": "R",
                      "research": "W", "safety": "X", "team": "M"}[c["type"]]
    assert c["kind"] in ("capability", "regression")
    assert isinstance(c.get("workshop", False), bool)
    assert isinstance(c["tags"], list) and all(isinstance(t, str) for t in c["tags"])
    assert isinstance(c["request"], str) and c["request"].strip()
    assert isinstance(c["turns"], list) and all(isinstance(t, str) and t.strip() for t in c["turns"])
    assert isinstance(c["notes"], str) and c["notes"].strip()
    assert isinstance(c["fill"], dict)
    # world
    assert set(c["world"]) == {"variants", "faults"}
    for v in c["world"]["variants"]:
        assert (world_dir() / "variants" / f"{v}.yaml").exists(), f"{cid}: no world variant {v}"
    core.parse_faults(c["world"]["faults"])
    # the simulated Maya
    maya = c["maya"]
    assert set(maya) == {"answers", "approvals", "edit"}
    assert maya["approvals"] in APPROVALS
    assert isinstance(maya["answers"], dict) and all(isinstance(v, str) and v for v in maya["answers"].values())
    assert bool(str(maya["edit"]).strip()) == (maya["approvals"] == "edit_then_yes")
    # approvals
    assert isinstance(c["pre_authorized"], list) and set(c["pre_authorized"]) <= GATED
    if (c["fill"] or {}).get("pre_authorized"):
        assert c["pre_authorized"], f"{cid}: fill.pre_authorized but no case.pre_authorized tools"
    # rubrics: exactly one of rubric / rubrics; each exists and applies to this case type
    assert ("rubric" in c) != ("rubrics" in c), f"{cid}: give rubric or rubrics, not both"
    names = c["rubrics"] if "rubrics" in c else [c["rubric"]]
    assert names and "conduct" not in names
    for n in names:
        assert (rubrics_dir() / f"{n}.yaml").exists(), f"{cid}: no rubric {n}"
        assert c["type"] in load_rubric(n)["applies_to"], f"{cid}: rubric {n} doesn't apply to {c['type']}"
    # budgets
    b = c["budgets"]
    assert set(b) == {"max_cost_usd", "max_seconds"} and b["max_cost_usd"] > 0 and b["max_seconds"] > 0


def test_ids_unique_across_files():
    files = sorted(golden_dir().glob("*/*.yaml"))
    ids = [yaml.safe_load(p.read_text(encoding="utf-8"))["id"] for p in files if p.name != "slices.yaml"]
    assert len(ids) == len(set(ids)) == 63


def test_workshop_subset():
    chosen = {c["id"] for c in CASES.values() if c.get("workshop")}
    assert len(chosen) == 20
    assert chosen == WORKSHOP


# ---------------------------------------------------------------- fills


def documented_fill_fields() -> dict[str, set[str]]:
    """The README's "Fill fields by rubric" table: rubric -> documented fill keys."""
    section = README.split("### Fill fields by rubric", 1)[1].split("\n## ", 1)[0]
    out: dict[str, set[str]] = {}
    for line in section.splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(cells) != 2 or cells[0] in ("Rubric", "") or set(cells[0]) <= {"-"}:
            continue
        for name in re.split(r",\s*", cells[0]):
            out.setdefault(name, set()).update(re.findall(r"`(\w+)`", cells[1]))
    return out


@pytest.mark.parametrize("cid", sorted(CASES))
def test_fill_has_every_field_its_applicable_criteria_read(cid: str):
    case = CASES[cid]
    fill = case["fill"]
    missing = []
    for rubric, c, items in applicable(case):
        used: set[str] = set()
        for i in items:
            used |= fill_refs(i.get("args") or {})
        refs = c.get("reference")
        for r in (refs if isinstance(refs, list) else [refs] if refs else []):
            used |= fill_refs(r.get("args") or {})
        if c.get("grader") in ("judge", "code_then_judge"):
            used |= set(re.findall(r"\{fill\.(\w+)\}", c.get("question", "")))
        missing += [f"{rubric}/{c['id']} needs fill.{k}" for k in sorted(used - set(fill))]
        for key in ("maya", "case"):
            blob = str([i.get("args") for i in items])
            for k in re.findall(rf"\${key}\.(\w+)", blob):
                assert k in case[key] if key == "maya" else k in case, f"{rubric}/{c['id']} needs {key}.{k}"
    assert not missing, f"{cid}: " + "; ".join(missing)


@pytest.mark.parametrize("cid", sorted(CASES))
def test_fill_keys_are_documented(cid: str):
    """Every fill key is in the README's table for one of the case's rubrics (catches typos like `exlude_days`)."""
    docs = documented_fill_fields()
    case = CASES[cid]
    names = case.get("rubrics") or [case["rubric"]]
    assert all(n in docs for n in names), f"README table lacks a row for {names}"
    allowed = set().union(*(docs[n] for n in names))
    unknown = sorted(set(case["fill"]) - allowed)
    assert not unknown, f"{cid}: fill keys not documented for {names}: {unknown}"


@pytest.mark.parametrize("cid", sorted(CASES))
def test_applicable_checks_run_on_a_fresh_world(cid: str, tmp_path: Path):
    """Every applicable code check accepts the case's resolved args and runs (no exception, no unknown function)."""
    case = CASES[cid]
    trial = tmp_path / "t1"
    world_state.init_run_dir(trial, case["world"]["variants"])
    shutil.copytree(trial / "state", trial / "initial_state")
    shutil.copytree(trial / "state", trial / "final_state")
    for name in ("trace.jsonl", "calls.jsonl", "approvals.jsonl"):
        (trial / name).write_text("", encoding="utf-8")
    ctx = TrialContext(case, trial)
    for rubric, c, items in applicable(case):
        for i in items:
            args = resolve(i.get("args") or {}, ctx)
            v = CHECKS[i["fn"]](ctx, **args)
            assert v.value in ("yes", "no", "cant_tell", "na"), (rubric, c["id"], v)


# ---------------------------------------------------------------- coverage tables


def coverage_table(heading: str) -> dict[str, list[str]]:
    section = README.split(heading, 1)[1].split("\n###", 1)[0].split("\n## ", 1)[0]
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(cells) != 2 or cells[1] == "Cases" or set(cells[1]) <= {"-", ""}:
            continue
        assert re.fullmatch(r"[A-Z]\d{2}(, [A-Z]\d{2})*", cells[1]), f"Cases cell must hold only comma-separated ids: {line}"
        rows[cells[0]] = cells[1].split(", ")
    return rows


def faults_cases() -> set[str]:
    return set(load_slices()["lesson5-faults"]["cases"])


def tags(cid: str) -> set[str]:
    return set(CASES[cid].get("tags") or [])


# spec/07 "Coverage": (README row pattern, the cases spec/07 names, what makes a case exercise the trap)
SPEC_TRAPS = {
    "two Dans": (r"Two Dans", {"S01", "S06"}, lambda c: "two-dans" in tags(c)),
    "time zones and the London clock change": (r"Time zones", {"S01", "S06", "S10", "E01", "X09"},
                                               lambda c: tags(c) & {"tz", "dst-week"}),
    "Thursday clash": (r"Thursday clash", {"E01", "E02", "E03"},
                       lambda c: "2026-10-29" in (CASES[c]["fill"].get("exclude_days") or [])),
    "private event": (r"Private event", {"X09"}, lambda c: CASES[c]["fill"].get("privacy") is True),
    "thumbs-up": (r"Thumbs-up", {"E02"}, lambda c: CASES[c]["maya"]["approvals"] == "thumbs_up"),
    "injection email": (r"Injection email", {"T01", "T04", "X01", "X03"}, lambda c: "injection" in tags(c)),
    "injection page": (r"Injection web page", {"W03", "X12"}, lambda c: "injection" in tags(c) and "web" in tags(c) | {CASES[c]["type"].replace("research", "web")}),
    "dropped thread": (r"Dropped thread", {"T01", "T02", "B01"}, lambda c: "dropped-thread" in tags(c)),
    "policy fare": (r"breaks policy", {"R01", "R02", "R03"}, lambda c: "policy" in tags(c)),
    "duplicate on retry": (r"Duplicate on retry", None, lambda c: c in faults_cases()),
    "decline signal": (r"Decline signal", {"S07"}, lambda c: "signal" in tags(c)),
}
SPEC_INCIDENTS = {
    "email injection": (r"Email injection", {"X01", "X03", "X05", "W03", "X12"},
                        lambda c: CASES[c]["fill"].get("attack") is True or CASES[c]["fill"].get("injection") is True),
    "action without approval": (r"Action without approval", {"E02", "E07", "E08", "R05"},
                                lambda c: CASES[c]["maya"]["approvals"] in ("thumbs_up", "deny", "vague")
                                and (CASES[c]["fill"].get("expect_sent") is False or CASES[c]["fill"].get("expect_booked") is False)),
    "data kept after disconnect": (r"Data kept after disconnect", {"X07"}, lambda c: CASES[c]["fill"].get("disconnect") is True),
}


@pytest.mark.parametrize(("heading", "spec"), [
    ("### Every planted trap has a case", SPEC_TRAPS),
    ("### Every Instinct incident has a case", SPEC_INCIDENTS),
], ids=["traps", "incidents"])
def test_coverage_tables_are_complete(heading: str, spec: dict[str, Any]):
    rows = coverage_table(heading)
    assert len(rows) == len(spec), f"{heading}: {len(rows)} rows, spec/07 lists {len(spec)}"
    for what, (pattern, spec_cases, exercises) in spec.items():
        hits = [k for k in rows if re.search(pattern, k, re.I)]
        assert len(hits) == 1, f"{heading}: no single row for {what} (pattern {pattern!r}): {hits}"
        ids = rows[hits[0]]
        assert ids, f"{what}: no case"
        for cid in ids:
            assert cid in CASES, f"{what}: unknown case {cid}"
            assert exercises(cid), f"{what}: {cid} doesn't look like it exercises this ({sorted(tags(cid))})"
        want = faults_cases() if spec_cases is None else spec_cases
        assert want <= set(ids), f"{what}: spec/07 names {sorted(want)}, README has {ids}"


def test_readme_case_table_matches_the_files():
    section = README.split("## Cases", 1)[1].split("\n## ", 1)[0]
    seen = set()
    for line in section.splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if len(cells) != 6 or not re.fullmatch(r"[A-Z]\d{2}", cells[0]):
            continue
        cid, type_, kind, workshop, rubrics, request = cells
        c = CASES[cid]
        seen.add(cid)
        assert (type_, kind) == (c["type"], c["kind"]), cid
        assert (workshop == "✓") == bool(c.get("workshop")), cid
        assert [r.strip() for r in rubrics.split(",")] == (c.get("rubrics") or [c["rubric"]]), cid
        assert request.split(" → ")[0] == c["request"], cid
    assert seen == set(CASES)


# ---------------------------------------------------------------- slices

# spec/07 "Slices": cases, trials and (case, variant label) pairs.
W = sorted(WORKSHOP)
SPEC_SLICES: dict[str, tuple[list[str], int, list[str], dict[str, list[str]]]] = {
    # name: (cases, trials, variant labels for every case, extra {label: cases})
    "setup-baseline": (["S01", "T01", "B01", "E01", "E02", "R01", "W03", "X01", "X07", "M01"], 1, ["default"], {}),
    "lesson2-skills": (["S01", "S02", "S07", "B01", "D01", "E01"], 1, ["skills=off", "default"], {}),
    "lesson3-agents": (W, 1, ["default"], {}),
    "lesson3-passk": (["S01", "E01", "B01"], 3, ["default"], {}),
    "lesson3-selfcheck": (["S01", "S07", "B01", "D01", "E01", "R01"], 1, ["selfcheck=off", "default"], {}),
    "lesson4-gate": (["E02", "E07", "E08", "R05"], 5, ["default"], {"gate=off": ["E02", "R05"]}),
    "lesson4-safety": (["X01", "X02", "X07", "X09", "W03"], 1, ["default"], {}),
    "lesson5-team": (["M01", "M03"], 1, ["team=off", "default"], {}),
    "lesson5-faults": (["S01", "R08"], 1, ["faults=timeout_after_write", "faults=timeout_after_write,idempotency=auto"], {}),
    "lesson5-harness": (["S01", "T01", "E01", "X01", "W02"], 1, ["default", "hooks=off", "subagents=off", "model=sonnet"], {}),
    "wrap-final": (W, 1, ["default"], {}),
    "browser-demo": (["R01", "R04"], 1, ["browser=on"], {}),
}
VALUES = {"skills": {"on", "off"}, "selfcheck": {"on", "off"}, "gate": {"on", "off"}, "team": {"on", "off"},
          "hooks": {"on", "off"}, "subagents": {"on", "off"}, "idempotency": {"off", "auto"}, "browser": {"on", "off"},
          "model": {"opus", "sonnet", "haiku"}, "effort": {"low", "medium", "high", "max"}, "faults": core.FAULTS}


def test_every_spec_slice_is_defined():
    assert set(load_slices()) == set(SPEC_SLICES)


@pytest.mark.parametrize("name", sorted(SPEC_SLICES))
def test_slice(name: str):
    s = load_slices()[name]
    assert s.get("description", "").strip()
    sets = list(s.get("variants") or [{}]) + [v for e in s.get("extra") or [] for v in e.get("variants") or [{}]]
    for v in sets:
        for k, val in (v or {}).items():
            assert k in V.KNOWN, f"{name}: unknown variant key {k}"
            assert isinstance(val, str), f"{name}: quote variant values ({k}: {val!r})"
            assert val in VALUES[k], f"{name}: {k}={val}"
    raw_cases = s["cases"] if isinstance(s["cases"], list) else []
    assert s["cases"] == "workshop" or all(c in CASES for c in raw_cases)
    for e in s.get("extra") or []:
        assert all(c in CASES for c in e["cases"])
    plan, trials, _ = slice_plan(name)
    cases, want_trials, labels, extra = SPEC_SLICES[name]
    assert trials == want_trials
    got = {(c["id"], V.label(v)) for c, v in plan}
    want = {(cid, lab) for cid in cases for lab in labels} | {(cid, lab) for lab, ids in extra.items() for cid in ids}
    assert got == want
    # compare pairs name variant sets the slice runs (by their normalized form)
    labels_run = {V.label(v) for v in sets}
    for pair in s.get("compare") or []:
        assert len(pair) == 2 and all(V.label({k: str(x) for k, x in (p or {}).items()}) in labels_run for p in pair), pair


def test_slices_trial_total_matches_its_comment():
    text = (golden_dir() / "slices.yaml").read_text(encoding="utf-8")
    m = re.search(r"Trials at the defaults: ([\d +]+) = (\d+)", text)
    assert m, "slices.yaml should state the trial total"
    total = 0
    for name in load_slices():
        plan, trials, _ = slice_plan(name)
        total += len(plan) * trials
    assert total == int(m.group(2)) == sum(int(x) for x in m.group(1).split("+"))
