"""Turn a failing trial into a draft golden case (`evals/golden/_drafts/<type>/<id>.yaml`)."""

import json
from pathlib import Path
from typing import Any

import yaml

from .cases import golden_dir, load_all
from .runner import results_dir

COPY_KEYS = ["slug", "type", "tags", "request", "turns", "world", "maya", "pre_authorized", "rubric", "rubrics", "fill", "budgets"]


def promote(run_id: str, case_id: str, harness: str, variant: str, trial: int | str) -> Path:
    t = str(trial).lstrip("t")
    tdir = results_dir() / run_id / case_id / harness / variant / f"t{t}"
    grades_path = tdir / "grades.json"
    if not grades_path.exists():
        raise FileNotFoundError(f"No graded trial at {tdir}")
    grades = json.loads(grades_path.read_text(encoding="utf-8"))
    case = load_all()[case_id]
    failing = [f"{c['rubric']}/{c['id']} ({c['verdict']}): {c.get('evidence', '')[:120]}" for c in grades.get("criteria", [])
               if c.get("verdict") not in ("yes", "na", "skipped")]
    drafts = golden_dir() / "_drafts" / case["type"]
    drafts.mkdir(parents=True, exist_ok=True)
    n = 1
    while (drafts / f"{case_id}-P{n}.yaml").exists():
        n += 1
    new_id = f"{case_id}-P{n}"
    draft: dict[str, Any] = {"id": new_id}
    for k in COPY_KEYS:
        if k in case:
            draft[k] = case[k]
    draft["slug"] = f"{case.get('slug', case_id.lower())}-regression-{n}"
    draft["kind"] = "regression"
    draft["workshop"] = False
    draft["notes"] = (
        f"DRAFT from {run_id} {case_id} {harness} {variant} t{t} ({tdir}). Review before moving it into the dataset: "
        "edit the request or world so it isolates the failure, check the fill, then move this file next to the other "
        f"{case['type']} cases. Failing criteria: " + ("; ".join(failing) if failing else "none (the trial passed)")
    )
    path = drafts / f"{new_id}.yaml"
    header = "# Draft golden case promoted from a failing trial. Schema: evals/golden/README.md\n"
    path.write_text(header + yaml.safe_dump(draft, sort_keys=False, allow_unicode=True, width=110), encoding="utf-8")
    return path
