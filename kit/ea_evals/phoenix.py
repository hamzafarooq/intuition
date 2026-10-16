"""`ea-eval phoenix`: send a finished run's traces to Arize Phoenix as OpenInference spans (optional, for instructors).

One trace per trial: a root AGENT span (the trial and its grade), an LLM span for each model step, a TOOL span for
each tool call, and a nested AGENT span for each sub-agent. Times come from the trace, so the timeline is real.
Needs the `phoenix` extra (`uv sync --extra phoenix`). Start Phoenix first: `uvx arize-phoenix serve`.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openinference.semconv.resource import ResourceAttributes
from openinference.semconv.trace import MessageAttributes as M
from openinference.semconv.trace import OpenInferenceMimeTypeValues as Mime
from openinference.semconv.trace import OpenInferenceSpanKindValues as Kind
from openinference.semconv.trace import SpanAttributes as A
from openinference.semconv.trace import ToolCallAttributes as TC
from opentelemetry import context as otel_context
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Status, StatusCode

from ea_harness.trace import read_trace

DEFAULT_ENDPOINT = "http://localhost:6006/v1/traces"
OUTPUTS = {"assistant", "thinking_summary", "tool_call", "skill_loaded", "delegate"}  # what a model step produces
JSON, TEXT = Mime.JSON.value, Mime.TEXT.value


@dataclass
class Node:
    """A span to be: built from the trace first, then emitted with its trace times."""

    parent: "Node | None"
    name: str
    kind: str
    start: str
    end: str
    attrs: dict[str, Any] = field(default_factory=dict)
    events: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    error: str = ""
    inputs: list[dict[str, str]] = field(default_factory=list)   # LLM steps: messages since the previous step
    outputs: list[dict[str, Any]] = field(default_factory=list)  # LLM steps: the trace events the step produced
    msgs: list[str] = field(default_factory=list)                # LLM steps: Claude message ids, from the stream


def endpoint_url(endpoint: str | None = None) -> str:
    """The OTLP traces URL from --endpoint, PHOENIX_COLLECTOR_ENDPOINT or the default; a bare host gets /v1/traces."""
    url = (endpoint or os.environ.get("PHOENIX_COLLECTOR_ENDPOINT") or DEFAULT_ENDPOINT).rstrip("/")
    return url if url.endswith("/v1/traces") else url + "/v1/traces"


def _ns(ts: str) -> int:
    return int(datetime.fromisoformat(ts).timestamp() * 1e9)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def _stream_messages(path: Path) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """From Claude Code's raw stream-json, if saved: tool_use id (or text) -> message id, and each message's model and usage."""
    msg_of: dict[str, str] = {}
    msgs: dict[str, dict[str, Any]] = {}
    for ev in read_trace(path):
        m = ev.get("message") or {}
        if ev.get("type") != "assistant" or not isinstance(m, dict) or not m.get("id"):
            continue
        info = msgs.setdefault(m["id"], {"model": m.get("model", ""), "usage": {}})
        u = m.get("usage") or {}
        if (u.get("output_tokens") or 0) >= (info["usage"].get("output_tokens") or 0):
            info["usage"] = u  # partial messages repeat the usage; the last one is complete
        for b in m.get("content") or []:
            key = isinstance(b, dict) and (b.get("id") or b.get("text") or b.get("thinking"))
            if key:
                msg_of[key] = m["id"]
    return msg_of, msgs


def _failed(grades: dict[str, Any]) -> list[str]:
    return [f"{c['id']} ({c.get('level', '')})" for c in grades.get("criteria", []) if c.get("verdict") == "no"]


def trial_nodes(tdir: Path, grades: dict[str, Any] | None = None) -> list[Node]:
    """The span tree for one trial folder: trace.jsonl, plus stream.jsonl and grades.json when they're there."""
    events = read_trace(Path(tdir) / "trace.jsonl")
    if not events:
        return []
    if grades is None and (Path(tdir) / "grades.json").exists():
        grades = json.loads((Path(tdir) / "grades.json").read_text())
    msg_of, msgs = _stream_messages(Path(tdir) / "stream.jsonl")
    e0 = events[0]
    case_id, variant, trial = e0.get("case_id") or "trial", e0.get("variant") or "default", e0.get("trial") or 1
    root = Node(None, f"{case_id}/{variant}/t{trial}", Kind.AGENT.value, e0["ts"], events[-1]["ts"])
    nodes = [root]

    def add(parent: Node, name: str, kind: Kind, ts: str, **attrs: Any) -> Node:
        n = Node(parent, name, kind.value, ts, ts, attrs)
        nodes.append(n)
        return n

    # A context is one agent's conversation: None for the main assistant, else the delegate call that started it.
    agents: dict[str | None, Node] = {None: root}
    steps: dict[str | None, Node] = {}           # its latest LLM step
    fresh: dict[str | None, bool] = {}           # inputs arrived since that step's last output
    pending: dict[str | None, list[dict[str, str]]] = {}  # input messages for its next step
    last: dict[str | None, str] = {}             # time of its latest event
    calls: dict[str, Node] = {}                  # call id -> TOOL or sub-agent span
    open_calls: set[str] = set()
    for e in events:
        ctx, t, ts, cid = e.get("parent_call_id"), e.get("type"), e["ts"], e.get("call_id") or ""
        agent = agents.get(ctx, root)
        if t in OUTPUTS:
            step, mid = steps.get(ctx), msg_of.get(cid or e.get("text") or "")
            same = bool(step and mid and mid in step.msgs)        # the stream says: the same model message
            other = bool(step and mid and step.msgs and not same)  # the stream says: another model message
            # Tool results can arrive while a message is still streaming, so only the stream can join across them
            if step is None or other or (fresh.get(ctx) and not same):
                step = steps[ctx] = add(agent, "llm", Kind.LLM, last.get(ctx, ts))
                step.inputs, fresh[ctx] = pending.pop(ctx, []), False
            step.end = ts
            step.outputs.append(e)
            if mid and mid not in step.msgs:
                step.msgs.append(mid)
        if t == "user":
            pending.setdefault(ctx, []).append({"role": "user", "content": e.get("text", "")})
            fresh[ctx] = True
        elif t == "tool_call":
            calls[cid] = add(agent, e.get("tool", "tool"), Kind.TOOL, ts, **{
                A.TOOL_NAME: e.get("tool", ""), A.INPUT_VALUE: _text(e.get("input")), A.INPUT_MIME_TYPE: JSON})
            open_calls.add(cid)
        elif t == "skill_loaded":
            add(agent, "Skill", Kind.TOOL, ts, **{A.TOOL_NAME: "Skill", A.INPUT_VALUE: _text({"skill": e.get("skill", "")}),
                                                  A.INPUT_MIME_TYPE: JSON, A.OUTPUT_VALUE: f"Loaded the {e.get('skill')} skill"})
        elif t == "delegate":
            to = e.get("to") or "subagent"
            calls[cid] = agents[cid] = add(agent, to, Kind.AGENT, ts, **{A.AGENT_NAME: to, A.INPUT_VALUE: e.get("task", "")})
            pending[cid], last[cid] = [{"role": "user", "content": e.get("task", "")}], ts
            open_calls.add(cid)
        elif t == "tool_result":
            out = e.get("output")
            if cid in calls:
                n = calls[cid]
                n.end = ts
                n.attrs.update({A.OUTPUT_VALUE: _text(out), A.OUTPUT_MIME_TYPE: TEXT if isinstance(out, str) else JSON})
                if not e.get("ok", True):
                    n.error = n.error or e.get("error") or "failed"
                open_calls.discard(cid)
            pending.setdefault(ctx, []).append({"role": "tool", "content": _text(out), "tool_call_id": cid})
            fresh[ctx] = True
        elif t == "handoff_result" and cid in calls:
            calls[cid].attrs[A.OUTPUT_VALUE] = e.get("text", "")
        elif t in ("approval_request", "approval_response", "check", "signal"):
            target = calls.get(cid, agent)
            extra = {k: _text(v) for k, v in e.items() if k in ("summary", "decision", "by", "reason", "verifier", "ok",
                                                               "problems", "kind", "object_id", "detail") and v is not None}
            target.events.append((t, ts, extra))
            if t == "approval_response" and e.get("decision") == "deny":
                target.error = f"Blocked: denied by {e.get('by')}" + (f" ({e['reason']})" if e.get("reason") else "")
        elif t in ("stop_check_block", "budget_exceeded", "error", "site_event"):
            extra = {k: _text(v) for k, v in e.items() if k in ("reason", "which", "where", "message", "site", "kind", "path")}
            root.events.append((t, ts, extra))
            if t == "error":
                root.error = e.get("message", "error")
        last[ctx] = ts
    for cid in open_calls:
        calls[cid].end, calls[cid].error = events[-1]["ts"], "No result: the trial ended first"

    usage = [e for e in events if e.get("type") == "usage"]
    model = next((u["model"] for u in reversed(usage) if u.get("model")), (grades or {}).get("model", ""))
    for n in nodes:
        if n.kind == Kind.LLM.value:
            _llm_attrs(n, msgs, model)
    _root_attrs(root, events, grades, model, usage)
    return nodes


def _llm_attrs(step: Node, msgs: dict[str, dict[str, Any]], model: str) -> None:
    a = step.attrs
    info = [msgs[m] for m in step.msgs if m in msgs]
    a.update({A.LLM_MODEL_NAME: next((i["model"] for i in info if i["model"]), model),
              A.LLM_PROVIDER: "anthropic", A.LLM_SYSTEM: "anthropic"})
    if info:  # prompt tokens only: the stream's per-message output counts are snapshots taken mid-message
        u = [i["usage"] for i in info]
        read = sum(x.get("cache_read_input_tokens") or 0 for x in u)
        write = sum(x.get("cache_creation_input_tokens") or 0 for x in u)
        a.update({A.LLM_TOKEN_COUNT_PROMPT: sum(x.get("input_tokens") or 0 for x in u) + read + write,
                  A.LLM_TOKEN_COUNT_PROMPT_DETAILS_CACHE_READ: read, A.LLM_TOKEN_COUNT_PROMPT_DETAILS_CACHE_WRITE: write})
    for i, m in enumerate(step.inputs):
        p = f"{A.LLM_INPUT_MESSAGES}.{i}."
        a[p + M.MESSAGE_ROLE], a[p + M.MESSAGE_CONTENT] = m["role"], m["content"]
        if m.get("tool_call_id"):
            a[p + M.MESSAGE_TOOL_CALL_ID] = m["tool_call_id"]
    out = f"{A.LLM_OUTPUT_MESSAGES}.0."
    a[out + M.MESSAGE_ROLE] = "assistant"
    text = "\n\n".join(e.get("text", "") for e in step.outputs if e["type"] == "assistant")
    if text:
        a[out + M.MESSAGE_CONTENT] = text
    tool_calls = []
    for e in step.outputs:
        if e["type"] == "tool_call":
            tool_calls.append((e.get("call_id", ""), e.get("tool", ""), e.get("input")))
        elif e["type"] == "delegate":
            tool_calls.append((e.get("call_id", ""), "Agent", {"subagent_type": e.get("to"), "description": e.get("description", ""),
                                                               "prompt": e.get("task", "")}))
        elif e["type"] == "skill_loaded":
            tool_calls.append((e.get("call_id", ""), "Skill", {"skill": e.get("skill")}))
        elif e["type"] == "thinking_summary":
            step.events.append(("thinking", e["ts"], {"text": e.get("text", "")}))
    for j, (cid, name, args) in enumerate(tool_calls):
        p = f"{out}{M.MESSAGE_TOOL_CALLS}.{j}."
        a.update({p + TC.TOOL_CALL_ID: cid, p + TC.TOOL_CALL_FUNCTION_NAME: name, p + TC.TOOL_CALL_FUNCTION_ARGUMENTS_JSON: _text(args)})
    a.update({A.INPUT_VALUE: _text(step.inputs), A.INPUT_MIME_TYPE: JSON})
    if text:
        a.update({A.OUTPUT_VALUE: text, A.OUTPUT_MIME_TYPE: TEXT})
    else:
        a.update({A.OUTPUT_VALUE: _text([{"name": n, "arguments": x} for _, n, x in tool_calls]), A.OUTPUT_MIME_TYPE: JSON})


def _root_attrs(root: Node, events: list[dict[str, Any]], grades: dict[str, Any] | None, model: str,
                usage: list[dict[str, Any]]) -> None:
    e0, g = events[0], grades or {}
    cost = (g.get("usage") or {}).get("cost_usd")
    if cost is None:
        cost = round(sum(u.get("cost_usd") or 0 for u in usage), 4)
    status = next((e.get("status") for e in reversed(events) if e.get("type") == "status"), "missing")
    meta = {"run_id": e0.get("run_id", ""), "case_id": e0.get("case_id", ""), "variant": e0.get("variant") or "default",
            "trial": e0.get("trial", 1), "harness": e0.get("harness", ""), "model": model, "status": status,
            "cost_usd": cost, "passed": g.get("passed"), "overclaim": g.get("overclaim"), "failed_criteria": _failed(g)}
    tags = [meta["case_id"], meta["variant"]] + (["pass" if g["passed"] else "fail"] if "passed" in g else [])
    root.attrs.update({
        A.AGENT_NAME: "assistant",
        A.INPUT_VALUE: next((e.get("text", "") for e in events if e.get("type") == "user"), ""),
        A.OUTPUT_VALUE: next((e.get("text", "") for e in reversed(events) if e.get("type") == "assistant" and not e.get("parent_call_id")), ""),
        A.METADATA: json.dumps(meta, ensure_ascii=False),
        A.TAG_TAGS: [t for t in tags if t],
        **{f"intuition.{k}": v for k, v in meta.items() if v is not None},
    })


def annotations(span_id: str, grades: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Phoenix span annotations for a trial's grade, on its root span."""
    if not grades or "passed" not in grades:
        return []
    failed = _failed(grades)
    out = [{"span_id": span_id, "name": "pass", "annotator_kind": "CODE",
            "result": {"label": "pass" if grades["passed"] else "fail", "score": 1.0 if grades["passed"] else 0.0,
                       "explanation": ("Criteria not met: " + ", ".join(failed)) if failed else "Every criterion met"}}]
    if grades.get("overclaim") is not None:
        out.append({"span_id": span_id, "name": "overclaim", "annotator_kind": "CODE",
                    "result": {"label": "yes" if grades["overclaim"] else "no", "score": 1.0 if grades["overclaim"] else 0.0,
                               "explanation": "Said done but failed" if grades["overclaim"] else "Its status matched the grade"}})
    return out


def emit(nodes: list[Node], tracer: otel_trace.Tracer) -> otel_trace.Span | None:
    """Create the spans with their trace times (parents come before children in `nodes`); returns the root span."""
    spans: dict[int, otel_trace.Span] = {}
    for n in nodes:
        ctx = otel_trace.set_span_in_context(spans[id(n.parent)]) if n.parent else otel_context.Context()
        attrs = {A.OPENINFERENCE_SPAN_KIND: n.kind, **{k: v for k, v in n.attrs.items() if v is not None}}
        s = tracer.start_span(n.name, context=ctx, start_time=_ns(n.start), attributes=attrs)
        for name, ts, extra in n.events:
            s.add_event(name, attributes=extra, timestamp=_ns(ts))
        s.set_status(Status(StatusCode.ERROR, n.error) if n.error else Status(StatusCode.OK))
        spans[id(n)] = s
    for n in nodes:
        spans[id(n)].end(end_time=max(_ns(n.end), _ns(n.start)))
    return spans[id(nodes[0])] if nodes else None


def trial_dirs(run: str, case: str | None = None) -> list[Path]:
    """Trial folders with a trace: a run id under evals/results/, or one trial folder (such as a fixture)."""
    from .runner import results_dir

    if (Path(run) / "trace.jsonl").exists():
        return [Path(run)]
    return [p.parent for p in sorted((results_dir() / run).glob("*/*/*/t*/trace.jsonl")) if not case or p.parts[-5] == case]


class Collector:
    """Builds each trial's spans in memory, under one resource carrying the Phoenix project name."""

    def __init__(self, project: str = "intuition"):
        self.memory = InMemorySpanExporter()
        resource = Resource.create({ResourceAttributes.PROJECT_NAME: project, "service.name": "intuition"})
        self.provider = TracerProvider(resource=resource, shutdown_on_exit=False)
        self.provider.add_span_processor(SimpleSpanProcessor(self.memory))
        self.tracer = self.provider.get_tracer("ea_evals.phoenix")

    def trial(self, tdir: Path) -> tuple[list[ReadableSpan], otel_trace.Span | None]:
        self.memory.clear()
        root = emit(trial_nodes(tdir), self.tracer)
        return list(self.memory.get_finished_spans()), root


def tree(spans: list[ReadableSpan]) -> str:
    """The span tree as indented text, one span a line."""
    kids: dict[int | None, list[ReadableSpan]] = {}
    for s in spans:
        kids.setdefault(s.parent.span_id if s.parent else None, []).append(s)
    lines: list[str] = []

    def walk(parent: int | None, depth: int) -> None:
        for s in sorted(kids.get(parent, []), key=lambda s: s.start_time or 0):
            lines.append("  " * depth + _line(s))
            walk(s.context.span_id if s.context else None, depth + 1)

    walk(None, 0)
    return "\n".join(lines)


def _line(s: ReadableSpan) -> str:
    a = s.attributes or {}
    kind = a.get(A.OPENINFERENCE_SPAN_KIND, "")
    bits = [f"{kind:5} {s.name}", f"{((s.end_time or 0) - (s.start_time or 0)) / 1e9:.1f}s"]
    if kind == Kind.LLM.value:
        if A.LLM_TOKEN_COUNT_PROMPT in a:
            bits.append(f"prompt {a[A.LLM_TOKEN_COUNT_PROMPT]} tokens")
        names, j = [], 0
        while f"{A.LLM_OUTPUT_MESSAGES}.0.{M.MESSAGE_TOOL_CALLS}.{j}.{TC.TOOL_CALL_FUNCTION_NAME}" in a:
            names.append(str(a[f"{A.LLM_OUTPUT_MESSAGES}.0.{M.MESSAGE_TOOL_CALLS}.{j}.{TC.TOOL_CALL_FUNCTION_NAME}"]))
            j += 1
        bits.append(("calls " + ", ".join(names)) if names else "replies")
    if s.parent is None:
        bits += [f"{k}={a[f'intuition.{k}']}" for k in ("passed", "overclaim", "cost_usd") if f"intuition.{k}" in a]
        if a.get("intuition.failed_criteria"):
            bits.append("failed " + ", ".join(a["intuition.failed_criteria"]))
    if s.status.status_code == StatusCode.ERROR:
        bits.append(f"ERROR {s.status.description}")
    return "  ".join(bits)


def dry_run(run: str, project: str = "intuition", case: str | None = None) -> int:
    """Print the span tree for the first trial; send nothing."""
    dirs = trial_dirs(run, case)
    if not dirs:
        print(f"No trials with a trace.jsonl in {run}")
        return 2
    spans, _ = Collector(project).trial(dirs[0])
    print(f"{dirs[0]}: {len(spans)} spans for project '{project}' (dry run, nothing sent)\n")
    print(tree(spans))
    return 0


def export(run: str, endpoint: str | None = None, project: str = "intuition", case: str | None = None) -> int:
    """Send every trial of a run to Phoenix, then add each trial's grade as annotations on its root span."""
    import httpx
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import SpanExportResult

    url = endpoint_url(endpoint)
    base = url.removesuffix("/v1/traces")
    dirs = trial_dirs(run, case)
    if not dirs:
        print(f"No trials with a trace.jsonl in {run}")
        return 2
    try:
        httpx.get(base, timeout=5)
    except httpx.HTTPError:
        print(f"No Phoenix at {base}. Start it with `uvx arize-phoenix serve` (or `docker run -p 6006:6006 arizephoenix/phoenix`).")
        return 2
    otlp, collector, notes = OTLPSpanExporter(endpoint=url), Collector(project), []
    n_spans = 0
    for tdir in dirs:
        spans, root = collector.trial(tdir)
        if otlp.export(spans) != SpanExportResult.SUCCESS:
            print(f"Phoenix didn't accept the spans for {tdir} (endpoint {url})")
            return 2
        n_spans += len(spans)
        grades = json.loads((tdir / "grades.json").read_text()) if (tdir / "grades.json").exists() else None
        if root is not None:
            notes += annotations(format(root.get_span_context().span_id, "016x"), grades)
    otlp.shutdown()
    print(f"Sent {len(dirs)} trials ({n_spans} spans) to {url}, project '{project}'.")
    if notes:
        try:
            r = httpx.post(f"{base}/v1/span_annotations", params={"sync": "false"}, json={"data": notes}, timeout=30)
            r.raise_for_status()
            print(f"Added {len(notes)} grade annotations (pass, overclaim).")
        except httpx.HTTPError as err:
            print(f"Spans sent, but the grade annotations weren't: {err}")
    print(f"Open {base} and choose the '{project}' project.")
    return 0
