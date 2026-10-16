"""Run variants: which harness and assistant configuration a trial uses (spec/09 "Variants", spec/05)."""

from typing import Any

OVERLAY_KEYS = {"skills", "selfcheck", "gate", "team", "hooks", "subagents"}
KNOWN = OVERLAY_KEYS | {"model", "effort", "idempotency", "faults", "browser"}
DEFAULTS = {"skills": "on", "selfcheck": "on", "gate": "on", "team": "on", "hooks": "on", "subagents": "on",
            "model": "opus", "idempotency": "off", "browser": "off"}  # fmt: skip

# The real-site search demo is instructor-only and app-only (ea_harness/real_sites.py): evals refuse it, so
# they stay deterministic and never touch a real website.
REAL_SITES_REFUSED = ("browser=real is the instructor-only real-site search demo, and evals refuse it: real sites "
                      "change from minute to minute, so results couldn't be compared or graded. Use browser=on "
                      "(the mock booking sites), or run the demo on the mock phone with `make demo-real`.")



class VariantError(ValueError):
    """A variant the user typed that can't run; `ea-eval` prints it without a traceback."""


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
                raise VariantError(f"Variant '{part}' should look like key=value")
            k, v = part.split("=", 1)
            if k not in KNOWN:
                raise VariantError(f"Unknown variant '{k}'. Known: {', '.join(sorted(KNOWN))}")
            out[k.strip()] = v.strip()
    refuse_real_sites(out)
    return out


def refuse_real_sites(v: dict[str, Any]) -> None:
    """Raise if a variant asks for the real-site demo; browser takes only on or off in evals."""
    value = str(v.get("browser", "off")).strip().lower()
    if value in ("real", "real-sites", "real_sites", "live"):
        raise VariantError(REAL_SITES_REFUSED)
    if value not in ("on", "off"):
        raise VariantError(f"browser={value} isn't a variant: use browser=on or browser=off.")


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
