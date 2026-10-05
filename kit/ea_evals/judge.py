"""The model judge: one structured yes/no/cant_tell call per judged criterion per trial.

OpenAI Responses API with a strict JSON schema. The model, its reasoning setting and the prompt are
fixed; results are cached by a hash of (model, prompt, criterion version, evidence), so rebuilding a
report costs nothing.
"""

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Protocol

from ea_world import paths

from .context import TrialContext
from .costs import CostMeter

SYSTEM_PROMPT = (
    "You grade one criterion of an AI assistant's work. Answer only the question asked, using only the evidence "
    "given. If the evidence doesn't settle it, answer cant_tell. Quote the evidence you relied on."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["yes", "no", "cant_tell"]},
        "evidence": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "evidence", "reason"],
    "additionalProperties": False,
}
DEFAULT_MODEL = "gpt-5.6-sol"  # decisions log 2026-10-05: gpt-6.1-sol isn't offered
REASONING_EFFORT = "low"
TOOL_OUTPUT_CAP = 600


def cache_dir() -> Path:
    return paths.kit_root() / ".ea_cache" / "judge"


class Judge(Protocol):
    def grade(self, ctx: TrialContext, criterion: dict[str, Any], rubric: str, reference: str) -> dict[str, Any]: ...


# ---------------------------------------------------------------- evidence


def compact_trace(ctx: TrialContext) -> str:
    lines = []
    for e in ctx.events:
        t, who = e.get("type"), e.get("agent", "main")
        if t == "user":
            lines.append(f"[turn {e.get('turn')}] MAYA: {e.get('text', '')}")
        elif t == "assistant":
            lines.append(f"[{who}] ASSISTANT: {e.get('text', '')}")
        elif t == "tool_call":
            lines.append(f"[{who}] CALL {e.get('tool')} {json.dumps(e.get('input', {}), ensure_ascii=False)[:TOOL_OUTPUT_CAP]}")
        elif t == "tool_result":
            out = e.get("output") if e.get("ok") else f"ERROR {e.get('error', '')}"
            text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
            lines.append(f"[{who}] RESULT {e.get('tool')}: {text[:TOOL_OUTPUT_CAP]}")
        elif t == "delegate":
            lines.append(f"[{who}] HAND-OFF to {e.get('to')}: {e.get('task', '')[:TOOL_OUTPUT_CAP]}")
        elif t == "handoff_result":
            lines.append(f"[{who}] {e.get('to')} RETURNED: {e.get('text', '')[:TOOL_OUTPUT_CAP * 2]}")
        elif t == "approval_response":
            lines.append(f"[gate] {e.get('decision')} ({e.get('reason', '')})")
        elif t == "signal":
            lines.append(f"[world] {e.get('kind')}: {e.get('detail', '')}")
        elif t == "skill_loaded":
            lines.append(f"[{who}] SKILL {e.get('skill')}")
    return "\n".join(lines)


def artifact(ctx: TrialContext, name: str | None) -> str:
    name = name or "final"
    if name == "final":
        return ctx.final_text() or "(no reply)"
    if name == "sent":
        sent = ctx.sent()
        return "\n\n".join(f"To: {', '.join(m.get('to', []))}\nCc: {', '.join(m.get('cc', []))}\nSubject: {m.get('subject', '')}\n\n{m.get('body', '')}"
                           for m in sent) or "(nothing sent)"
    if name == "draft":
        m = ctx.latest_email_to(ctx.fill.get("to", "")) if ctx.fill.get("to") else None
        if m is None:
            return "(no draft)"
        return f"To: {', '.join(m.get('to', []))}\nCc: {', '.join(m.get('cc', []))}\nSubject: {m.get('subject', '')}\n\n{m.get('body', '')}"
    if name == "brief":
        b = ctx.latest_brief()
        return f"{b.get('markdown', '')}\n\nSources: {', '.join(b.get('sources', []))}" if b else "(no brief)"
    if name == "deck":
        d = ctx.latest_deck()
        if not d:
            return "(no deck)"
        return "\n\n".join(f"Slide {i}: {s.get('title', '')}\n" + "\n".join(f"- {b}" for b in s.get("bullets", [])) + f"\nSources: {', '.join(s.get('sources', []))}"
                           for i, s in enumerate(d.get("slides", []), start=1))
    if name == "handoffs":
        from .checks import handoff_log

        return handoff_log(ctx)
    if name == "trace":
        out = []
        for e in ctx.events:
            if e.get("type") == "user":
                out.append(f"MAYA: {e.get('text', '')}")
            elif e.get("type") == "assistant" and e.get("agent", "main") == "main":
                out.append(f"ASSISTANT: {e.get('text', '')}")
        return "\n\n".join(out)
    return ctx.final_text()


def sources(ctx: TrialContext, name: str | None) -> str:
    """The sources an artifact should rest on: for briefs and decks, the listed sources' text."""
    ids: list[str] = []
    if name == "brief" and ctx.latest_brief():
        ids = ctx.latest_brief().get("sources", [])
    elif name == "deck" and ctx.latest_deck():
        ids = sorted({s for sl in ctx.latest_deck().get("slides", []) for s in sl.get("sources", [])})
    if not ids:
        return "(see the tool results in the trace)"
    inbox = {e["id"]: e for e in ctx.final.load("inbox").get("emails", [])}
    docs = {d["id"]: d for d in ctx.final.load("docs").get("docs", [])}
    out = []
    for i in ids:
        if i in inbox:
            out.append(f"{i}: {inbox[i].get('subject', '')}\n{inbox[i].get('body', '')}")
        elif i in docs:
            p = ctx.final.state_dir / "docs" / docs[i]["file"]
            out.append(f"{i}:\n{p.read_text(encoding='utf-8')[:2500] if p.exists() else ''}")
        else:
            fetched = next((f for f in ctx.final.load("fetched").get("pages", []) if i.rstrip("/") in f.get("url", "")), None)
            out.append(f"{i}: {(fetched or {}).get('text', '(not fetched in this run)')[:1500]}")
    return "\n\n".join(out)


def format_question(question: str, fill: dict[str, Any]) -> str:
    out = question
    for k, v in fill.items():
        token = "{fill." + k + "}"
        if token in out:
            out = out.replace(token, ", ".join(map(str, v)) if isinstance(v, list) else str(v))
    return out


def build_prompt(ctx: TrialContext, criterion: dict[str, Any], reference: str) -> str:
    parts = [f"QUESTION: {format_question(criterion.get('question', ''), ctx.fill)}"]
    if criterion.get("pass_example"):
        parts.append(f"A PASSING example: {criterion['pass_example']}")
    if criterion.get("fail_example"):
        parts.append(f"A FAILING example: {criterion['fail_example']}")
    if reference:
        parts.append(f"REFERENCE (computed by code, reliable):\n{reference}")
    maya_lines = [e.get("text", "") for e in ctx.of_type("user")]
    parts.append(f"MAYA'S REQUEST: {ctx.case.get('request', '')}\nMAYA'S MESSAGES:\n" + "\n".join(f"- {m}" for m in maya_lines))
    parts.append(f"TRACE (tool outputs cut to {TOOL_OUTPUT_CAP} characters):\n{compact_trace(ctx)}")
    parts.append(f"THE ARTIFACT BEING JUDGED ({criterion.get('artifact', 'final')}):\n{artifact(ctx, criterion.get('artifact'))}")
    parts.append(f"SOURCES:\n{sources(ctx, criterion.get('artifact'))}")
    parts.append('Answer with JSON: {"verdict": "yes" | "no" | "cant_tell", "evidence": "<quote>", "reason": "<one sentence>"}.')
    return "\n\n".join(parts)


# ---------------------------------------------------------------- the OpenAI judge


class OpenAIJudge:
    def __init__(self, model: str | None = None, meter: CostMeter | None = None, cache: bool = True):
        self.model = model or os.environ.get("EA_JUDGE_MODEL") or DEFAULT_MODEL
        self.meter = meter or CostMeter()
        self.cache = cache
        self._client = None
        self._lock = threading.Lock()

    def client(self):  # lazy, so offline paths never need the package configured
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI()
        return self._client

    def key(self, prompt: str, criterion: dict[str, Any], rubric: str) -> str:
        blob = json.dumps([self.model, REASONING_EFFORT, SYSTEM_PROMPT, rubric, criterion.get("id"), criterion.get("question"), prompt], ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()

    def ask(self, prompt: str) -> tuple[dict[str, Any], dict[str, int]]:
        resp = self.client().responses.create(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=prompt,
            reasoning={"effort": REASONING_EFFORT},
            text={"format": {"type": "json_schema", "name": "criterion_verdict", "schema": SCHEMA, "strict": True}},
            store=False,
        )
        data = json.loads(resp.output_text)
        usage = {"input_tokens": getattr(resp.usage, "input_tokens", 0) or 0, "output_tokens": getattr(resp.usage, "output_tokens", 0) or 0}
        return data, usage

    def grade(self, ctx: TrialContext, criterion: dict[str, Any], rubric: str, reference: str) -> dict[str, Any]:
        prompt = build_prompt(ctx, criterion, reference)
        key = self.key(prompt, criterion, rubric)
        path = cache_dir() / f"{key}.json"
        if self.cache and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        try:
            data, usage = self.ask(prompt)
        except Exception as exc:
            return {"verdict": "cant_tell", "evidence": "", "reason": f"judge error: {exc}"[:300]}
        self.meter.add_openai(self.model, usage["input_tokens"], usage["output_tokens"])
        data["model"] = self.model
        if self.cache:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data


class FakeJudge:
    """Offline judge for tests: answers from a mapping of criterion id to verdict (default yes)."""

    def __init__(self, answers: dict[str, str] | None = None, default: str = "yes"):
        self.answers = answers or {}
        self.default = default
        self.calls: list[str] = []

    def grade(self, ctx: TrialContext, criterion: dict[str, Any], rubric: str, reference: str) -> dict[str, Any]:
        self.calls.append(f"{rubric}/{criterion['id']}")
        v = self.answers.get(f"{ctx.case.get('id')}:{criterion['id']}", self.answers.get(criterion["id"], self.default))
        return {"verdict": v, "evidence": "(fake judge)", "reason": "offline"}
