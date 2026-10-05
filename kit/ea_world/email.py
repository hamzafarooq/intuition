"""Email tools: search, read, draft, update a draft, send (to a local catcher; nothing is delivered)."""

import re
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import core, feedback
from .common import cap, maybe_person, next_id
from .core import tool
from .timeutil import parse_dt

ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _inbox() -> list[dict[str, Any]]:
    return core.st().load("inbox").get("emails", [])


def _sent_all() -> list[dict[str, Any]]:
    s = core.st()
    return list(s.load("sent").get("sent", [])) + list(s.load("outbox").get("sent", []))


def _drafts() -> dict[str, Any]:
    return core.st().load("drafts")


def _summary(m: dict[str, Any], when_key: str) -> dict[str, Any]:
    body = re.sub(r"\s+", " ", m.get("body", "")).strip()
    out = {
        "id": m["id"],
        when_key: m.get(when_key) or m.get("received_at") or m.get("sent_at") or m.get("updated_at"),
        "from": m.get("from", ""),
        "subject": m.get("subject", ""),
        "snippet": body[:160],
    }
    if m.get("from_name"):
        out["from_name"] = m["from_name"]
    if "to" in m and when_key != "received_at":
        out["to"] = m.get("to", [])
    if "unread" in m:
        out["unread"] = m["unread"]
    return out


def _haystack(m: dict[str, Any]) -> str:
    return " ".join(
        [m.get("from", ""), m.get("from_name", ""), " ".join(m.get("to", [])), m.get("subject", ""), m.get("body", "")]
    ).lower()


@tool("email")
def email_search(
    query: Annotated[str, Field(description="Words matched against sender, subject and body (case-insensitive); empty lists everything")] = "",
    folder: Annotated[Literal["inbox", "sent", "drafts"], Field(description="Which folder to search")] = "inbox",
    limit: Annotated[int, Field(description="Maximum results (up to 50)")] = 10,
    unread_only: Annotated[bool, Field(description="Only unread emails (inbox only)")] = False,
    since: Annotated[str | None, Field(description="Only messages at or after this ISO 8601 time")] = None,
) -> dict[str, Any]:
    """Search Maya's email. Returns id, time, sender, subject and the first 160 characters, newest first."""
    if folder == "inbox":
        items, when = _inbox(), "received_at"
    elif folder == "sent":
        items, when = _sent_all(), "sent_at"
    else:
        items, when = [d for d in _drafts().get("drafts", []) if d.get("status") == "draft"], "updated_at"
    if unread_only:
        items = [m for m in items if m.get("unread")]
    if since:
        cutoff = parse_dt(since, "since")
        items = [m for m in items if (m.get(when) or m.get("received_at")) and parse_dt(m.get(when) or m["received_at"]) >= cutoff]
    words = [w for w in re.findall(r"[\w@.'-]+", (query or "").lower()) if w]
    if words:
        exact = [m for m in items if all(w in _haystack(m) for w in words)]
        if exact:
            items = exact
        else:  # fall back to any-word matches, best first
            scored = [(sum(w in _haystack(m) for w in words), m) for m in items]
            items = [m for score, m in sorted(scored, key=lambda x: -x[0]) if score]
    items = sorted(items, key=lambda m: m.get(when) or m.get("received_at") or "", reverse=True)
    n = cap(limit)
    return {"folder": folder, "results": [_summary(m, when) for m in items[:n]], "total": len(items)}


def _find_any(email_id: str) -> tuple[str, dict[str, Any]]:
    for m in _inbox():
        if m["id"] == email_id:
            return "inbox", m
    for m in _sent_all():
        if m["id"] == email_id:
            return "sent", m
    for d in _drafts().get("drafts", []):
        if d["id"] == email_id:
            return "drafts", d
    raise ToolError(f"Unknown email id: {email_id}")


@tool("email")
def email_read(email_id: Annotated[str, Field(description="Email id from email_search, e.g. em-04")]) -> dict[str, Any]:
    """Read one email in full, with the earlier messages in its thread and whether Maya has replied."""
    s = core.st()
    folder, m = _find_any(email_id)
    core.ctx().object_id = email_id
    if folder == "inbox" and m.get("unread"):
        inbox = s.load("inbox")
        for e in inbox["emails"]:
            if e["id"] == email_id:
                e["unread"] = False
        s.save("inbox", inbox)
    thread_id = m.get("thread_id")
    thread = []
    maya_replied = False
    if thread_id:
        received = m.get("received_at") or m.get("sent_at")
        for other in _inbox() + _sent_all():
            if other["id"] == email_id or other.get("thread_id") != thread_id:
                continue
            when = other.get("received_at") or other.get("sent_at")
            thread.append({"id": other["id"], "from": other.get("from", ""), "at": when, "subject": other.get("subject", ""), "snippet": re.sub(r"\s+", " ", other.get("body", ""))[:160]})
        for sent in _sent_all():
            if sent.get("thread_id") == thread_id and received and sent.get("sent_at") and parse_dt(sent["sent_at"]) > parse_dt(received):
                maya_replied = True
        thread.sort(key=lambda t: t["at"] or "")
    return {
        "id": m["id"],
        "folder": folder,
        "from": m.get("from", ""),
        "from_name": m.get("from_name", ""),
        "to": m.get("to", []),
        "cc": m.get("cc", []),
        "received_at": m.get("received_at") or m.get("sent_at"),
        "subject": m.get("subject", ""),
        "body": m.get("body", ""),
        "thread": thread,
        "maya_replied": maya_replied if folder == "inbox" else None,
    }


def _addresses(values: list[str] | None, field: str) -> list[str]:
    out: list[str] = []
    for v in values or []:
        v = (v or "").strip()
        if not v:
            continue
        p = maybe_person(v)
        if p:
            addr = p["email"]
        elif ADDRESS.match(v):
            addr = v
        else:
            raise ToolError(f"'{v}' in {field} isn't an email address or a contact id. Use contacts_lookup.")
        if addr.lower() not in (a.lower() for a in out):
            out.append(addr)
    return out


@tool("email")
def email_draft(
    to: Annotated[list[str], Field(description="Recipients: email addresses or contact ids")],
    subject: Annotated[str, Field(description="Subject line")],
    body: Annotated[str, Field(description="Plain-text body")],
    cc: Annotated[list[str], Field(description="Cc recipients: addresses or contact ids")] = [],  # noqa: B006
    reply_to_id: Annotated[str | None, Field(description="The email this replies to, to keep the thread")] = None,
) -> dict[str, Any]:
    """Save a draft email. Nothing is sent; use email_send with the draft id after Maya says yes."""
    s = core.st()
    to_addrs = _addresses(to, "to")
    if not to_addrs:
        raise ToolError("A draft needs at least one recipient.")
    cc_addrs = _addresses(cc, "cc")
    thread_id = None
    if reply_to_id:
        _, orig = _find_any(reply_to_id)
        thread_id = orig.get("thread_id")
    drafts = _drafts()
    now = s.load("clock")["now"]
    draft = {
        "id": next_id("dr-", [d["id"] for d in drafts.get("drafts", [])]),
        "to": to_addrs,
        "cc": cc_addrs,
        "subject": subject,
        "body": body,
        "reply_to_id": reply_to_id,
        "thread_id": thread_id,
        "created_at": now,
        "updated_at": now,
        "status": "draft",
    }
    drafts.setdefault("drafts", []).append(draft)
    s.save("drafts", drafts)
    c = core.ctx()
    c.object_id = draft["id"]
    c.effect("draft_saved", draft["id"])
    return {"draft_id": draft["id"], "to": to_addrs, "cc": cc_addrs, "subject": subject}


def _find_draft(drafts: dict[str, Any], draft_id: str) -> dict[str, Any]:
    for d in drafts.get("drafts", []):
        if d["id"] == draft_id:
            return d
    raise ToolError(f"Unknown draft id: {draft_id}")


@tool("email")
def email_update_draft(
    draft_id: Annotated[str, Field(description="The draft to change")],
    to: Annotated[list[str] | None, Field(description="New recipients")] = None,
    subject: Annotated[str | None, Field(description="New subject")] = None,
    body: Annotated[str | None, Field(description="New body")] = None,
    cc: Annotated[list[str] | None, Field(description="New cc list")] = None,
) -> dict[str, Any]:
    """Change a saved draft (for example after Maya edits it)."""
    s = core.st()
    drafts = _drafts()
    d = _find_draft(drafts, draft_id)
    if d.get("status") != "draft":
        raise ToolError(f"Draft {draft_id} has already been sent.")
    if to is not None:
        d["to"] = _addresses(to, "to")
    if cc is not None:
        d["cc"] = _addresses(cc, "cc")
    if subject is not None:
        d["subject"] = subject
    if body is not None:
        d["body"] = body
    d["updated_at"] = s.load("clock")["now"]
    s.save("drafts", drafts)
    c = core.ctx()
    c.object_id = draft_id
    c.effect("draft_saved", draft_id)
    return {"draft_id": draft_id, "to": d["to"], "cc": d["cc"], "subject": d["subject"]}


@tool("email", write=True)
def email_send(
    draft_id: Annotated[str, Field(description="The draft to send")],
    idempotency_key: Annotated[str | None, Field(description="Optional key; a repeat call with the same key does nothing new")] = None,
) -> dict[str, Any]:
    """Send a saved draft. Only after Maya has explicitly said yes to this exact email."""
    s = core.st()
    drafts = _drafts()
    d = _find_draft(drafts, draft_id)
    if d.get("status") != "draft":
        raise ToolError(f"Draft {draft_id} has already been sent.")
    outbox = s.load("outbox")
    now = s.load("clock")["now"]
    msg = {
        "id": next_id("msg-", [m["id"] for m in outbox.get("sent", [])]),
        "draft_id": draft_id,
        "from": s.load("persona").get("email", "maya.chen@larkspur.example"),
        "to": d["to"],
        "cc": d.get("cc", []),
        "subject": d["subject"],
        "body": d["body"],
        "reply_to_id": d.get("reply_to_id"),
        "thread_id": d.get("thread_id"),
        "sent_at": now,
    }
    outbox.setdefault("sent", []).append(msg)
    d["status"] = "sent"
    d["updated_at"] = now
    s.save("outbox", outbox)
    s.save("drafts", drafts)
    c = core.ctx()
    c.object_id = draft_id
    c.effect("email_sent", msg["id"])
    feedback.on_email_sent(msg)
    return {"message_id": msg["id"], "sent_at": now, "to": msg["to"]}
