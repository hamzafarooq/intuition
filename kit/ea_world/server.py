"""The `ea-world` MCP server (stdio). Serves every tool over the run directory's copy of the world."""

import functools
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from . import approvals, core, state
from . import calendar, connectors, contacts, docs, email, outputs, travel, verifiers, web  # noqa: F401  (register tools)

log = logging.getLogger("ea_world")

INSTRUCTIONS = (
    "Tools for Maya Chen's mock workspace: calendar, email, contacts, documents, web, travel, outputs and "
    "verifiers. Use clock_now for the current time. Write tools change the world; after any write, run the "
    "matching check_* tool and read the result before telling Maya it's done."
)


def tool_names() -> list[str]:
    return sorted(core.REGISTRY)


def _plain(value: Any) -> Any:
    """Validated arguments as plain JSON data (nested Pydantic models, e.g. deck slides, become dicts)."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _wrap(name: str, fn: Any) -> Any:
    @functools.wraps(fn)
    def wrapper(**kwargs: Any) -> Any:
        return core.call(name, {k: _plain(v) for k, v in kwargs.items()})

    return wrapper


def build_server(run_dir: Path) -> MCPServer:
    server = MCPServer(name="ea-world", instructions=INSTRUCTIONS)
    for name, spec in core.REGISTRY.items():
        server.tool()(_wrap(name, spec.fn))

    @server.tool(structured_output=False)
    def approval_prompt(
        tool_name: Annotated[str, Field(description="The tool Claude Code wants to run")],
        input: Annotated[dict[str, Any], Field(description="Its input")],
        tool_use_id: Annotated[str | None, Field(description="The tool-use id")] = None,
    ) -> str:
        """Permission prompt for Claude Code's --permission-prompt-tool. Not for the assistant."""
        try:
            return approvals.handle(tool_name, input, tool_use_id, run_dir)
        except Exception as exc:  # an approval error must deny, never crash
            log.exception("approval_prompt failed")
            return approvals._deny(f"Approval failed: {exc}")

    return server


def prepare_run_dir() -> Path:
    """Resolve, initialise and register the run directory for this server process."""
    env_dir = os.environ.get("EA_RUN_DIR")
    if env_dir:
        run_dir = Path(env_dir).resolve()
    else:
        current = state.read_current()
        if current and current.name.startswith("interactive-") and (current / "state").exists():
            run_dir = current
        else:
            run_dir = state.new_run_dir().resolve()
    state.init_run_dir(run_dir, os.environ.get("EA_WORLD_VARIANT"))
    if os.environ.get("EA_WRITE_CURRENT", "1") != "0":  # eval trials leave runs/CURRENT alone
        state.write_current(run_dir)
    core.configure(run_dir)
    state.update_meta(
        run_dir,
        server={
            "mode": os.environ.get("EA_MODE", "mock"),
            "variants": state.parse_variants(os.environ.get("EA_WORLD_VARIANT")),
            "faults": os.environ.get("EA_FAULTS", ""),
            "seed": os.environ.get("EA_SEED", "0"),
            "approval_mode": os.environ.get("EA_APPROVAL_MODE", "script"),
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
    )
    return run_dir


def main() -> None:
    logging.basicConfig(stream=sys.stderr, level=os.environ.get("EA_LOG_LEVEL", "WARNING"),
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    run_dir = prepare_run_dir()
    log.info("ea-world serving %s", run_dir)
    build_server(run_dir).run()
