"""Judge calibration (Lesson 3.5): students label judged criteria; we measure the judge's TPR and TNR.

`ea-eval label <run-id> --rubric email --criteria tone,answers-the-ask --n 20` writes
`evals/labels/<rubric>.yaml` with the artifacts and blank `human` fields. `ea-eval calibrate <run-id>
--rubric email` compares the judge with the labels (positive class: "yes").
"""

import json
from pathlib import Path
from typing import Any

import yaml

from ea_world import paths

from .cases import load_all
from .context import TrialContext
from .judge import artifact
from .rubrics import load_rubric
from .runner import load_grades, results_dir


def labels_path(rubric: str) -> Path:
    return paths.kit_root() / "evals" / "labels" / f"{rubric}.yaml"


def make_labels(run_id: str, rubric: str, criteria: list[str] | None = None, n: int = 20) -> Path:
    rub = load_rubric(rubric)
    judged = {c["id"]: c for c in rub["criteria"] if c.get("grader") in ("judge", "code_then_judge")}
    wanted = [c for c in (criteria or list(judged)) if c in judged]
    cases = load_all()
    items = []
    for g in load_grades(results_dir() / run_id):
        for c in g.get("criteria", []):
            if c["rubric"] != rubric or c["id"] not in wanted or c["verdict"] in ("na", "skipped"):
                continue
            ctx = TrialContext(cases[g["case_id"]], results_dir() / run_id / g["path"])
            crit = judged[c["id"]]
            items.append({
                "trial": f"{g['case_id']}/{g['harness']}/{g['variant']}/t{g['trial']}",
                "criterion": c["id"],
                "question": crit.get("question", ""),
                "pass_example": crit.get("pass_example", ""),
                "fail_example": crit.get("fail_example", ""),
                "artifact": artifact(ctx, crit.get("artifact")),
                "judge": c["verdict"],
                "human": None,
            })
            if len(items) >= n:
                break
        if len(items) >= n:
            break
    p = labels_path(rubric)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"rubric": rubric, "version": int(rub.get("version", 1)), "run_id": run_id,
           "instructions": "Read each artifact and answer the question yourself: set human to yes or no. Don't look at `judge` first.",
           "items": items}
    p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")
    return p


def calibrate(run_id: str, rubric: str) -> dict[str, Any]:
    doc = yaml.safe_load(labels_path(rubric).read_text(encoding="utf-8"))
    per: dict[str, dict[str, int]] = {}
    for it in doc.get("items", []):
        h = str(it.get("human") or "").lower()
        # Compare with the judge's CURRENT verdict, so label -> reword -> regrade -> calibrate reuses your labels.
        j = str(it.get("judge") or "").lower()
        gp = results_dir() / run_id / it.get("trial", "") / "grades.json"
        if gp.exists():
            for c in json.loads(gp.read_text(encoding="utf-8")).get("criteria", []):
                if c.get("rubric") == rubric and c.get("id") == it.get("criterion") and c.get("verdict") not in ("na", "skipped"):
                    j = str(c["verdict"]).lower()
        if h not in ("yes", "no"):
            continue
        row = per.setdefault(it["criterion"], {"tp": 0, "fn": 0, "tn": 0, "fp": 0})
        if h == "yes":
            row["tp" if j == "yes" else "fn"] += 1
        else:
            row["tn" if j == "no" else "fp"] += 1
    out = {}
    for crit, r in per.items():
        pos, neg = r["tp"] + r["fn"], r["tn"] + r["fp"]
        out[f"{rubric}/{crit}"] = {**r, "tpr": (r["tp"] / pos) if pos else None, "tnr": (r["tn"] / neg) if neg else None, "labelled": pos + neg}
    p = results_dir() / run_id / "calibration.json"
    existing = json.loads(p.read_text()) if p.exists() else {}
    existing.update(out)
    p.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return out
