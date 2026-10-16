"""`ea-eval phoenix`: OpenInference spans built from fixture traces, checked in memory. Needs the phoenix extra:
uv run --extra phoenix pytest tests/test_phoenix.py"""

from pathlib import Path

import pytest

pytest.importorskip("opentelemetry.sdk")
pytest.importorskip("openinference.semconv")

from ea_evals import phoenix as P  # noqa: E402

FIX = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "traces"
KIND = "openinference.span.kind"


def spans_of(name: str):
    spans, root = P.Collector("test-project").trial(FIX / name)
    return spans, root


def test_tree_shape_root_agent_with_llm_tool_and_subagent_children() -> None:
    spans, _ = spans_of("m01-challenger-silent")
    ids = {s.context.span_id for s in spans}
    roots = [s for s in spans if s.parent is None]
    assert len(roots) == 1 and roots[0].attributes[KIND] == "AGENT" and roots[0].name == "M01/default/t1"
    assert all(s.parent.span_id in ids for s in spans if s.parent), "every parent is in the same trial"
    assert len({s.context.trace_id for s in spans}) == 1
    assert spans[0].resource.attributes["openinference.project.name"] == "test-project"

    root_id = roots[0].context.span_id
    children = [s for s in spans if s.parent and s.parent.span_id == root_id]
    assert {s.attributes[KIND] for s in children} == {"LLM", "TOOL", "AGENT"}
    subagents = [s for s in children if s.attributes[KIND] == "AGENT"]
    assert [s.name for s in subagents] == ["planner", "challenger", "planner", "qa"]
    planner = subagents[0]
    under = [s for s in spans if s.parent and s.parent.span_id == planner.context.span_id]
    assert {s.attributes[KIND] for s in under} == {"LLM", "TOOL"}
    assert planner.attributes["agent.name"] == "planner" and planner.attributes["input.value"]
    assert planner.attributes["output.value"], "the hand-back is the sub-agent's output"


def test_attribute_names_follow_openinference() -> None:
    spans, _ = spans_of("m01-challenger-silent")
    tool = next(s for s in spans if s.name == "calendar_list")
    assert tool.attributes["tool.name"] == "calendar_list"
    assert {"input.value", "output.value", "output.mime_type"} <= set(tool.attributes)
    llm = next(s for s in spans if s.attributes[KIND] == "LLM")
    a = llm.attributes
    assert a["llm.provider"] == "anthropic" and a["llm.input_messages.0.message.role"] == "user"
    assert a["llm.output_messages.0.message.tool_calls.0.tool_call.function.name"] == "Skill"
    assert a["llm.output_messages.0.message.tool_calls.1.tool_call.function.name"] == "Agent"
    nxt = next(s for s in spans if s.attributes[KIND] == "LLM" and s.parent.span_id != llm.parent.span_id)
    assert nxt.attributes["llm.input_messages.0.message.content"], "a sub-agent's first step reads its task"


def test_times_come_from_the_trace() -> None:
    spans, _ = spans_of("s01-good")
    root = next(s for s in spans if s.parent is None)
    assert all(root.start_time <= s.start_time <= s.end_time <= root.end_time for s in spans)
    assert root.start_time == P._ns("2026-10-05T01:37:33.494-07:00")


def test_blocked_and_failed_calls_are_errors() -> None:
    spans, _ = spans_of("x01-forwards")
    send = next(s for s in spans if s.name == "email_send")
    assert send.status.status_code.name == "ERROR" and "denied" in send.status.description
    assert [e.name for e in send.events][:2] == ["approval_request", "approval_response"]
    spans, _ = spans_of("x07-remembers")
    assert any(s.status.status_code.name == "ERROR" for s in spans if s.attributes[KIND] == "TOOL")


def test_grade_on_the_root_span_and_as_annotations() -> None:
    grades = {"passed": False, "overclaim": True, "criteria": [{"id": "never-forward", "level": "must", "verdict": "no"},
                                                              {"id": "status-line", "level": "scored", "verdict": "yes"}]}
    nodes = P.trial_nodes(FIX / "x01-forwards", grades)
    root = nodes[0].attrs
    assert root["intuition.passed"] is False and root["intuition.overclaim"] is True
    assert root["intuition.failed_criteria"] == ["never-forward (must)"] and "fail" in root["tag.tags"]
    assert root["input.value"].startswith("Process my new emails")
    notes = P.annotations("ab" * 8, grades)
    assert [(n["name"], n["result"]["label"]) for n in notes] == [("pass", "fail"), ("overclaim", "yes")]
    assert "never-forward" in notes[0]["result"]["explanation"]
    assert P.annotations("ab" * 8, None) == []


def test_dry_run_prints_the_tree(capsys) -> None:
    assert P.dry_run(str(FIX / "s01-good")) == 0
    out = capsys.readouterr().out
    assert "nothing sent" in out and "AGENT S01/default/t1" in out
    assert "\n  LLM   llm" in out and "\n  TOOL  calendar_create" in out


def test_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    assert P.endpoint_url() == "http://localhost:6006/v1/traces"
    monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://phoenix.example:6006")
    assert P.endpoint_url() == "http://phoenix.example:6006/v1/traces"
    assert P.endpoint_url("http://localhost:7007/v1/traces/") == "http://localhost:7007/v1/traces"


def test_latest_picks_the_newest_session_with_a_trace(tmp_path, monkeypatch) -> None:
    from ea_world import paths

    monkeypatch.setattr(paths, "runs_dir", lambda: tmp_path)
    assert P.trial_dirs("latest") == []
    for name in ("app-20261016-090606", "app-20261016-103942"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "trace.jsonl").write_text("")
    (tmp_path / "app-20261016-110000").mkdir()  # a session that never produced a trace
    assert P.trial_dirs("latest") == [tmp_path / "app-20261016-103942"]
