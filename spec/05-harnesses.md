# Harness

**Decision (2026-10-04): the assistant runs on Claude Code only, for now.** The runner and the app drive the Claude Code CLI in headless mode. It uses each person's own Claude login (Pro or Max), so no Anthropic API key is needed. The independent harness is deferred; its design is kept at the end of this file for later.

Everything talks to the harness through one interface, so the independent harness can be added later without touching the runner or the app:

```python
class Harness(Protocol):
    async def start(self, run_dir: Path, config: RunConfig) -> None: ...
    async def send(self, user_message: str) -> TurnResult: ...   # runs until the assistant ends its turn
    async def close(self) -> None: ...

@dataclass
class TurnResult:
    final_text: str
    status: Literal["done", "partial", "failed", "waiting", "missing"]
    status_reason: str
    usage: Usage            # tokens, api_equivalent_cost_usd, seconds, num_turns
    stopped_by: Literal["end_turn", "max_turns", "error", "usage_limit"]

@dataclass
class RunConfig:
    model: str = "opus"                 # Claude Code model alias or full id
    effort: str | None = None           # only if Claude Code exposes it (verify)
    approval_mode: Literal["script", "app", "allow_all"] = "script"
    on_event: Callable[[TraceEvent], None]
    max_turns: int = 40
    idempotency: Literal["auto", "off"] = "off"
    assistant_dir: Path                 # the (possibly overlaid) assistant folder
    env: dict[str, str]                 # EA_* for the tool server
```

## Claude Code headless adapter (`ea_harness/claude_code.py`)

Checked against code.claude.com/docs (headless, cli-reference, hooks, mcp, setup) on 2026-10-04. Items marked **verify** were reported inconsistently or are new; confirm them with a live test before relying on them, and record the result in [13-decisions-log.md](13-decisions-log.md).

Known facts:
- `claude -p` works with a Pro or Max login, no API key.
- `--output-format stream-json --verbose` emits `system` (subtype `init`, with `model`, `tools`, `mcp_servers`, `mcp_server_errors`), `assistant` and `user` messages (content blocks; `parent_tool_use_id` set for sub-agents; `session_id`), and `result` (`result`, `total_cost_usd` as a client-side estimate even for subscriptions, `usage`, `num_turns`, `duration_ms`, `is_error`, `session_id`, `permission_denials`).
- `--resume <session_id>` continues a session.
- `--model` takes aliases (`opus`, `sonnet`, `haiku`) or full ids, and `--effort` takes `low` to `max`.
- `--permission-prompt-tool` applies to MCP tools, and to calls matched by `ask` rules or not allowed.
- `MCP_TOOL_TIMEOUT` (milliseconds) and a per-server `timeout` in the MCP config set tool timeouts. Calls longer than about 2 minutes may be moved to the background unless `CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS` is raised.
- Stop hooks run in `-p` mode on recent versions; `--include-hook-events` adds hook events to the stream.
- There is no `--setting-sources` flag for `-p`, so the user's personal settings and CLAUDE.md also load. `--bare` would skip them but needs an API key.

### One turn

Run the CLI as a subprocess with `cwd=<assistant_dir>`:

```
claude -p "<maya's message>"
  --output-format stream-json --verbose
  --model <model>
  --max-turns <max_turns>
  --effort <level>                        # if set
  --mcp-config <run_dir>/mcp.json --strict-mcp-config
  --permission-prompt-tool mcp__ea-world__approval_prompt
  --include-hook-events
  [--agents '<json>']                     # only if headless doesn't load .claude/agents (verify)
  [--resume <session_id>]                 # every turn after the first
```

- **`<run_dir>/mcp.json`** is written by the adapter for this trial. It has one `ea-world` stdio server entry, with `EA_RUN_DIR`, `EA_MODE`, `EA_WORLD_VARIANT`, `EA_FAULTS`, `EA_SEED`, `EA_APPROVAL_MODE`, `SERPAPI_API_KEY`, `PATH` and `HOME` passed explicitly. `--strict-mcp-config` stops the project `.mcp.json` from adding a second server.
- **Settings, skills and agents** come from the overlaid `assistant/` folder, because it is the working directory: CLAUDE.md, `.claude/settings.json` (permissions and hooks) and `.claude/skills/`. **Verify** that `.claude/agents/` loads in `-p` mode (one report says it doesn't). If it doesn't, the adapter reads each agent file and passes them all through `--agents` as JSON (verify that flag's shape).
- **Personal settings also load** (no `--setting-sources` in `-p`). `make doctor` warns when `~/.claude/CLAUDE.md` or user-level hooks exist, because they can change results. The report records the Claude Code version and whether personal settings were present.
- **Environment for the subprocess:** `MCP_TOOL_TIMEOUT=600000`, and `CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS` raised above 10 minutes (verify the name), so approval waits in the app aren't cut off or backgrounded. Also set `"timeout": 600000` on the `ea-world` entry in `mcp.json`.
- **Session continuity:** the first turn's `result` event gives `session_id`. Later turns pass `--resume <session_id>`, so the whole conversation stays in one Claude Code session.
- **Timeout:** 10 minutes per turn. Kill the process and record `stopped_by="error"` after that.
- **Usage limits:** if the CLI reports a plan usage limit, stop with `stopped_by="usage_limit"`; the runner pauses the run and says when to resume.

### Approvals (`--permission-prompt-tool`)

Claude Code calls the MCP tool named by `--permission-prompt-tool` when a tool call needs permission: the gated tools in `permissions.ask`, and any tool that isn't allowed. The tool is `approval_prompt` on the `ea-world` server ([03-mcp-server.md](03-mcp-server.md#approvals-tool)).

**Verify the reply shape with a live test before anything else in this adapter.** Reports differ between `{"behavior": "allow", "updatedInput": {...}}` / `{"behavior": "deny", "message": "..."}` and a form wrapped in `"decision"`. `make doctor` includes a check that runs a tiny headless session in which one gated tool must be allowed and another denied.

It decides by `EA_APPROVAL_MODE`:

| Mode | Used by | Decision |
|---|---|---|
| `script` | the runner | Applies the approval policy ([04-assistant.md](04-assistant.md#approval-policy)) using `<run_dir>/approval_context.json`, which the runner rewrites before every turn (case data and Maya's latest message) |
| `app` | the app | Writes the request to `<run_dir>/approvals/pending/<id>.json` and waits (polling every 0.5 seconds, up to 10 minutes) for `<run_dir>/approvals/decided/<id>.json`, written when the user clicks Approve or Deny. Timeout means deny |
| `allow_all` | the `gate=off` variant | Allows |

On allow with `idempotency=auto`, the tool returns `updatedInput` with `idempotency_key = sha256(tool + canonical_json(args))[:16]` added to write tools. That's the harness-level fix in Lesson 5.

**Every** request and decision is appended to `<run_dir>/approvals.jsonl`, and the adapter turns those lines into `approval_request` and `approval_response` trace events.

If MCP tool calls time out before 10 minutes, set the timeout environment variable Claude Code uses for MCP tools in the subprocess environment (verify its name and default).

### From stream-json to the trace

Read the CLI's stdout line by line. Each line is one JSON event. Verify the exact event and field names; the mapping is:

| Claude Code event | Trace events |
|---|---|
| `system` (init) | Record `session_id`, model, tools and MCP server status in `meta.json`. Fail the trial with a clear error if `ea-world` isn't connected |
| `assistant` message, `text` block | `assistant` |
| `assistant` message, `thinking` block (if present) | `thinking_summary` |
| `assistant` message, `tool_use` block | `tool_use` named `Skill` becomes `skill_loaded`; named `Agent` becomes `delegate` (verify the input field naming the agent); `mcp__ea-world__<tool>` becomes `tool_call` with the prefix stripped; anything else (built-in tools) becomes `tool_call` and is flagged, because the assistant shouldn't use them |
| `user` message, `tool_result` block | `tool_result` (for an `Agent` call, also `handoff_result`), plus `check` for `check_*` tools and `signal`s from the server call log |
| Any event with `parent_tool_use_id` | Same mapping, with `agent` set to the delegated agent's name and `parent_call_id` set |
| `result` | `usage` (num_turns, duration, tokens, `total_cost_usd` as the API-equivalent cost), `status` (parsed from the final text), `turn_end` |

Signals and approval events are merged in by sequence from `calls.jsonl` and `approvals.jsonl`, so the trace shows the world's reactions in order.

### Stop check

The project's Stop and SubagentStop command hooks (`python -m ea_harness.stopcheck`) run in headless mode too; verify that they do. The hook logs every block it issues to `<run_dir>/stopcheck.jsonl`, and the adapter turns those into `stop_check_block` events. The shared logic is in `ea_harness/stopcheck.py`:

```python
WRITE_TO_CHECK = {
    "calendar_create": "check_event", "calendar_update": "check_event",
    "email_send": "check_email",          # check_email must run on the draft BEFORE send
    "travel_book": "check_booking", "restaurant_book": "check_booking",
    "brief_save": "check_brief", "deck_create": "check_deck",
}
def evaluate(run_dir, since_seq, final_text) -> str | None:
    # 1. read calls.jsonl entries with seq > since_seq (the hook uses the seq stored at the turn's start in run_dir/turn_start)
    # 2. each successful write needs a later successful check_* on the same object id
    #    (email_send: an earlier successful check_email on that draft_id)
    # 3. return a reason naming the first unverified write
    # 4. else, if final_text is available and has no STATUS line, return "End your reply with a STATUS line."
    # 5. else None
```

The hook blocks at most twice per turn (counter in the run directory), with `{"decision": "block", "reason": "..."}`.

### Harness configuration variants (replacing the cross-harness comparison for now)

Lesson 5 compares harness *configurations* on the same model and cases:

| Variant | Change |
|---|---|
| `hooks=off` | No Stop or SubagentStop hooks |
| `subagents=off` | No agents folder; the main assistant does everything |
| `model=sonnet` | Claude Code model alias `sonnet` instead of `opus` |
| `effort=<level>` | If Claude Code exposes effort in headless mode |
| `idempotency=auto` | Approval tool adds idempotency keys |

## Independent harness (deferred)

Kept for a later version: a ~300-line loop on a model API, loading the same assistant files (CLAUDE.md as the system prompt, `load_skill` and `delegate` tools, settings-based permissions), with the same `Harness` interface and trace format. When it's built, it enables the cross-harness comparison and can run on Claude or OpenAI models. The design is kept in [later/independent-harness.md](later/independent-harness.md).
