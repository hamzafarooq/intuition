"""Per-trial assistant overlays: each run variant changes exactly what it should."""

import json
import re
from pathlib import Path

import pytest

from ea_harness.overlay import allowed_tools, build_overlay, mcp_config


def settings(d: Path) -> dict:
    return json.loads((d / ".claude/settings.json").read_text())


def all_text(d: Path) -> str:
    return "\n".join(p.read_text() for p in [d / "CLAUDE.md", *(d / ".claude").rglob("*.md")])


@pytest.fixture
def run(tmp_path: Path) -> Path:
    return tmp_path / "run"


def test_default_overlay(run):
    d = build_overlay(run, {})
    s = settings(d)
    assert (d / ".claude/skills/scheduling/SKILL.md").exists() and len(list((d / ".claude/agents").glob("*.md"))) == 8
    assert "mcp__ea-world__email_send" in s["permissions"]["ask"]
    stop = s["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "ea_harness.stopcheck" in stop and f"--run-dir {run.resolve()}" in stop and "uv run --project .." not in stop
    assert "PreToolUse" not in s["hooks"], "browser guard only in browser mode"
    assert not (d / ".mcp.json").exists(), "the adapter passes --mcp-config with --strict-mcp-config"
    assert "<!-- selfcheck" not in all_text(d) and "<!-- browser" not in all_text(d)
    assert "check_*" in (d / "CLAUDE.md").read_text(), "self-check text kept by default"
    assert "Browser mode" not in (d / "CLAUDE.md").read_text(), "browser text stripped when browser mode is off"
    assert "mcp__browser__" not in (d / ".claude/agents/travel.md").read_text()


def test_source_untouched(run):
    src = Path(__file__).resolve().parents[1] / "assistant"
    before = (src / "CLAUDE.md").read_text()
    build_overlay(run, {"selfcheck": "off", "skills": "off"})
    assert (src / "CLAUDE.md").read_text() == before
    assert (src / ".claude/skills/scheduling").exists()


def test_skills_off(run):
    d = build_overlay(run, {"skills": "off"})
    assert not (d / ".claude/skills").exists()
    assert (d / ".claude/agents/scheduler.md").exists()


def test_selfcheck_off(run):
    d = build_overlay(run, {"selfcheck": "off"})
    s = settings(d)
    text = all_text(d)
    assert "Stop" not in s.get("hooks", {}) and "SubagentStop" not in s.get("hooks", {})
    assert "mcp__ea-world__check_event" in s["permissions"]["deny"]
    assert not (d / ".claude/agents/reviewer.md").exists()
    assert "run the matching `check_*` tool" not in (d / "CLAUDE.md").read_text()
    assert "Definition of done" not in (d / ".claude/skills/scheduling/SKILL.md").read_text()
    assert not re.search(r"mcp__ea-world__check_", "".join(p.read_text() for p in (d / ".claude/agents").glob("*.md")))
    assert "| reviewer |" not in text
    # tables still well formed after stripping
    table = re.search(r"\| Request is about \| Specialist \|\n(\|.*\|\n)+", (d / "CLAUDE.md").read_text())
    assert table and "<!--" not in table.group(0)


def test_gate_off(run):
    d = build_overlay(run, {"gate": "off"})
    assert "ask" not in settings(d)["permissions"]


def test_team_off(run):
    d = build_overlay(run, {"team": "off"})
    assert not (d / ".claude/skills/monday-brief").exists()
    for a in ("planner", "challenger", "qa"):
        assert not (d / f".claude/agents/{a}.md").exists()
    assert (d / ".claude/agents/scheduler.md").exists()


def test_subagents_off(run):
    d = build_overlay(run, {"subagents": "off"})
    assert not (d / ".claude/agents").exists()
    assert (d / ".claude/skills/scheduling/SKILL.md").exists()


def test_hooks_off(run):
    d = build_overlay(run, {"hooks": "off"})
    assert "Stop" not in settings(d).get("hooks", {})
    assert "check_*" in (d / "CLAUDE.md").read_text(), "hooks=off keeps the self-check instructions"


def test_browser_on(run):
    d = build_overlay(run, {}, browser=True)
    s = settings(d)
    guard = s["hooks"]["PreToolUse"][0]
    assert guard["matcher"] == "mcp__browser__.*" and "ea_harness.browser_guard" in guard["hooks"][0]["command"]
    assert "Browser mode" in (d / "CLAUDE.md").read_text()
    assert "mcp__browser__navigate_page" in (d / ".claude/agents/travel.md").read_text()
    assert "mcp__browser__navigate_page" in allowed_tools(True) and "mcp__browser__navigate_page" not in allowed_tools(False)
    cfg = mcp_config(run, {"EA_RUN_DIR": str(run), "PATH": "/bin", "HOME": "/tmp"}, True)
    assert cfg["mcpServers"]["browser"]["args"][1].startswith("chrome-devtools-mcp@") and "@latest" not in cfg["mcpServers"]["browser"]["args"][1]
    assert cfg["mcpServers"]["ea-world"]["timeout"] == 600000


def test_rebuild_replaces(run):
    build_overlay(run, {"skills": "off"})
    d = build_overlay(run, {})
    assert (d / ".claude/skills/scheduling").exists()
