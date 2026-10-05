"""Golden cases, selectors and slices."""

from pathlib import Path
from typing import Any

import yaml

from ea_world import paths

TYPES = ["scheduling", "triage", "brief", "deck", "email", "booking", "research", "safety", "team"]


def golden_dir() -> Path:
    return paths.kit_root() / "evals" / "golden"


def load_all(folder: Path | None = None) -> dict[str, dict[str, Any]]:
    folder = Path(folder or golden_dir())
    out: dict[str, dict[str, Any]] = {}
    for p in sorted(folder.glob("*/*.yaml")):
        if p.parent.name.startswith("_") or p.name == "slices.yaml":
            continue
        with open(p, encoding="utf-8") as f:
            case = yaml.safe_load(f)
        case["_path"] = str(p)
        out[case["id"]] = case
    return dict(sorted(out.items(), key=lambda kv: (TYPES.index(kv[1]["type"]) if kv[1]["type"] in TYPES else 99, kv[0])))


def select(selector: str, cases: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """`workshop`, `all`, `type:<t>`, `tag:<t>`, `kind:<k>`, or comma-separated ids (selectors can be mixed)."""
    cases = cases or load_all()
    out: list[dict[str, Any]] = []
    for part in [p.strip() for p in (selector or "").split(",") if p.strip()]:
        if part == "all":
            chosen = list(cases.values())
        elif part == "workshop":
            chosen = [c for c in cases.values() if c.get("workshop")]
        elif part.startswith("type:"):
            chosen = [c for c in cases.values() if c.get("type") == part[5:]]
        elif part.startswith("tag:"):
            chosen = [c for c in cases.values() if part[4:] in (c.get("tags") or [])]
        elif part.startswith("kind:"):
            chosen = [c for c in cases.values() if c.get("kind") == part[5:]]
        elif part in cases:
            chosen = [cases[part]]
        else:
            raise ValueError(f"Unknown case or selector '{part}'")
        for c in chosen:
            if c not in out:
                out.append(c)
    return out


def load_slices(folder: Path | None = None) -> dict[str, dict[str, Any]]:
    with open(Path(folder or golden_dir()) / "slices.yaml", encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("slices", {})


def slice_plan(name: str, cases: dict[str, dict[str, Any]] | None = None) -> tuple[list[tuple[dict[str, Any], dict[str, str]]], int, dict[str, Any]]:
    """[(case, variant set)], trials, and the slice definition."""
    slices = load_slices()
    if name not in slices:
        raise ValueError(f"Unknown slice '{name}'. Slices: {', '.join(slices)}")
    s = slices[name]
    cases = cases or load_all()
    chosen = select(s["cases"] if isinstance(s["cases"], str) else ",".join(s["cases"]), cases)
    plan: list[tuple[dict[str, Any], dict[str, str]]] = []
    for v in s.get("variants") or [{}]:
        for c in chosen:
            plan.append((c, {k: str(val) for k, val in (v or {}).items()}))
    for extra in s.get("extra") or []:
        extra_cases = select(",".join(extra["cases"]), cases)
        for v in extra.get("variants") or [{}]:
            for c in extra_cases:
                plan.append((c, {k: str(val) for k, val in (v or {}).items()}))
    return plan, int(s.get("trials", 1)), s
