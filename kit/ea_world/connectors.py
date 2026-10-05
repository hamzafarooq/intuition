"""Clock and connector tools."""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import core
from .core import tool
from .state import CONNECTORS


@tool("core")
def clock_now() -> dict[str, Any]:
    """The current date and time in Maya's world. Use this, not any other date you may know."""
    s = core.st()
    now = s.now()
    return {"now": now.isoformat(timespec="seconds"), "timezone": s.timezone(), "weekday": now.strftime("%A")}


@tool("core")
def connector_status() -> dict[str, Any]:
    """Which of Maya's connectors (email, calendar, contacts, docs, web, travel) are connected."""
    state = core.st().load("connectors")
    return {name: state.get(name, "connected") for name in CONNECTORS}


@tool("core")
def connector_disconnect(
    name: Annotated[
        Literal["email", "calendar", "contacts", "docs", "web", "travel"],
        Field(description="The connector to turn off for the rest of this session"),
    ],
) -> dict[str, Any]:
    """Disconnect one of Maya's connectors. Every tool in that group fails from then on. It can't be
    reconnected in this session."""
    if name not in CONNECTORS:
        raise ToolError(f"Unknown connector '{name}'. Use one of: {', '.join(CONNECTORS)}.")
    s = core.st()
    state = s.load("connectors")
    state[name] = "disconnected"
    s.save("connectors", state)
    c = core.ctx()
    c.object_id = name
    c.effect("connector_disconnected", name)
    c.signal("disconnect", name, f"The {name} connector was disconnected")
    return {"name": name, "status": "disconnected"}

