"""Build `../starter/` (what students receive) from this kit (spec/11-guide-site.md "Starter repo").

    uv run python tools/make_starter.py [--out ../starter] [--no-check]

Copies the kit, removes what students build in the lessons, applies `starter_overrides/` (the Lesson 1
tool bug, the short assistant brief, settings without the gate or hooks), and checks that the planted
find_free test fails in the starter and passes in the kit.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]

EXCLUDE = {".venv", "runs", "reports", ".ea_cache", "__pycache__", ".pytest_cache", ".ruff_cache", ".DS_Store",
           ".env", "starter_overrides", "uv.lock"}  # fmt: skip
REMOVE = [
    # Lesson 2: the four skills students write
    "assistant/.claude/skills/scheduling", "assistant/.claude/skills/inbox-triage",
    "assistant/.claude/skills/meeting-brief", "assistant/.claude/skills/meeting-deck",
    # Lesson 3: specialists, the reviewer and the email rubric
    "assistant/.claude/agents/scheduler.md", "assistant/.claude/agents/inbox.md", "assistant/.claude/agents/briefer.md",
    "assistant/.claude/agents/travel.md", "assistant/.claude/agents/reviewer.md", "evals/rubrics/email.yaml",
    # Lesson 5: the Monday-brief team
    "assistant/.claude/skills/monday-brief", "assistant/.claude/agents/planner.md", "assistant/.claude/agents/challenger.md",
    "assistant/.claude/agents/qa.md",
    # Created by runs
    "evals/labels", "evals/results", "evals/golden/_drafts",
    # Kit-only: the generator and its test
    "tools/make_starter.py", "tests/test_starter.py",
]  # fmt: skip
OVERRIDES = KIT / "starter_overrides"


def build(out: Path) -> Path:
    out = out.resolve()
    if out.exists():
        shutil.rmtree(out)

    def ignore(d: str, names: list[str]) -> list[str]:
        return [n for n in names if n in EXCLUDE]

    shutil.copytree(KIT, out, ignore=ignore)
    for rel in REMOVE:
        p = out / rel
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    (out / "assistant/.claude/agents").mkdir(parents=True, exist_ok=True)
    (out / "assistant/.claude/agents/.gitkeep").write_text("")
    for src in OVERRIDES.rglob("*"):
        if src.is_file():
            dst = out / src.relative_to(OVERRIDES)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    if (KIT / "uv.lock").exists():
        shutil.copy2(KIT / "uv.lock", out / "uv.lock")
    return out


def pytest_rc(root: Path, args: list[str]) -> int:
    return subprocess.run(["uv", "run", "--project", str(root), "pytest", "-q", "-p", "no:cacheprovider", *args],
                          cwd=root, capture_output=True, text=True).returncode


def check(out: Path) -> list[str]:
    problems = []
    if pytest_rc(KIT, ["tests/test_tools.py", "-k", "find_free"]) != 0:
        problems.append("the find_free tests fail in kit/ (they must pass)")
    if pytest_rc(out, ["tests/test_tools.py", "-k", "find_free"]) == 0:
        problems.append("the find_free tests pass in starter/ (the planted bug must make them fail)")
    for root in (KIT, out):
        r = subprocess.run(["uv", "run", "--project", str(root), "ea-eval", "list", "cases"], cwd=root, capture_output=True, text=True)
        if r.returncode != 0 or len(r.stdout.splitlines()) < 60:
            problems.append(f"ea-eval list cases doesn't work in {root.name}/")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(KIT.parent / "starter"))
    ap.add_argument("--no-check", action="store_true")
    args = ap.parse_args()
    if not (KIT / "site" / "dist" / "index.html").exists():
        print("site/dist isn't built; building it so the starter ships the guide.")
        subprocess.run([sys.executable, str(KIT / "site" / "build.py")], cwd=KIT)
    out = build(Path(args.out))
    print(f"Starter written to {out}")
    if args.no_check:
        return 0
    problems = check(out)
    for p in problems:
        print(f"✗ {p}")
    if not problems:
        print("✓ find_free fails in the starter and passes in the kit; ea-eval works in both")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
