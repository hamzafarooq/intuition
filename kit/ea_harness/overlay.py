"""Per-trial copies of `assistant/`, changed for the run's variants.

Claude Code runs with the overlay as its working directory, so the variant decides which skills,
agents, hooks and permissions it sees. Hook commands are rewritten to absolute paths so they work
from inside a run directory, and pointed at the trial's run directory.
"""

import json
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from ea_world import paths

SELFCHECK_RX = re.compile(r"[ \t]*<!-- selfcheck:start -->.*?<!-- selfcheck:end -->[ \t]*\n?", re.S)
BROWSER_RX = re.compile(r"[ \t]*<!-- browser:start -->.*?<!-- browser:end -->[ \t]*\n?", re.S)
MARKER_LINE_RX = re.compile(r"^[ \t]*<!-- (?:selfcheck|browser):(?:start|end) -->[ \t]*\n", re.M)
TEAM = ["planner", "challenger", "qa"]
CHECK_TOOLS = ["check_event", "check_email", "check_brief", "check_deck", "check_booking"]

# Browser MCP server (spec 16), pinned
CHROME_DEVTOOLS_MCP = "chrome-devtools-mcp@1.10.1"


def assistant_source() -> Path:
    return paths.kit_root() / "assistant"


def _strip(text: str, rx: re.Pattern[str]) -> str:
    return rx.sub("", text)


def _md_files(root: Path) -> list[Path]:
    return [root / "CLAUDE.md", *sorted((root / ".claude").rglob("*.md"))]


def _rewrite_text(root: Path, fn: Any) -> None:
    for p in _md_files(root):
        if p.exists():
            p.write_text(fn(p.read_text(encoding="utf-8")), encoding="utf-8")


def _drop_agent_tools(agent_file: Path, predicate: Any) -> None:
    text = agent_file.read_text(encoding="utf-8")
    m = re.search(r"^tools:\s*(.+)$", text, re.M)
    if not m:
        return
    tools = [t.strip() for t in m.group(1).split(",") if t.strip()]
    kept = [t for t in tools if not predicate(t)]
    agent_file.write_text(text[: m.start(1)] + ", ".join(kept) + text[m.end(1) :], encoding="utf-8")


def hook_command(module: str, *args: str, run_dir: Path) -> str:
    """An absolute command for a hook module, run with this interpreter from the kit root."""
    py = shlex.quote(sys.executable)
    kit = shlex.quote(str(paths.kit_root()))
    extra = " ".join(shlex.quote(a) for a in args)
    return f"cd {kit} && {py} -m {module} {extra} --run-dir {shlex.quote(str(run_dir))}".strip()


def build_overlay(run_dir: Path, variants: dict[str, str] | None = None, browser: bool = False,
                  source: Path | None = None) -> Path:
    """Copy assistant/ to <run_dir>/assistant and apply the variants. Returns the copy's path."""
    variants = dict(variants or {})
    run_dir = Path(run_dir).resolve()
    src = Path(source or assistant_source())
    dst = run_dir / "assistant"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
    claude_dir = dst / ".claude"
    skills = claude_dir / "skills"
    agents = claude_dir / "agents"
    settings_path = claude_dir / "settings.json"
    settings: dict[str, Any] = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    perms = settings.setdefault("permissions", {})
    hooks = settings.setdefault("hooks", {})

    if variants.get("skills") == "off" and skills.exists():
        shutil.rmtree(skills)
    if variants.get("team") == "off":
        shutil.rmtree(skills / "monday-brief", ignore_errors=True)
        for a in TEAM:
            (agents / f"{a}.md").unlink(missing_ok=True)
    if variants.get("subagents") == "off" and agents.exists():
        shutil.rmtree(agents)
    if variants.get("selfcheck") == "off":
        _rewrite_text(dst, lambda t: _strip(t, SELFCHECK_RX))
        (agents / "reviewer.md").unlink(missing_ok=True)
        hooks.pop("Stop", None)
        hooks.pop("SubagentStop", None)
        perms.setdefault("deny", []).extend(f"mcp__ea-world__{t}" for t in CHECK_TOOLS)
        if agents.exists():
            for f in agents.glob("*.md"):
                _drop_agent_tools(f, lambda t: t.split("__")[-1] in CHECK_TOOLS)
    if variants.get("hooks") == "off":
        hooks.pop("Stop", None)
        hooks.pop("SubagentStop", None)
    if variants.get("gate") == "off":
        perms.pop("ask", None)
    if not browser:
        _rewrite_text(dst, lambda t: _strip(t, BROWSER_RX))
        if agents.exists():
            for f in agents.glob("*.md"):
                _drop_agent_tools(f, lambda t: t.startswith("mcp__browser__"))
        hooks.pop("PreToolUse", None)
    # Leftover marker lines (from blocks that were kept) are noise for the model.
    _rewrite_text(dst, lambda t: MARKER_LINE_RX.sub("", t))

    # Absolute hook commands that know the trial's run directory.
    for event, module, args in (
        ("Stop", "ea_harness.stopcheck", ("--hook", "stop")),
        ("SubagentStop", "ea_harness.stopcheck", ("--hook", "subagent")),
        ("PreToolUse", "ea_harness.browser_guard", ()),
    ):
        for entry in hooks.get(event, []) or []:
            for h in entry.get("hooks", []):
                if h.get("type") == "command":
                    h["command"] = hook_command(module, *args, run_dir=run_dir)
    if not hooks:
        settings.pop("hooks", None)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    (dst / ".mcp.json").unlink(missing_ok=True)  # the adapter passes --mcp-config with --strict-mcp-config
    return dst


def allowed_tools(browser: bool) -> list[str]:
    """CLI --allowedTools: untrusted folders ignore permissions.allow (decisions log, 2026-10-05)."""
    tools = ["mcp__ea-world__*", "Skill", "Agent"]
    if browser:
        tools += [f"mcp__browser__{t}" for t in (
            "new_page", "navigate_page", "select_page", "list_pages", "take_snapshot", "click", "fill",
            "fill_form", "press_key", "hover", "wait_for", "take_screenshot", "close_page")]
    return tools


def mcp_config(run_dir: Path, env: dict[str, str], browser: bool) -> dict[str, Any]:
    servers: dict[str, Any] = {
        "ea-world": {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "ea_world"],
            "env": env,
            "timeout": 600000,
        }
    }
    if browser:
        servers["browser"] = {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", CHROME_DEVTOOLS_MCP, "--browserUrl", "http://127.0.0.1:9222"],
            "env": {"PATH": env.get("PATH", ""), "HOME": env.get("HOME", "")},
        }
    return {"mcpServers": servers}
