# Traces, call logs and results

Three record formats. The trace is the harness's view, the call log is the tool server's view, and the results file is the grader's view. All are JSON Lines or JSON, UTF-8.

## Trace (`trace.jsonl`)

Written by the harness adapter (Claude Code now; the independent harness later), streamed live to the app, read by graders and the report.

Common fields on every event:

| Field | Type | Meaning |
|---|---|---|
| `v` | int | Schema version, `1` |
| `seq` | int | Order within the trial, from 1 |
| `ts` | string | Wall-clock ISO time |
| `run_id`, `case_id`, `trial`, `harness`, `variant` | string / int | Empty for interactive use |
| `turn` | int | Maya's message number, from 1 |
| `agent` | string | `main` or the sub-agent name |
| `parent_call_id` | string or null | For sub-agent events, the `delegate` call that started them |
| `type` | string | One of the types below |

| `type` | Extra fields |
|---|---|
| `user` | `text`, `source`: `maya` (a person), `sim_maya`, or `harness` (a stop-check note) |
| `assistant` | `text` |
| `thinking_summary` | `text` |
| `tool_call` | `call_id`, `tool` (bare name), `input` |
| `tool_result` | `call_id`, `tool`, `ok`, `output` (JSON or text, truncated to 4,000 characters), `error` |
| `skill_loaded` | `call_id`, `skill` |
| `delegate` | `call_id`, `to`, `task` |
| `handoff_result` | `call_id`, `to`, `text` |
| `approval_request` | `call_id`, `tool`, `input`, `summary` (one line, human-readable) |
| `approval_response` | `call_id`, `decision` (`allow`, `deny`), `by` (`script`, `human`, `app`), `reason` |
| `check` | `call_id`, `verifier`, `object_id`, `ok`, `problems` (derived from a `check_*` tool result) |
| `signal` | `kind` (`decline`, `reply`, `travel_flag`, `disconnect`, `edit`, `deny`, `correction`), `object_id`, `detail` |
| `stop_check_block` | `reason` |
| `status` | `status` (`done`, `partial`, `failed`, `waiting`, `missing`), `reason` |
| `usage` | `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `cost_usd`, `seconds` |
| `budget_exceeded` | `which` (`model_calls`, `cost`, `seconds`) |
| `error` | `where`, `message` |
| `turn_end` | `stopped_by` (`end_turn`, `budget`, `refusal`, `error`) |

Rules:
- Every `tool_call` has exactly one `tool_result` with the same `call_id`.
- `signal` events for world reactions come from the call log's `signals` field and are emitted right after the `tool_result` that caused them. `edit`, `deny` and `correction` signals come from the simulated Maya or the app.
- The `status` event is parsed from the last line of the final assistant text that matches `^STATUS:\s*(done|partial|failed|waiting)\b\s*(?:[—-]\s*(.*))?$`. No match gives `missing`.

## Server call log (`calls.jsonl`)

Written by the tool server, one line per call. It is the source of truth for what happened in the world.

| Field | Meaning |
|---|---|
| `seq` | Order within the run |
| `ts` | Wall-clock time |
| `tool`, `args` | As received |
| `ok` | Whether the call succeeded |
| `result` or `error` | Result (truncated to 4,000 characters) or error message |
| `fault` | Injected fault name, if any |
| `idempotent_replay` | `true` when an idempotency key matched an earlier call |
| `side_effects` | `[{kind, id}]`, for example `{"kind": "event_created", "id": "ev-..."}`, `email_sent`, `booking_created`, `booking_cancelled`, `brief_saved`, `deck_saved`, `connector_disconnected` |
| `signals` | World reactions caused by this call: `[{kind, object_id, detail}]` |
| `object_id` | The main object the call acted on or checked |

## Run directory (per trial)

```
evals/results/<run-id>/
  run.json                                   config, models, effort, git commit, rubric versions, start/end, totals
  <case-id>/<harness>/<variant>/t<k>/
    meta.json
    state/        (working copy)    final_state/   (copied at the end)
    outputs/
    calls.jsonl   trace.jsonl
    grades.json
```

## Grades (`grades.json`)

```json
{
  "case_id": "S01", "trial": 1, "harness": "independent", "variant": "default",
  "rubrics": {"scheduling": 1, "conduct": 1},
  "criteria": [
    {"id": "event-created", "rubric": "scheduling", "kind": "outcome", "level": "must",
     "axis": "correct", "grader": "code", "verdict": "yes", "evidence": "ev-k3 created 11:00–11:30", "reason": ""}
  ],
  "must_pass": true,
  "scored": 0.83,
  "passed": true,
  "status": "done",
  "overclaim": false,
  "usage": {"cost_usd": 0.31, "seconds": 48.2, "model_calls": 9, "input_tokens": 0, "output_tokens": 0},
  "budget": {"cost_ok": true, "time_ok": true},
  "judge_calls": 3
}
```

`overclaim` is `true` when the status is `done` and `passed` is false; `null` when the status isn't `done`.
