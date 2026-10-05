"""Run variants: which harness and assistant configuration a trial uses (spec/09 "Variants", spec/05)."""

from typing import Any

OVERLAY_KEYS = {"skills", "selfcheck", "gate", "team", "hooks", "subagents"}
KNOWN = OVERLAY_KEYS | {"model", "effort", "idempotency", "faults", "browser"}
DEFAULTS = {"skills": "on", "selfcheck": "on", "gate": "on", "team": "on", "hooks": "on", "subagents": "on",
            "model": "opus", "idempotency": "off", "browser": "off"}  # fmt: skip

# faults=timeout_after_write: the write each case makes first (spec/09)
FAULT_TARGETS = {"S01": "calendar_create", "R08": "travel_book"}
FAULT_BY_TYPE = {"scheduling": "calendar_create", "booking": "travel_book", "email": "email_send",
                 "brief": "brief_save", "deck": "deck_create"}  # fmt: skip


def parse(items: list[str] | None) -> dict[str, str]:
    """`["skills=off", "model=sonnet"]` -> {"skills": "off", "model": "sonnet"}."""
    out: dict[str, str] = {}
    for item in items or []:
        for part in item.split(","):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                raise ValueError(f"Variant '{part}' should look like key=value")
            k, v = part.split("=", 1)
            if k not in KNOWN:
                raise ValueError(f"Unknown variant '{k}'. Known: {', '.join(sorted(KNOWN))}")
            out[k.strip()] = v.strip()
    return out


def normalized(v: dict[str, str]) -> dict[str, str]:
    """Drop keys set to their default, so {skills: on} and {} are the same configuration."""
    return {k: str(val) for k, val in sorted(v.items()) if DEFAULTS.get(k) != str(val)}


def label(v: dict[str, str]) -> str:
    n = normalized(v)
    return ",".join(f"{k}={val}" for k, val in n.items()) or "default"


def overlay_variants(v: dict[str, str]) -> dict[str, str]:
    return {k: val for k, val in v.items() if k in OVERLAY_KEYS}


def faults_for(case: dict[str, Any], v: dict[str, str]) -> str:
    spec = (case.get("world") or {}).get("faults") or ""
    fault = v.get("faults")
    if fault:
        tool = FAULT_TARGETS.get(case["id"]) or FAULT_BY_TYPE.get(case.get("type", ""), "calendar_create")
        if ":" in fault:
            extra = fault
        else:
            extra = f"{tool}:{fault}:1"
        spec = ";".join(p for p in (spec, extra) if p)
    return spec


def model_for(v: dict[str, str], default: str) -> str:
    return v.get("model") or default


def browser_on(v: dict[str, str]) -> bool:
    return v.get("browser") == "on"


def gate_off(v: dict[str, str]) -> bool:
    return v.get("gate") == "off"
