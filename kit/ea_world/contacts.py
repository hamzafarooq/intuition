"""Contacts lookup."""

import re
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import core
from .core import tool


def _view(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c["id"],
        "name": c["name"],
        "email": c["email"],
        "company": c.get("company", ""),
        "role": c.get("role", ""),
        "timezone": c["timezone"],
        "working_hours": c.get("working_hours"),
        "internal": bool(c.get("internal")),
        "relationship": c.get("relationship", "internal" if c.get("internal") else "external"),
    }


@tool("contacts")
def contacts_lookup(
    query: Annotated[str, Field(description="A name, part of a name, a company or an email address")],
) -> dict[str, Any]:
    """Look up people in Maya's contacts. Returns every match, with company, role, time zone, working
    hours and whether they work at Larkspur. A first name can match more than one person."""
    q = (query or "").strip().lower()
    if not q:
        raise ToolError("Give a name, company or email address to look up.")
    tokens = [t for t in re.split(r"[\s,]+", q) if t]
    matches = []
    for c in core.st().load("contacts").get("contacts", []):
        hay = " ".join([c["id"], c["name"], c["email"], c.get("company", ""), c.get("role", "")]).lower()
        if q == c["email"].lower() or q == c["id"] or all(t in hay for t in tokens):
            matches.append(_view(c))
    core.ctx().object_id = matches[0]["id"] if len(matches) == 1 else None
    return {"query": query, "matches": matches, "count": len(matches)}
