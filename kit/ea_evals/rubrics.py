"""Rubric files: loading, the `when` expression language, `$` references, grading and scoring.

Schema: spec/08-rubrics.md and evals/rubrics/_functions.yaml (conventions).
"""

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ea_world import paths

from .checks import CHECKS, REFERENCES
from .context import TrialContext, Verdict

AXES = ["correct", "safety", "grounded", "process", "communication", "honest"]
TEAM_AXES = ["right_specialist", "complete_handoffs", "no_duplicated_work", "consistent_final", "challenger_catches", "qa_agrees"]
KINDS = {"outcome", "process", "team"}
LEVELS = {"must", "scored"}
GRADERS = {"code", "judge", "code_then_judge"}


def rubrics_dir() -> Path:
    return paths.kit_root() / "evals" / "rubrics"


def load_rubric(name: str, folder: Path | None = None) -> dict[str, Any]:
    p = Path(folder or rubrics_dir()) / f"{name}.yaml"
    with open(p, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data.setdefault("scoring", {"scored_threshold": 0.75})
    return data


def all_rubrics(folder: Path | None = None) -> dict[str, dict[str, Any]]:
    out = {}
    for p in sorted(Path(folder or rubrics_dir()).glob("*.yaml")):
        if p.name.startswith("_"):
            continue
        out[p.stem] = load_rubric(p.stem, folder)
    return out


def case_rubrics(case: dict[str, Any]) -> list[str]:
    """The case's rubric(s) plus conduct, which grades every case."""
    names = case.get("rubrics") or ([case["rubric"]] if case.get("rubric") else [])
    names = [n for n in names if n != "conduct"]
    return names + ["conduct"]


# ---------------------------------------------------------------- `when` expressions


class _Obj:
    """Attribute access over a dict, with None for missing keys; `'k' in obj` tests the key."""

    def __init__(self, data: dict[str, Any] | None):
        self.data = data or {}

    def get(self, name: str) -> Any:
        v = self.data.get(name)
        return _Obj(v) if isinstance(v, dict) else v

    def __contains__(self, key: object) -> bool:
        return key in self.data


_NAMES = {"true": True, "false": False, "True": True, "False": False, "null": None, "None": None}


def eval_when(expr: str | None, scope: dict[str, Any]) -> bool:
    if expr is None or str(expr).strip() == "":
        return True
    tree = ast.parse(str(expr), mode="eval")
    return bool(_eval(tree.body, {k: _Obj(v) if isinstance(v, dict) else v for k, v in scope.items()}))


def _eval(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, env) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval(node.operand, env)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        for op, comp in zip(node.ops, node.comparators, strict=True):
            right = _eval(comp, env)
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.In):
                ok = _contains(right, left)
            elif isinstance(op, ast.NotIn):
                ok = not _contains(right, left)
            else:
                raise ValueError(f"Operator {type(op).__name__} isn't allowed in `when`")
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        if node.id in _NAMES:
            return _NAMES[node.id]
        if node.id in env:
            return env[node.id]
        raise ValueError(f"Unknown name '{node.id}' in `when`")
    if isinstance(node, ast.Attribute):
        base = _eval(node.value, env)
        return base.get(node.attr) if isinstance(base, _Obj) else None
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, env) for e in node.elts]
    raise ValueError(f"`{ast.unparse(node)}` isn't allowed in `when`")


def _contains(container: Any, item: Any) -> bool:
    if container is None:
        return False
    if isinstance(container, _Obj):
        return item in container
    try:
        return item in container
    except TypeError:
        return False


def validate_when(expr: str | None) -> None:
    """Raise if the expression uses anything outside the grammar."""
    if not expr:
        return
    eval_when(expr, {"fill": {}, "maya": {}, "case": {}, "variant": {}})


# ---------------------------------------------------------------- `$` references


def resolve(value: Any, ctx: TrialContext) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        head, _, rest = value[1:].partition(".")
        if head == "gold":
            return ctx.gold.ref(rest)
        base = {"fill": ctx.fill, "maya": ctx.maya, "case": ctx.case, "variant": ctx.variant}.get(head)
        if base is None:
            raise ValueError(f"Unknown reference {value}")
        cur: Any = base
        for part in rest.split(".") if rest else []:
            cur = cur.get(part) if isinstance(cur, dict) else None
        return cur
    if isinstance(value, list):
        return [resolve(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}
    return value


def fill_refs(value: Any) -> set[str]:
    """Every `$fill.<key>` used in an args structure (for schema tests)."""
    out: set[str] = set()
    if isinstance(value, str) and value.startswith("$fill."):
        out.add(value[len("$fill.") :].split(".")[0])
    elif isinstance(value, list):
        for v in value:
            out |= fill_refs(v)
    elif isinstance(value, dict):
        for v in value.values():
            out |= fill_refs(v)
    return out


# ---------------------------------------------------------------- grading


def scope_for(ctx: TrialContext) -> dict[str, Any]:
    return {"fill": ctx.fill, "maya": ctx.maya, "case": ctx.case, "variant": ctx.variant}


def check_items(criterion: dict[str, Any]) -> list[dict[str, Any]]:
    c = criterion.get("check")
    if c is None:
        return []
    return c if isinstance(c, list) else [c]


def run_check(ctx: TrialContext, criterion: dict[str, Any]) -> Verdict:
    scope = scope_for(ctx)
    results = []
    for item in check_items(criterion):
        if not eval_when(item.get("when"), scope):
            continue
        fn = CHECKS.get(item["fn"])
        if fn is None:
            return Verdict.cant(f"no check function {item['fn']}")
        args = resolve(item.get("args") or {}, ctx)
        try:
            results.append(fn(ctx, **args))
        except Exception as exc:  # a broken check says so instead of crashing the run
            results.append(Verdict.cant(f"{item['fn']} failed: {exc}"))
    if not results or all(r.value == "na" for r in results):
        return Verdict("na", "; ".join(r.evidence for r in results))
    evidence = "; ".join(r.evidence for r in results if r.evidence)
    if any(r.value == "no" for r in results):
        return Verdict("no", evidence)
    if any(r.value == "cant_tell" for r in results):
        return Verdict("cant_tell", evidence)
    return Verdict("yes", evidence)


def build_reference(ctx: TrialContext, criterion: dict[str, Any]) -> str:
    ref = criterion.get("reference")
    if not ref:
        return ""
    refs = ref if isinstance(ref, list) else [ref]
    out = []
    for r in refs:
        fn = REFERENCES.get(r.get("fn", ""))
        if fn is None:
            out.append(f"(unknown reference {r.get('fn')})")
            continue
        try:
            out.append(fn(ctx, **resolve(r.get("args") or {}, ctx)))
        except Exception as exc:
            out.append(f"({r.get('fn')} failed: {exc})")
    return "\n\n".join(out)


@dataclass
class CriterionResult:
    id: str
    rubric: str
    kind: str
    level: str
    axis: str
    grader: str
    verdict: str
    evidence: str = ""
    reason: str = ""
    team_axis: str | None = None
    rubric_version: int = 1

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        if d["team_axis"] is None:
            d.pop("team_axis")
        return d


@dataclass
class Grade:
    criteria: list[CriterionResult] = field(default_factory=list)
    rubric_versions: dict[str, int] = field(default_factory=dict)
    must_pass: bool = True
    scored: float | None = None
    passed: bool = False
    judge_calls: int = 0


def grade_trial(ctx: TrialContext, judge: Any | None = None, rubric_folder: Path | None = None) -> Grade:
    """Grade one trial against the case's rubrics plus conduct."""
    names = case_rubrics(ctx.case)
    rubrics = {n: load_rubric(n, rubric_folder) for n in names}
    scope = scope_for(ctx)
    grade = Grade(rubric_versions={n: int(r.get("version", 1)) for n, r in rubrics.items()})
    deferred: list[tuple[str, dict[str, Any]]] = []
    for name, rub in rubrics.items():
        for c in rub.get("criteria", []):
            if not eval_when(c.get("when"), scope):
                continue
            if any(i.get("fn") == "qa_agrees" for i in check_items(c)):
                deferred.append((name, c))
                continue
            grade.criteria.append(_grade_one(ctx, name, rub, c, judge, grade))
    for name, c in deferred:  # qa_agrees reads the other verdicts
        grade.criteria.append(_grade_one(ctx, name, rubrics[name], c, judge, grade))
    threshold = max(float(r.get("scoring", {}).get("scored_threshold", 0.75)) for r in rubrics.values())
    counted = [r for r in grade.criteria if r.verdict not in ("na", "skipped")]
    musts = [r for r in counted if r.level == "must"]
    scored = [r for r in counted if r.level == "scored"]
    grade.must_pass = all(r.verdict == "yes" for r in musts)
    grade.scored = (sum(r.verdict == "yes" for r in scored) / len(scored)) if scored else None
    grade.passed = grade.must_pass and (grade.scored is None or grade.scored >= threshold)
    return grade


def _grade_one(ctx: TrialContext, rubric_name: str, rubric: dict[str, Any], c: dict[str, Any], judge: Any, grade: Grade) -> CriterionResult:
    grader = c.get("grader", "code")
    res = CriterionResult(
        id=c["id"], rubric=rubric_name, kind=c.get("kind", "outcome"), level=c.get("level", "must"),
        axis=c.get("axis", "correct"), grader=grader, verdict="cant_tell", team_axis=c.get("team_axis"),
        rubric_version=int(rubric.get("version", 1)),
    )
    if grader in ("code", "code_then_judge"):
        v = run_check(ctx, c)
        res.verdict, res.evidence = v.value, v.evidence
        if grader == "code" or v.value != "yes":
            ctx.verdicts[f"{rubric_name}/{c['id']}"] = {"value": res.verdict, "kind": res.kind, "level": res.level}
            return res
    if judge is None:
        res.verdict, res.reason = "skipped", "judge off (--no-judge)"
    else:
        jv = judge.grade(ctx, c, rubric_name, build_reference(ctx, c))
        grade.judge_calls += 1
        res.verdict, res.evidence, res.reason = jv["verdict"], jv.get("evidence", ""), jv.get("reason", "")
    ctx.verdicts[f"{rubric_name}/{c['id']}"] = {"value": res.verdict, "kind": res.kind, "level": res.level}
    return res
