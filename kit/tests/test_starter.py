"""The starter repo: lesson-built files removed, overrides applied, the planted Lesson 1 bug in place."""

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("make_starter", KIT / "tools" / "make_starter.py")
make_starter = importlib.util.module_from_spec(spec)
sys.modules["make_starter"] = make_starter
spec.loader.exec_module(make_starter)  # type: ignore[union-attr]


@pytest.fixture(scope="module")
def starter(tmp_path_factory) -> Path:
    return make_starter.build(tmp_path_factory.mktemp("s") / "starter")


def test_removed_files_absent(starter: Path) -> None:
    for rel in make_starter.REMOVE:
        assert not (starter / rel).exists(), rel
    skills = sorted(p.name for p in (starter / "assistant/.claude/skills").iterdir())
    assert skills == ["email-reply", "travel-booking", "web-research"]
    assert not list((starter / "assistant/.claude/agents").glob("*.md"))


def test_overrides_applied(starter: Path) -> None:
    settings = json.loads((starter / "assistant/.claude/settings.json").read_text())
    assert "ask" not in settings["permissions"] and "hooks" not in settings
    assert settings["permissions"]["deny"] and settings["permissions"]["allow"]
    md = (starter / "assistant/CLAUDE.md").read_text()
    assert "## House rules" in md and "## Asking for approval" in md and "STATUS:" in md
    assert "Routing table" not in md and "selfcheck" not in md and "reviewer" not in md and "specialist" not in md
    cal = (starter / "ea_world/calendar.py").read_text()
    assert '"""Finds free time."""' in cal and "timezone(timedelta(hours=-6))" in cal


def test_starter_calendar_differs_only_in_the_planted_spots() -> None:
    """Catches drift: the override must be the kit's calendar.py plus the bug and the vague description."""
    kit = (KIT / "ea_world/calendar.py").read_text()
    over = (KIT / "starter_overrides/ea_world/calendar.py").read_text()

    def strip(src: str) -> str:
        src = re.sub(r"def _hours_ok\(.*?\n\n\n", "", src, flags=re.S)
        src = re.sub(r'(def calendar_find_free\(.*?-> dict\[str, Any\]:\n)    """.*?"""', r"\1", src, flags=re.S)
        src = re.sub(r"^(from|import) .*\n|^MAYA_TZ = .*\n", "", src, flags=re.M)
        return re.sub(r"\n\s*\n+", "\n\n", src).strip()

    assert strip(kit) == strip(over)


def test_kit_skills_and_agents_present() -> None:
    assert (KIT / "assistant/.claude/skills/scheduling/SKILL.md").exists()
    assert len(list((KIT / "assistant/.claude/agents").glob("*.md"))) == 8


def test_planted_find_free_test_fails_in_starter(starter: Path) -> None:
    import subprocess

    # Run the kit's find_free tests against the starter's calendar.py by putting the starter first on the path.
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(KIT / "tests/test_tools.py"), "-k", "find_free"],
                       cwd=starter, capture_output=True, text=True, env={**__import__("os").environ, "PYTHONPATH": str(starter), "EA_KIT_ROOT": str(starter)})
    assert r.returncode != 0, "the planted bug must make the find_free tests fail"
