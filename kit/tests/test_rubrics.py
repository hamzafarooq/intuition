"""Every rubric file validates against spec/08-rubrics.md and evals/rubrics/_functions.yaml (the contract)."""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from ea_evals.cases import TYPES
from ea_evals.checks import CHECKS, REFERENCES
from ea_evals.rubrics import (
    AXES,
    GRADERS,
    KINDS,
    LEVELS,
    TEAM_AXES,
    all_rubrics,
    check_items,
    eval_when,
    rubrics_dir,
    validate_when,
)

RUBRICS = all_rubrics()
CONTRACT = yaml.safe_load((rubrics_dir() / "_functions.yaml").read_text(encoding="utf-8"))
FUNCTIONS = {f["name"]: f for f in CONTRACT["functions"]}
ARTIFACTS = {"final", "sent", "draft", "brief", "deck", "handoffs", "trace"}  # conventions.artifacts
# Implemented checks the contract lists only in its closing comment ("Spec functions that no rubric uses yet"):
# spec/08 names them, so they stay available to students' rubrics.
EXTRA_CHECKS = {"sent_contains", "bookings", "skill_loaded"}
EXPECTED_RUBRICS = {"conduct", "scheduling", "triage", "brief", "deck", "email", "booking", "research", "safety", "team",
                    "monday_brief"}


def criteria() -> list[tuple[str, dict[str, Any]]]:
    return [(name, c) for name, r in RUBRICS.items() for c in r.get("criteria", [])]


def crit_id(item: tuple[str, dict[str, Any]]) -> str:
    return f"{item[0]}/{item[1]['id']}"


ALL_CRITERIA = criteria()


def test_every_rubric_is_present():
    assert set(RUBRICS) == EXPECTED_RUBRICS


@pytest.mark.parametrize("name", sorted(RUBRICS))
def test_rubric_header(name: str):
    r = RUBRICS[name]
    assert r["id"] == name
    assert isinstance(r.get("version"), int) and r["version"] >= 1
    assert r.get("description", "").strip()
    assert r["applies_to"] and set(r["applies_to"]) <= set(TYPES)
    t = r["scoring"]["scored_threshold"]
    assert 0 < float(t) <= 1
    ids = [c["id"] for c in r["criteria"]]
    assert len(ids) == len(set(ids)), f"duplicate criterion ids in {name}"
    for c in r["criteria"]:
        assert re.fullmatch(r"[a-z][a-z0-9-]*", c["id"]), c["id"]
    assert isinstance(r.get("signals", []), list)


def test_conduct_applies_to_every_type():
    assert set(RUBRICS["conduct"]["applies_to"]) == set(TYPES)


@pytest.mark.parametrize("item", ALL_CRITERIA, ids=crit_id)
def test_criterion_fields(item: tuple[str, dict[str, Any]]):
    rubric, c = item
    assert c.get("kind") in KINDS
    assert c.get("level") in LEVELS
    assert c.get("axis") in AXES
    assert c.get("grader") in GRADERS
    if c["kind"] == "team":
        assert c.get("team_axis") in TEAM_AXES, f"{rubric}/{c['id']} needs a team_axis"
    else:
        assert "team_axis" not in c
    if c["grader"] in ("judge", "code_then_judge"):
        for key in ("question", "pass_example", "fail_example"):
            assert str(c.get(key) or "").strip(), f"{rubric}/{c['id']} needs {key}"
        assert c.get("artifact", "final") in ARTIFACTS
    if c["grader"] in ("code", "code_then_judge"):
        assert check_items(c), f"{rubric}/{c['id']} needs a check"
    if c["grader"] == "judge":
        assert "check" not in c, f"{rubric}/{c['id']}: a judge criterion has no check (use code_then_judge)"
    if c["grader"] == "code":
        assert "question" not in c and "reference" not in c
    placeholders = re.findall(r"\{fill\.(\w+)\}", str(c.get("question", "")))
    if placeholders:
        assert c["grader"] in ("judge", "code_then_judge")


def test_team_axes_cover_the_team_chart():
    """The team spider chart (spec/09) has one axis per team criterion."""
    used = [c["team_axis"] for _, c in ALL_CRITERIA if c["kind"] == "team"]
    assert sorted(used) == sorted(TEAM_AXES)


@pytest.mark.parametrize("item", ALL_CRITERIA, ids=crit_id)
def test_when_expressions_parse(item: tuple[str, dict[str, Any]]):
    _, c = item
    validate_when(c.get("when"))
    for i in check_items(c):
        validate_when(i.get("when"))


@pytest.mark.parametrize("expr", [
    "fill.x < 3", "len(fill.x) == 2", "fill.x[0] == 'a'", "fill.x + 1 == 2", "__import__('os')",
    "fill.x == 1 < 2", "foo == 1", "fill.x if fill.y else fill.z", "fill.f()", "'a'.x == 1",
])
def test_validate_when_rejects_anything_outside_the_grammar(expr: str):
    with pytest.raises((ValueError, SyntaxError)):
        validate_when(expr)


@pytest.mark.parametrize(("expr", "scope", "want"), [
    ("'x' in fill", {"fill": {"x": None}}, True),            # has the key, even when null
    ("'x' in fill", {"fill": {}}, False),
    ("fill.x == true", {"fill": {}}, False),                  # missing is not true
    ("not fill.x", {"fill": {}}, True),
    ("fill.mode in ['create', 'move']", {"fill": {"mode": "move"}}, True),
    ("'email_send' not in case.pre_authorized", {"case": {"pre_authorized": ["email_send"]}}, False),
    ("'email_send' not in case.pre_authorized", {"case": {}}, True),
    ("maya.approvals == 'edit_then_yes' and (fill.a or fill.b)", {"maya": {"approvals": "edit_then_yes"}, "fill": {"b": 1}}, True),
    ("fill.privacy == true and 'duration' in fill", {"fill": {"privacy": True, "duration": 45}}, True),
    ("variant.gate == 'off'", {"variant": {"gate": "off"}}, True),
    ("fill.expected.email_sent == null", {"fill": {"expected": {}}}, True),
])
def test_when_semantics(expr: str, scope: dict[str, Any], want: bool):
    full = {"fill": {}, "maya": {}, "case": {}, "variant": {}, **scope}
    assert eval_when(expr, full) is want


# ---------------------------------------------------------------- functions


def used_functions() -> dict[str, set[str]]:
    """function name -> {rubric/criterion} that use it, as a check or a reference."""
    out: dict[str, set[str]] = {}
    for rubric, c in ALL_CRITERIA:
        for i in check_items(c):
            out.setdefault(i["fn"], set()).add(f"{rubric}/{c['id']}")
        refs = c.get("reference")
        for r in (refs if isinstance(refs, list) else [refs] if refs else []):
            out.setdefault(r["fn"], set()).add(f"{rubric}/{c['id']}")
    return out


@pytest.mark.parametrize("item", ALL_CRITERIA, ids=crit_id)
def test_every_fn_exists_and_its_args_bind(item: tuple[str, dict[str, Any]]):
    rubric, c = item
    for i in check_items(c):
        assert set(i) <= {"fn", "args", "when"}, i
        fn = CHECKS.get(i["fn"])
        assert fn is not None, f"{rubric}/{c['id']}: no check function {i['fn']}"
        inspect.signature(fn).bind(None, **(i.get("args") or {}))
    refs = c.get("reference")
    for r in (refs if isinstance(refs, list) else [refs] if refs else []):
        fn = REFERENCES.get(r["fn"])
        assert fn is not None, f"{rubric}/{c['id']}: no reference builder {r['fn']}"
        inspect.signature(fn).bind(None, **(r.get("args") or {}))


def test_every_contract_function_is_implemented():
    for name, f in FUNCTIONS.items():
        registry = CHECKS if f["role"] == "check" else REFERENCES
        assert f["role"] in ("check", "reference"), name
        assert name in registry, f"_functions.yaml lists {name} ({f['role']}) but ea_evals.checks doesn't implement it"


def test_every_implemented_function_is_in_the_contract():
    assert set(CHECKS) - set(FUNCTIONS) == EXTRA_CHECKS
    assert set(REFERENCES) <= set(FUNCTIONS)
    for name in EXTRA_CHECKS:  # documented in the contract's closing comment
        assert name in (rubrics_dir() / "_functions.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(FUNCTIONS))
def test_contract_args_match_the_implementation(name: str):
    f = FUNCTIONS[name]
    fn = CHECKS.get(name) or REFERENCES[name]
    params = list(inspect.signature(fn).parameters.values())[1:]  # after ctx
    impl = {p.name: p for p in params}
    contract = {a["name"]: a for a in f.get("args") or []}
    assert set(contract) == set(impl), f"{name}: contract args {sorted(contract)} vs implementation {sorted(impl)}"
    for arg, spec in contract.items():
        required = spec.get("default") == "required"
        has_default = impl[arg].default is not inspect.Parameter.empty
        assert required != has_default, f"{name}({arg}): contract says {'required' if required else 'optional'}"
    assert f.get("source") in ("spec", "new")
    assert str(f.get("returns_yes_when" if f["role"] == "check" else "returns", "")).strip()


def test_contract_used_by_matches_the_rubrics():
    used = used_functions()
    for name, f in FUNCTIONS.items():
        assert set(f.get("used_by") or []) == used.get(name, set()), f"{name}: used_by is out of date"
    assert set(used) <= set(FUNCTIONS), f"rubrics use functions the contract doesn't list: {set(used) - set(FUNCTIONS)}"


def test_tool_sets_match_the_code():
    from ea_evals import checks
    from ea_world import approvals

    sets = CONTRACT["conventions"]["tool_sets"]
    assert sets["write_tools"] == checks.WRITE_TOOLS
    assert sets["gated_tools"] == checks.GATED_TOOLS
    assert set(sets["gated_tools"]) == approvals.GATED
    assert sets["outbound_tools"] == checks.OUTBOUND_TOOLS
    assert sets["read_tools"] == checks.READ_TOOLS


def test_references_in_args_are_well_formed():
    """`$` references: $fill.<key>, $maya.<key>, $case.<key> or $gold.<file>[.<key>] (conventions.references)."""
    gold = Path(rubrics_dir()).parents[1] / "world" / "gold"

    def walk(v: Any) -> list[str]:
        if isinstance(v, str):
            return [v] if v.startswith("$") else []
        if isinstance(v, list):
            return [x for i in v for x in walk(i)]
        if isinstance(v, dict):
            return [x for i in v.values() for x in walk(i)]
        return []

    for rubric, c in ALL_CRITERIA:
        refs = c.get("reference")
        blobs = [i.get("args") for i in check_items(c)] + [r.get("args") for r in (refs if isinstance(refs, list) else [refs] if refs else [])]
        for ref in walk(blobs):
            head, _, rest = ref[1:].partition(".")
            assert head in ("fill", "maya", "case", "gold") and rest, f"{rubric}/{c['id']}: bad reference {ref}"
            if head == "gold":
                assert (gold / f"{rest.split('.')[0]}.json").exists(), f"{rubric}/{c['id']}: no gold file for {ref}"
