"""Saved outputs: meeting briefs and short HTML decks."""

import json
from pathlib import Path
from typing import Annotated, Any

import markdown as md
from jinja2 import Environment, FileSystemLoader, select_autoescape
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from . import core
from .core import tool

TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape(["html", "j2"])
)


class Slide(BaseModel):
    title: Annotated[str, Field(description="A claim headline (a sentence that says something), at most 60 characters")]
    bullets: Annotated[list[str], Field(description="At most 5 bullets of at most 90 characters; at most 70 words a slide")]
    sources: Annotated[list[str], Field(description="Ids or URLs of the sources for this slide's facts")] = []
    notes: Annotated[str | None, Field(description="Speaker notes")] = None


def _next(prefix: str, folder: Path, ext: str) -> str:
    n = 1
    while (folder / f"{prefix}{n}{ext}").exists():
        n += 1
    return f"{prefix}{n}"


@tool("outputs", write=True)
def brief_save(
    title: Annotated[str, Field(description="Brief title")],
    markdown: Annotated[str, Field(description="The brief, in Markdown (at most 300 words)")],
    sources: Annotated[list[str], Field(description="Ids (em-…, doc-…, ev-…) or URLs the brief rests on")],
) -> dict[str, Any]:
    """Save a meeting brief (Markdown, rendered to HTML too). Run check_brief on it afterwards."""
    if not markdown.strip():
        raise ToolError("The brief is empty.")
    c = core.ctx()
    folder = c.run_dir / "outputs" / "briefs"
    folder.mkdir(parents=True, exist_ok=True)
    brief_id = _next("br-", folder, ".md")
    record = {"id": brief_id, "title": title, "markdown": markdown, "sources": list(sources or []),
              "saved_at": core.st().load("clock")["now"]}
    (folder / f"{brief_id}.md").write_text(markdown, encoding="utf-8")
    (folder / f"{brief_id}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    html = TEMPLATES.get_template("brief.html.j2").render(brief=record, body_html=md.markdown(markdown))
    (folder / f"{brief_id}.html").write_text(html, encoding="utf-8")
    c.object_id = brief_id
    c.effect("brief_saved", brief_id)
    return {"brief_id": brief_id, "path": f"outputs/briefs/{brief_id}.html", "words": len(markdown.split())}


@tool("outputs", write=True)
def deck_create(
    title: Annotated[str, Field(description="Deck title")],
    slides: Annotated[list[Slide], Field(description="The slides, in order")],
) -> dict[str, Any]:
    """Create a short 16:9 HTML deck. Run check_deck on it afterwards (text budgets, sources, numbers)."""
    if not slides:
        raise ToolError("A deck needs at least one slide.")
    c = core.ctx()
    folder = c.run_dir / "outputs" / "decks"
    folder.mkdir(parents=True, exist_ok=True)
    deck_id = _next("dk-", folder, ".json")
    record = {
        "id": deck_id,
        "title": title,
        "slides": [s.model_dump() if isinstance(s, BaseModel) else dict(s) for s in slides],
        "saved_at": core.st().load("clock")["now"],
    }
    (folder / f"{deck_id}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    html = TEMPLATES.get_template("deck.html.j2").render(deck=record)
    (folder / f"{deck_id}.html").write_text(html, encoding="utf-8")
    c.object_id = deck_id
    c.effect("deck_saved", deck_id)
    return {"deck_id": deck_id, "path": f"outputs/decks/{deck_id}.html", "slide_count": len(record["slides"])}


def load_brief(run_dir: Path, brief_id: str) -> dict[str, Any] | None:
    p = Path(run_dir) / "outputs" / "briefs" / f"{brief_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_deck(run_dir: Path, deck_id: str) -> dict[str, Any] | None:
    p = Path(run_dir) / "outputs" / "decks" / f"{deck_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def all_briefs(run_dir: Path) -> list[dict[str, Any]]:
    folder = Path(run_dir) / "outputs" / "briefs"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("br-*.json"))] if folder.exists() else []


def all_decks(run_dir: Path) -> list[dict[str, Any]]:
    folder = Path(run_dir) / "outputs" / "decks"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("dk-*.json"))] if folder.exists() else []
