"""Build the workshop guide: site/content/*.yaml -> site/dist/*.html (spec/11-guide-site.md).

    uv run python site/build.py              # writes site/dist/
    uv run python site/build.py --out /tmp/x # somewhere else (the tests do this)

Every page is one self-contained HTML file: CSS (the app's tokens.css plus site.css) and JS are
inlined, replays are rendered from site/replays/*.jsonl at build time, and nothing is fetched from
the network, so the pages open straight from disk (file://).
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path
from textwrap import dedent
from typing import Any

import markdown as md
import yaml
from jinja2 import Environment, FileSystemLoader, Undefined
from markupsafe import Markup, escape

SITE = Path(__file__).resolve().parent
KIT = SITE.parent
CONTENT = SITE / "content"
TEMPLATES = SITE / "templates"
STATIC = SITE / "static"
REPLAYS = SITE / "replays"
TOKENS = KIT / "ea_app" / "static" / "tokens.css"
DIST = SITE / "dist"

APP_URL = "http://localhost:8765"
STORAGE_PREFIX = "intuition-guide:v1:"

# The course runs in this order; prev/next links and the soft lock follow it.
COURSE = ["setup", "lesson-1", "lesson-2", "lesson-3", "lesson-4", "lesson-5", "wrap"]
REFERENCE = ["rubrics", "cheatsheet", "glossary"]
PAGES = ["index", *COURSE, *REFERENCE]

# First word of every `run` command (spec 11: commands students paste into a terminal).
RUN_FIRST_TOKENS = {"uv", "make", "cd", "claude", "git", "cp"}

NAV = [
    ("setup", "Setup"),
    ("lesson-1", "1 Tools"),
    ("lesson-2", "2 Skills"),
    ("lesson-3", "3 Sub-agents"),
    ("lesson-4", "4 Human gate"),
    ("lesson-5", "5 Team and harness"),
    ("wrap", "Wrap"),
]
NAV_REF = [("rubrics", "Rubrics"), ("cheatsheet", "Cheatsheet"), ("glossary", "Glossary")]


class ContentError(ValueError):
    pass


# ---------------------------------------------------------------- markdown


_MD = md.Markdown(extensions=["tables", "fenced_code", "sane_lists"], output_format="html")
_EXTERNAL_A = re.compile(r'<a href="(https?://[^"]+)"')


def render_md(text: str | None) -> Markup:
    if not text:
        return Markup("")
    _MD.reset()
    out = _MD.convert(dedent(str(text)).strip())
    out = _EXTERNAL_A.sub(r'<a href="\1" rel="noopener" target="_blank"', out)
    return Markup(out)


def render_inline(text: str | None) -> Markup:
    """Markdown for a short string: no wrapping <p>."""
    out = str(render_md(text))
    if out.startswith("<p>") and out.endswith("</p>") and out.count("<p>") == 1:
        out = out[3:-4]
    return Markup(out)


# ---------------------------------------------------------------- content


def load_page(slug: str) -> dict[str, Any]:
    path = CONTENT / f"{slug}.yaml"
    if not path.exists():
        raise ContentError(f"Missing content file: {path.relative_to(KIT)}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data["slug"] = slug
    data.setdefault("template", "lesson" if slug in COURSE else ("index" if slug == "index" else "page"))
    return data


def as_list(v: Any) -> list[Any]:
    if v is None or v == "":
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def run_commands(step: dict[str, Any]) -> list[str]:
    return [str(c).strip() for c in as_list(step.get("run")) if str(c).strip()]


def first_token(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return parts[0] if parts else ""


def validate(pages: dict[str, dict[str, Any]]) -> list[str]:
    """Problems with the content. The build refuses to write pages while any remain."""
    problems: list[str] = []
    seen_ids: dict[str, str] = {}
    meta = replay_meta()
    for slug, page in pages.items():
        if not page.get("title"):
            problems.append(f"{slug}: no title")
        if page["template"] != "lesson":
            continue
        steps = page.get("steps") or []
        if not steps:
            problems.append(f"{slug}: no steps")
        for s in steps:
            sid = str(s.get("id", ""))
            where = f"{slug} step {sid or '?'}"
            if not sid:
                problems.append(f"{where}: no id")
            elif sid in seen_ids:
                problems.append(f"{where}: id also used on {seen_ids[sid]}")
            seen_ids[sid] = slug
            for key in ("title", "why", "prompt", "expect"):
                if not s.get(key):
                    problems.append(f"{where}: missing {key}")
            for cmd in run_commands(s):
                if first_token(cmd) not in RUN_FIRST_TOKENS:
                    problems.append(f"{where}: run command must start with one of {sorted(RUN_FIRST_TOKENS)}: {cmd}")
            for r in as_list(s.get("replay")):
                p = SITE / str(r)
                if not p.exists():
                    problems.append(f"{where}: replay not found: {r}")
                elif p.name not in meta:
                    problems.append(f"{where}: replay {p.name} has no entry in replays/index.yaml")
        for i, q in enumerate(page.get("check") or [], 1):
            opts = q.get("options") or []
            ans = q.get("answer")
            if not q.get("q") or len(opts) < 2:
                problems.append(f"{slug} check {i}: needs q and at least two options")
            if not isinstance(ans, int) or not 0 <= ans < len(opts):
                problems.append(f"{slug} check {i}: answer must be an index into options")
            if not q.get("why"):
                problems.append(f"{slug} check {i}: missing why")
    return problems


# ---------------------------------------------------------------- replays


def replay_meta() -> dict[str, dict[str, Any]]:
    p = REPLAYS / "index.yaml"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_trace(path: Path) -> list[dict[str, Any]]:
    events = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except ValueError as e:
            raise ContentError(f"{path.relative_to(KIT)} line {n}: not JSON ({e})") from e
    return events


def _maybe_json(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _pretty(v: Any, limit: int = 1400) -> str:
    v = _maybe_json(v)
    text = v if isinstance(v, str) else json.dumps(v, indent=2, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit].rstrip() + "\n… (shortened)"


def _short(v: Any, limit: int = 60) -> str:
    text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _args_line(inp: dict[str, Any] | None, limit: int = 150) -> str:
    if not inp:
        return "no arguments"
    out = ", ".join(f"{k}={_short(v, 48)}" for k, v in inp.items() if k != "idempotency_key")
    if "idempotency_key" in (inp or {}):
        out += (", " if out else "") + "idempotency_key=…"
    return out if len(out) <= limit else out[: limit - 1] + "…"


def _bold(text: str) -> Markup:
    """Assistant text: escaped, with **bold** kept and line breaks preserved by CSS."""
    safe = str(escape(text or ""))
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", safe)
    return Markup(safe)


WHO = {"maya": "Maya", "sim_maya": "Simulated Maya", "harness": "Harness note", "builder": "You"}


def replay_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Trace events -> timeline rows: {cat, label, agent, sub, turn, html, detail, badge, tone}."""
    rows: list[dict[str, Any]] = []
    last_turn = None
    for e in events:
        t = e.get("type")
        agent = e.get("agent") or "main"
        turn = e.get("turn")
        if turn is not None and turn != last_turn:
            if last_turn is not None:
                rows.append({"divider": f"Turn {turn}"})
            last_turn = turn
        row: dict[str, Any] = {"agent": agent, "sub": agent != "main", "detail": "", "badge": "", "badge_text": "", "tone": ""}
        if t == "user":
            row.update(cat="messages", label=WHO.get(e.get("source", "maya"), "Maya"), html=_bold(e.get("text", "")), bubble="me")
        elif t == "assistant":
            row.update(cat="messages", label="Assistant" if agent == "main" else agent.capitalize(), html=_bold(e.get("text", "")), bubble="them")
        elif t == "thinking_summary":
            row.update(cat="system", label="Thinking", html=_bold(e.get("text", "").strip()), tone="quiet")
        elif t == "tool_call":
            row.update(cat="tools", label="Tool call", html=Markup(f"<code>{escape(e.get('tool', ''))}</code> "
                       f"<span class=\"args\">{escape(_args_line(e.get('input')))}</span>"),
                       detail=_pretty(e.get("input") or {}) if e.get("input") else "")
        elif t == "tool_result":
            if e.get("tool") == "Agent":
                continue  # the handoff_result row says the same, more readably
            ok = bool(e.get("ok"))
            out = e.get("output") if ok else (e.get("error") or e.get("output"))
            row.update(cat="tools" if ok else "error", label="Result",
                       html=Markup(f"<code>{escape(e.get('tool', ''))}</code> <span class=\"args\">{escape(_short(out, 120))}</span>"),
                       detail=_pretty(out), tone="result", badge="ok" if ok else "error", badge_text="ok" if ok else "failed")
        elif t == "skill_loaded":
            row.update(cat="skills", label="Skill", html=Markup(f"Loaded the <strong>{escape(e.get('skill', ''))}</strong> skill"))
        elif t == "delegate":
            row.update(cat="agents", label="Hand-off", html=Markup(f"Handed the task to <strong>{escape(e.get('to', ''))}</strong>"),
                       detail=_pretty(e.get("task", ""), 2000))
        elif t == "handoff_result":
            row.update(cat="agents", label="Hand-back", html=Markup(f"<strong>{escape(e.get('to', ''))}</strong> reported back: "
                       f"<span class=\"args\">{escape(_short(e.get('text', ''), 110))}</span>"), detail=_pretty(e.get("text", ""), 2000))
        elif t == "approval_request":
            summary = e.get("summary") or f"{e.get('tool', '')} {_args_line(e.get('input'), 100)}"
            row.update(cat="approvals", label="Approval asked", html=Markup(f"<code>{escape(e.get('tool', ''))}</code> "
                       f"<span class=\"args\">{escape(_short(summary, 130))}</span>"))
        elif t == "approval_response":
            allow = e.get("decision") == "allow"
            who = {"script": "the eval script", "human": "Maya", "app": "Maya in the app"}.get(e.get("by", ""), e.get("by", ""))
            reason = f": {e['reason']}" if e.get("reason") else ""
            row.update(cat="approvals", label="Approved" if allow else "Denied",
                       html=Markup(f"{'Allowed' if allow else 'Denied'} by {escape(who)}{escape(reason)}"))
        elif t == "check":
            probs = e.get("problems") or []
            ok = bool(e.get("ok"))
            text = "no problems" if ok and not probs else f"{len(probs)} problem{'s' if len(probs) != 1 else ''}"
            row.update(cat="checks", label="Check", html=Markup(f"<code>{escape(e.get('verifier', ''))}</code> on "
                       f"{escape(e.get('object_id', ''))}: {escape(text)}"), detail=_pretty(probs) if probs else "", badge="ok" if ok else "error",
                       badge_text="ok" if ok else "problems")
        elif t == "signal":
            row.update(cat="signals", label="Signal", html=Markup(f"<strong>{escape(e.get('kind', ''))}</strong>: {escape(e.get('detail', ''))}"))
        elif t == "stop_check_block":
            row.update(cat="system", label="Stop check", html=Markup(f"Sent back: {escape(e.get('reason', ''))}"), badge="warn", badge_text="blocked")
        elif t == "status":
            st = e.get("status", "")
            reason = f" — {e['reason']}" if e.get("reason") else ""
            row.update(cat="system", label="Status", html=Markup(f"<span class=\"status status-{escape(st)}\">STATUS: {escape(st)}</span>{escape(reason)}"))
        elif t == "usage":
            bits = []
            if e.get("cost_usd") is not None:
                bits.append(f"${float(e['cost_usd']):.2f} API-equivalent")
            if e.get("seconds") is not None:
                bits.append(f"{float(e['seconds']):.0f} s")
            if e.get("num_turns"):
                bits.append(f"{e['num_turns']} model turns")
            row.update(cat="system", label="Usage", html=Markup(escape(" · ".join(bits) or "usage recorded")), tone="quiet")
        elif t in ("error", "budget_exceeded"):
            msg = e.get("message") or e.get("which") or ""
            row.update(cat="error", label="Error", html=Markup(escape(f"{e.get('where', '')} {msg}".strip())))
        elif t == "turn_end":
            continue
        else:
            row.update(cat="system", label=str(t), html=Markup(escape(_short(e, 120))))
        rows.append(row)
    return rows


def replay_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    calls = [e for e in events if e.get("type") == "tool_call"]
    usage = [e for e in events if e.get("type") == "usage"]
    statuses = [e.get("status") for e in events if e.get("type") == "status"]
    return {
        "tool_calls": len(calls),
        "agents": sorted({e.get("agent") for e in events if e.get("agent") and e.get("agent") != "main"}),
        "cost": sum(float(u.get("cost_usd") or 0) for u in usage),
        "seconds": sum(float(u.get("seconds") or 0) for u in usage),
        "status": statuses[-1] if statuses else "",
        "turns": len({e.get("turn") for e in events if e.get("turn") is not None}),
    }


# ---------------------------------------------------------------- rendering


def build_env() -> Environment:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True, undefined=Undefined,
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["md"] = render_md
    env.filters["mdi"] = render_inline
    env.filters["as_list"] = as_list
    env.filters["wbr"] = lambda text: Markup(str(escape(text)).replace("/", "/<wbr>"))
    env.filters["anchor"] = lambda sid: "step-" + re.sub(r"[^A-Za-z0-9]+", "-", str(sid)).strip("-").lower()
    return env


def course_manifest(pages: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for slug in COURSE:
        p = pages[slug]
        out.append({"slug": slug, "title": p["title"], "label": page_label(p),
                    "steps": [{"id": str(s["id"]), "title": s["title"], "builds": as_list(s.get("builds"))} for s in p.get("steps") or []]})
    return out


def page_label(page: dict[str, Any]) -> str:
    if page["slug"].startswith("lesson-"):
        return f"Lesson {page.get('number')} of 5"
    return {"setup": "Setup", "wrap": "Wrap-up"}.get(page["slug"], page.get("title", ""))


def build(out: Path | None = None) -> list[Path]:
    out = Path(out or DIST)
    pages = {slug: load_page(slug) for slug in PAGES}
    problems = validate(pages)
    if problems:
        raise ContentError("Content problems:\n  " + "\n  ".join(problems))
    if not TOKENS.exists():
        raise ContentError(f"Design tokens not found at {TOKENS}")
    css = TOKENS.read_text(encoding="utf-8") + "\n" + (STATIC / "site.css").read_text(encoding="utf-8")
    js = (STATIC / "site.js").read_text(encoding="utf-8")
    meta = replay_meta()
    env = build_env()
    manifest = course_manifest(pages)

    def replay(path: str) -> dict[str, Any]:
        p = SITE / path
        events = load_trace(p)
        m = meta.get(p.name, {})
        return {"id": p.stem, "file": p.name, "title": m.get("title", p.stem), "caption": m.get("caption", ""),
                "source": m.get("source", "example"), "origin": m.get("origin", ""), "rows": replay_rows(events),
                "stats": replay_stats(events)}

    out.mkdir(parents=True, exist_ok=True)
    written = []
    for slug in PAGES:
        page = pages[slug]
        idx = COURSE.index(slug) if slug in COURSE else None
        prev_slug = COURSE[idx - 1] if idx else ("index" if idx == 0 else None)
        next_slug = COURSE[idx + 1] if idx is not None and idx + 1 < len(COURSE) else ("setup" if slug == "index" else None)
        for s in page.get("steps") or []:
            s["_replays"] = [replay(r) for r in as_list(s.get("replay"))]
            s["_run"] = run_commands(s)
        data = {
            "page": slug,
            "prefix": STORAGE_PREFIX,
            "course": manifest,
            "courseIndex": idx,
            "steps": [str(s["id"]) for s in page.get("steps") or []],
        }
        ctx = {
            "page": page,
            "slug": slug,
            "label": page_label(page),
            "nav": NAV,
            "nav_ref": NAV_REF,
            "pages": pages,
            "app_url": APP_URL,
            "css": Markup(css),
            "js": Markup(js),
            "guide_data": Markup(json.dumps(data, ensure_ascii=False).replace("</", "<\\/")),
            "prev": pages.get(prev_slug) if prev_slug else None,
            "next": pages.get(next_slug) if next_slug else None,
            "manifest": manifest,
            "course_index": idx,
            "label_of": lambda s: page_label(pages[s]),
        }
        html = env.get_template(f"{page['template']}.j2").render(**ctx)
        target = out / f"{slug}.html"
        target.write_text(html, encoding="utf-8")
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the workshop guide into site/dist/.")
    ap.add_argument("--out", default=None, help="Output folder (default: site/dist)")
    args = ap.parse_args(argv)
    try:
        written = build(Path(args.out) if args.out else None)
    except ContentError as e:
        print(e, file=sys.stderr)
        return 1
    where = written[0].parent
    try:
        where = where.relative_to(Path.cwd())
    except ValueError:
        pass
    print(f"Built {len(written)} pages in {where}/. Open {where}/index.html in your browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
