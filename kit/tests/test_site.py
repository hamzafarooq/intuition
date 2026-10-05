"""The guide site (spec/11-guide-site.md): it builds, every page exists, lessons have the parts the
spec asks for, pages need no network, and every command the guide tells students to run is real."""

from __future__ import annotations

import importlib.util
import json
import re
import shlex
from pathlib import Path

import pytest
import yaml

KIT = Path(__file__).resolve().parents[1]
SITE = KIT / "site"

SPEC_PAGES = [
    "index",
    "setup",
    "lesson-1",
    "lesson-2",
    "lesson-3",
    "lesson-4",
    "lesson-5",
    "wrap",
    "rubrics",
    "cheatsheet",
    "glossary",
]
LESSONS = ["lesson-1", "lesson-2", "lesson-3", "lesson-4", "lesson-5"]
COURSE = ["setup", *LESSONS, "wrap"]
ALLOWED_FIRST = {"uv", "make", "cd", "claude", "git", "cp"}


def _load_build():
    spec = importlib.util.spec_from_file_location("site_build", SITE / "build.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


build_mod = _load_build()


@pytest.fixture(scope="module")
def dist(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("site-dist")
    build_mod.build(out)
    return out


def content(slug: str) -> dict:
    return yaml.safe_load((SITE / "content" / f"{slug}.yaml").read_text(encoding="utf-8"))


def html(dist: Path, slug: str) -> str:
    return (dist / f"{slug}.html").read_text(encoding="utf-8")


def all_run_commands() -> list[tuple[str, str]]:
    """(where, command) for every command the guide shows students: step `run`s and cheatsheet layers."""
    out = []
    for slug in COURSE:
        for s in content(slug).get("steps") or []:
            run = s.get("run") or []
            for cmd in run if isinstance(run, list) else [run]:
                out.append((f"{slug} {s['id']}", str(cmd)))
    for sec in content("cheatsheet").get("sections") or []:
        for layer in sec.get("layers") or []:
            run = layer.get("run") or []
            for cmd in run if isinstance(run, list) else [run]:
                out.append((f"cheatsheet {layer['id']}", str(cmd)))
    return out


# ---------------------------------------------------------------- build and pages


def test_build_writes_every_spec_page(dist: Path) -> None:
    for slug in SPEC_PAGES:
        page = dist / f"{slug}.html"
        assert page.exists(), f"missing {page.name}"
        text = page.read_text(encoding="utf-8")
        assert text.startswith("<!doctype html>")
        assert "<title>" in text and "</html>" in text


def test_content_validates() -> None:
    pages = {slug: build_mod.load_page(slug) for slug in build_mod.PAGES}
    assert build_mod.validate(pages) == []
    assert set(build_mod.PAGES) == set(SPEC_PAGES)


def test_build_refuses_bad_content(tmp_path: Path, monkeypatch) -> None:
    bad = content("lesson-1")
    bad["check"][0]["answer"] = 7
    bad["steps"][0]["run"] = ["open reports/x.html"]
    original = build_mod.load_page

    def fake(slug: str) -> dict:
        page = original(slug)
        if slug == "lesson-1":
            page.update(bad)
        return page

    monkeypatch.setattr(build_mod, "load_page", fake)
    with pytest.raises(build_mod.ContentError) as e:
        build_mod.build(tmp_path)
    assert "answer must be an index" in str(e.value)
    assert "run command must start with" in str(e.value)


# ---------------------------------------------------------------- lessons


@pytest.mark.parametrize("slug", LESSONS)
def test_lesson_steps_have_why_prompt_expect(slug: str) -> None:
    page = content(slug)
    assert page.get("number") == int(slug.split("-")[1])
    assert page.get("title") and page.get("goal") and page.get("duration") and page.get("by_the_end")
    steps = page.get("steps") or []
    assert len(steps) >= 3
    for s in steps:
        assert s.get("id") and s.get("title"), s
        assert str(s.get("why", "")).strip(), f"{slug} {s['id']}: no why"
        assert str(s.get("prompt", "")).strip(), f"{slug} {s['id']}: no prompt"
        assert s.get("expect") and isinstance(s["expect"], list), f"{slug} {s['id']}: expect must be a list"
        # Prompts name exact files (spec 11: "They name the exact files")
        assert re.search(r"[\w.-]+/[\w./<>*-]+|\w+\.(?:ya?ml|md|py|json)\b", s["prompt"]), (
            f"{slug} {s['id']}: prompt names no file"
        )


@pytest.mark.parametrize("slug", COURSE)
def test_three_check_questions_with_valid_answers(slug: str) -> None:
    check = content(slug).get("check") or []
    assert len(check) == 3, f"{slug}: needs exactly 3 check questions"
    for q in check:
        assert q["q"] and q["why"]
        assert (
            isinstance(q["answer"], int) and 0 <= q["answer"] < len(q["options"]) and len(q["options"]) >= 2
        )


@pytest.mark.parametrize("slug", LESSONS)
def test_every_lesson_has_a_replay(slug: str, dist: Path) -> None:
    replays = [
        r
        for s in content(slug)["steps"]
        for r in ([s["replay"]] if isinstance(s.get("replay"), str) else s.get("replay") or [])
    ]
    assert replays, f"{slug}: no replay (PRD F4)"
    for r in replays:
        assert (SITE / r).exists(), r
    text = html(dist, slug)
    assert 'class="replay"' in text and 'class="timeline"' in text and "tl-tools" in text


def test_replays_are_valid_traces_without_local_paths() -> None:
    meta = yaml.safe_load((SITE / "replays" / "index.yaml").read_text(encoding="utf-8"))
    files = sorted((SITE / "replays").glob("*.jsonl"))
    assert files
    for f in files:
        assert f.name in meta, f"{f.name} has no entry in replays/index.yaml"
        assert meta[f.name].get("source") in ("recorded", "example")
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"/(?:Users|home|private/var|var/folders)/", text), (
            f"{f.name} contains a local path"
        )
        events = [json.loads(line) for line in text.splitlines() if line.strip()]
        assert events and all("type" in e and "seq" in e for e in events)
        calls = {e["call_id"] for e in events if e["type"] == "tool_call"}
        results = {e["call_id"] for e in events if e["type"] == "tool_result"}
        assert calls <= results, f"{f.name}: a tool_call without its tool_result"
    assert any(m.get("source") == "recorded" for m in meta.values()), (
        "at least one replay is a real recorded run"
    )


def test_lesson_page_anatomy(dist: Path) -> None:
    for slug in COURSE:
        text = html(dist, slug)
        n_steps = len(content(slug)["steps"])
        assert text.count('data-tick="') == n_steps
        assert text.count("data-quiz data-answer=") == 3
        assert 'id="built"' in text and "What you have built so far" in text
        assert "Finish the earlier steps first, or show anyway" in text
        assert 'class="pager"' in text
        assert text.count('class="copy"') >= n_steps  # every prompt has a copy button
    assert "Lesson 3 of 5" in html(dist, "lesson-3")
    assert 'href="lesson-2.html"' in html(dist, "lesson-3") and 'href="lesson-4.html"' in html(
        dist, "lesson-3"
    )


def test_tracker_lists_every_build(dist: Path) -> None:
    text = html(dist, "lesson-2")
    for slug in COURSE:
        for s in content(slug)["steps"]:
            for _b in s.get("builds") or []:
                assert f'data-built-by="{s["id"]}"' in text


# ---------------------------------------------------------------- no network, works from file://


EXTERNAL_RESOURCE = re.compile(
    r"""<(?:script|img|iframe|source|video|audio|embed|object)\b[^>]*\b(?:src|data|srcset)\s*=\s*["']?\s*(?:https?:)?//"""
    r"""|<link\b[^>]*\bhref\s*=\s*["']?\s*(?:https?:)?//"""
    r"""|@import\s+(?:url\()?["']?\s*(?:https?:)?//"""
    r"""|url\(\s*["']?\s*(?:https?:)?//""",
    re.I,
)


@pytest.mark.parametrize("slug", SPEC_PAGES)
def test_no_network_resources(slug: str, dist: Path) -> None:
    text = html(dist, slug)
    assert not EXTERNAL_RESOURCE.search(text), EXTERNAL_RESOURCE.search(text).group(0)
    assert '<link rel="stylesheet"' not in text and "<script src" not in text
    # CSS and JS are inlined: the app's tokens and the site's script
    assert "--paper:" in text and "--accent:" in text and "prefers-color-scheme: dark" in text
    assert "intuition-guide:v1:" in text and "localStorage" in text
    assert 'href="http://localhost:8765"' in text  # the header link to the app


def test_storage_is_guarded() -> None:
    js = (SITE / "static" / "site.js").read_text(encoding="utf-8")
    assert js.count("try {") >= 3 and "catch (e)" in js
    assert '"intuition-guide:v1:"' in js
    assert "navigator.clipboard" in js and "execCommand" in js  # copy with a textarea fallback


# ---------------------------------------------------------------- commands are real


def _ea_eval_parser():
    from ea_evals.cli import build_parser

    return build_parser()


def _subcommands() -> set[str]:
    p = _ea_eval_parser()
    sub = next(a for a in p._actions if a.__class__.__name__ == "_SubParsersAction")
    return set(sub.choices)


def _make_targets() -> set[str]:
    text = (KIT / "Makefile").read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Za-z][\w-]*):", text, re.M))


@pytest.mark.parametrize(("where", "command"), all_run_commands())
def test_run_commands_are_real(where: str, command: str) -> None:
    subs = _subcommands()
    targets = _make_targets()
    for segment in [s.strip() for s in command.split("&&")]:
        argv = shlex.split(segment)
        assert argv and argv[0] in ALLOWED_FIRST, f"{where}: '{segment}' starts with {argv[:1]}"
        if argv[0] == "make":
            for t in argv[1:]:
                if "=" not in t:
                    assert t in targets, f"{where}: no make target '{t}'"
        if argv[:3] == ["uv", "run", "ea-eval"]:
            assert len(argv) > 3 and argv[3] in subs, f"{where}: unknown ea-eval subcommand in '{segment}'"
            args = [re.sub(r"<[^>]+>", "x", a) for a in argv[3:]]
            if args[0] in ("promote",):
                args = [a if a != "x" or i != 5 else "1" for i, a in enumerate(args)]
            try:
                _ea_eval_parser().parse_args(args)
            except SystemExit as e:  # argparse rejects unknown flags or bad choices
                pytest.fail(f"{where}: ea-eval rejects '{segment}' (exit {e.code})")
        if argv[:3] == ["uv", "run", "pytest"]:
            for a in argv[3:]:
                if a.startswith("tests/"):
                    assert (KIT / a.split("::")[0]).exists(), f"{where}: no test file {a}"
        if argv[0] == "cd":
            assert (KIT / argv[1]).is_dir(), f"{where}: no folder {argv[1]}"


def test_slices_named_in_the_guide_exist() -> None:
    from ea_evals.cases import load_slices

    slices = set(load_slices())
    for where, command in all_run_commands():
        for name in re.findall(r"--slice\s+([\w-]+)", command):
            assert name in slices, f"{where}: unknown slice {name}"
    for slug in COURSE:
        for s in content(slug)["steps"]:
            for name in re.findall(r"--slice\s+([\w-]+)", s["prompt"]):
                assert name in slices, f"{slug} {s['id']}: prompt names unknown slice {name}"


def test_files_named_in_prompts_exist_or_are_built() -> None:
    """Paths a prompt asks Claude Code to read must exist in the kit, unless an earlier step builds them."""
    built: set[str] = set()
    for slug in COURSE:
        for s in content(slug)["steps"]:
            built |= {b.split(" (")[0] for b in s.get("builds") or []}
    readable = re.compile(r"\b((?:assistant|evals|ea_world|ea_harness|ea_evals|world|tests|docs)/[\w./-]*\w)")
    for slug in COURSE:
        for s in content(slug)["steps"]:
            for path in readable.findall(s["prompt"]):
                if "<" in path or path.startswith(("evals/results", "evals/labels", "evals/golden/_drafts")):
                    continue
                p = KIT / path
                assert p.exists() or path in built or any(b.startswith(path) for b in built), (
                    f"{slug} {s['id']}: {path} doesn't exist"
                )


def test_index_tells_the_instinct_story_as_reported(dist: Path) -> None:
    text = html(dist, "index")
    assert text.count("eported") >= 4
    for phrase in ("planted in an email", "without asking", "after being disconnected"):
        assert phrase in text
    assert 'href="setup.html"' in text and "Start with setup" in text
