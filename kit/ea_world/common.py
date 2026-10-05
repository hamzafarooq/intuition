"""Small helpers shared by the tool modules."""

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from . import core
from .rules import resolve_person


def contacts() -> dict[str, Any]:
    return core.st().load("contacts")


def person(value: str) -> dict[str, Any]:
    p = resolve_person(value, contacts())
    if p is None:
        raise ToolError(f"Unknown contact id: {value}. Use contacts_lookup to find people.")
    return p


def maybe_person(value: str) -> dict[str, Any] | None:
    return resolve_person(value, contacts())


def next_id(prefix: str, existing: list[str]) -> str:
    n = 1
    taken = set(existing)
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


def cap(limit: int | None, default: int = 10) -> int:
    if limit is None:
        return default
    return max(1, min(int(limit), 50))


def maya_email() -> str:
    return core.st().load("persona").get("email", "maya.chen@larkspur.example")


def org_for_address(address: str) -> dict[str, Any] | None:
    domain = address.rsplit("@", 1)[-1].lower() if "@" in address else ""
    for org in contacts().get("organizations", []):
        if org.get("domain", "").lower() == domain:
            return org
    return None


def is_internal_address(address: str) -> bool:
    org = org_for_address(address)
    return bool(org and org.get("relationship") == "internal")
