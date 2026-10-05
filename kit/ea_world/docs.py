"""Document search and read."""

import re
from typing import Annotated, Any

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import core
from .common import cap
from .core import tool

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at", "is", "are", "be", "with", "our",
    "my", "me", "what", "whats", "s", "about", "by", "from", "this", "that", "it", "its", "do", "does",
}  # fmt: skip


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOPWORDS}


def doc_text(doc: dict[str, Any]) -> str:
    path = core.st().state_dir / "docs" / doc["file"]
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ToolError(f"Document {doc['id']} is missing from the workspace") from exc


@tool("docs")
def docs_search(
    query: Annotated[str, Field(description="Words to search for")],
    limit: Annotated[int, Field(description="Maximum results (up to 50)")] = 5,
) -> dict[str, Any]:
    """Search Maya's documents (travel policy, forecast summary, customer notes, catalog). Results are
    ranked by word overlap and show each document's visibility (internal or public)."""
    q = tokens(query)
    if not q:
        raise ToolError("Give some words to search for.")
    scored = []
    for d in core.st().load("docs").get("docs", []):
        text = doc_text(d)
        dt = tokens(d.get("title", "")) | tokens(text)
        score = len(q & dt) + 2 * len(q & tokens(d.get("title", "")))
        if score:
            first = next((ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")), "")
            scored.append((score, {"id": d["id"], "title": d.get("title", d["id"]), "snippet": first[:200], "visibility": d.get("visibility", "internal")}))
    scored.sort(key=lambda x: -x[0])
    return {"results": [r for _, r in scored[: cap(limit, 5)]]}


@tool("docs")
def docs_read(doc_id: Annotated[str, Field(description="Document id, e.g. doc-ridgeway")]) -> dict[str, Any]:
    """Read one document in full (Markdown) with its visibility."""
    for d in core.st().load("docs").get("docs", []):
        if d["id"] == doc_id:
            core.ctx().object_id = doc_id
            return {"id": doc_id, "title": d.get("title", doc_id), "visibility": d.get("visibility", "internal"), "markdown": doc_text(d)}
    raise ToolError(f"Unknown document id: {doc_id}")
