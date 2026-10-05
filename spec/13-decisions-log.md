# Decisions log

Add a row whenever a decision is made or changed during the build.

## Decisions

| Date | Decision | Why |
|---|---|---|
| 2026-10-04 | The example is an executive assistant for a fictional sales director, built as an open alternative to Instinct | Relatable; real business value; the riskiest agent behaviours (side effects, untrusted input, other people, time zones) |
| 2026-10-04 | Standalone workshop; no references to other courses in student materials | The audience is new to agents |
| 2026-10-04 | Synthetic world, `.example` domains, fixed clock in the London clock-change week | Exact gold answers; planted traps; no real data |
| 2026-10-04 | Tool server in Python on MCP SDK v2 (`MCPServer`) | Current SDK; FastMCP was renamed in v2 |
| 2026-10-04 | Assistant lives in `assistant/`, separate from the builder's workspace; built-in file, shell and web tools denied | Keeps the builder's Claude Code and Maya's assistant apart; stops the assistant reading gold files |
| 2026-10-04 | ~~Two harnesses~~ → **Claude Code only for now**, driven headless (`claude -p`, stream-json, `--resume`, `--permission-prompt-tool`) on each person's Claude login; independent harness deferred ([later/](later/independent-harness.md)); Lesson 5 compares harness configurations instead | No Anthropic API key available; Claude Code needs only a Claude login. Owner decision |
| 2026-10-04 | Monday-brief "team" built from ordinary sub-agents in sequence, not Claude Code's experimental agent teams | Agent teams are experimental and Claude Code-only; sub-agents work on both harnesses |
| 2026-10-04 | Rubrics are the main grading tool: outcome, process and team criteria, yes/no, must-pass or scored; facts graded in code, judgements by a calibrated judge | Agent runs vary; rubrics grade properties, not one path |
| 2026-10-04 | Spider charts for results, with fixed axes, exact numbers beside them and must-pass failures flagged | They work well with audiences; the rules stop them misleading |
| 2026-10-04 | Self-checks grounded in state and code (verifiers, read-back, reviewer, stop check); measured by overclaim rate | Models can't reliably find their own mistakes without an outside signal (Huang et al. 2023; Reflexion) |
| 2026-10-04 | Assistant on Claude Code with the `opus` alias; judge and simulated Maya on OpenAI (`gpt-6.1-sol` and `gpt-6-luna`, ids to verify) | Owner decision; a different model family as judge avoids self-grading |
| 2026-10-04 | No Anthropic API key. OpenAI key for judge and simulated Maya (provided during the workshop; students' own afterwards). Students bring their own SerpAPI key. Google key not used | Owner decision |
| 2026-10-04 | Deployment is local only: `docs/SETUP.md` for Mac, Windows (WSL2) and Linux, plus `make doctor` | Students take the code home ([15-setup.md](15-setup.md)) |
| 2026-10-04 | App name **Intuition** confirmed | Owner decision |
| 2026-10-04 | Reservations happen visibly in Brave on mock booking sites (Skyway, Stays, Tables), through the same Chrome DevTools MCP setup used in this environment (`--browserUrl http://127.0.0.1:9222`). The browser only creates holds; the gated booking tools confirm them after approval. A guard hook keeps the browser on the mock sites | Owner request: show reservations working behind the scenes, without weakening the gate or the graders |
| 2026-10-04 | Deck overflow checked by text budgets the template guarantees, not by rendering | No browser dependency for students |
| 2026-10-04 | App named **Intuition**, with its own look; nothing copied from Instinct or WhatsApp | Close to "instinct" in meaning; avoids impersonating real companies |
| 2026-10-04 | Onboarding page plus docked messaging simulator in a local web app; phone access deferred; voice is a stretch goal using the browser's speech APIs | What the user asked for; no extra keys or services |
| 2026-10-04 | Guide site not public: shipped in the starter repo, opens from disk | User decision |
| 2026-10-04 | House rule 13: after a disconnect, stop using anything learned from that connector, including earlier in the conversation | Makes the reported Instinct retention failure testable (X07) |
| 2026-10-05 | `approval_prompt` replies with the **flat** shape: `{"behavior": "allow", "updatedInput": {...}}` or `{"behavior": "deny", "message": "..."}`, as a single text block (register the tool with `@server.tool(structured_output=False)`; a `-> str` tool with structured output is rejected with "Expected a single text block") | Live test, Claude Code 2.1.289: allow with an injected `updatedInput` field reached the tool; deny appeared in `permission_denials` |
| 2026-10-05 | `.claude/agents/` **does** load in `claude -p`; no `--agents` JSON needed. The `Agent` tool's input names the agent in `subagent_type` (also `description`, `prompt`); sub-agent events carry `parent_tool_use_id`; `system` events `task_started`/`task_notification` also appear | Live test: `init.agents` listed the project agent, which ran its MCP tool |
| 2026-10-05 | Brave resolves `*.localhost` to 127.0.0.1, so the mock sites use **host routing** (`skyway.localhost:8766`) | Live test through the Brave MCP (Brave 154, port 9222) |
| 2026-10-05 | **Untrusted folders ignore `permissions.allow`** in `.claude/settings.json` ("this workspace has not been trusted"); `ask`, `deny`, hooks, skills and agents still apply. The adapter therefore passes `--allowedTools "mcp__ea-world__*" Skill Agent` on the command line. `ask` still wins over a CLI allow (gated tools still reach `approval_prompt`) | Live test; the runner's overlay folders are fresh temp copies, never trusted |
| 2026-10-05 | `--setting-sources` **exists** in 2.1.289. Runs pass `--setting-sources project,local`, which drops personal skills, agents and hooks (built-in skills and plugins still load). `make doctor` still warns about `~/.claude` settings for interactive use | Live test: user skills/agents absent from `init` |
| 2026-10-05 | Stop and SubagentStop hooks run in `-p`. Hook stdin has `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `stop_hook_active`, **`last_assistant_message`** (so the status-line rule reads it directly), and for SubagentStop `agent_id`, `agent_type`, `agent_transcript_path` | Live test |
| 2026-10-05 | MCP tools are deferred in Claude Code 2.1.289: the model calls the built-in `ToolSearch` first. The adapter treats `ToolSearch` as harness plumbing (not flagged, not a tool call in graders) | Live test |
| 2026-10-05 | The skill tool's input field is `skill`; denying `Read` does not stop skills loading; the permission names `Skill` and `Agent` are right | Live test |
| 2026-10-05 | `claude -p --tools ""` runs with no tools (date-reasoning suite) | Live test |
| 2026-10-05 | `claude -p` warns when stdin is a pipe with no data; the adapter runs it with `stdin=DEVNULL` | Live test |
| 2026-10-05 | `chrome-devtools-mcp` pinned to `1.10.1` | `npm view chrome-devtools-mcp version` |
| 2026-10-05 | Added a read-only `bookings_list` tool (travel group) | R08 ("switch my summit flight") needs the existing booking id; nothing else lists bookings |
| 2026-10-05 | Runs build a per-trial overlay at `<run_dir>/assistant/`; hook commands are rewritten to absolute `cd <kit> && <python> -m ea_harness.stopcheck --hook stop --run-dir <run_dir>`, so parallel trials never share `runs/CURRENT`. The overlay drops `.mcp.json` (the adapter passes `--mcp-config` with `--strict-mcp-config`) | Live test: the Stop hook blocked an unchecked `calendar_create` and the assistant ran `check_event` |
| 2026-10-05 | `selfcheck=off` also strips the markers in agent files and removes `check_*` tools from agents' tool lists | Agents' check steps would otherwise point at denied tools |

## Facts verified while writing the spec (2026-10-04)

| Topic | Fact | Source |
|---|---|---|
| MCP Python SDK | v2.3.0 (2026-10-02), Python ≥ 3.10; `from mcp.server.mcpserver import MCPServer`; `@server.tool()` needs parentheses; `ToolError` for messages the client sees; stdio child processes get a minimal environment; snake_case result attributes | py.sdk.modelcontextprotocol.io (migration guide, servers, client) |
| Claude Agent SDK | `claude-agent-sdk`, Python ≥ 3.10; `ClaudeSDKClient` for multi-turn; `can_use_tool` returns `PermissionResultAllow` or `PermissionResultDeny` and is called for "ask" tools; sub-agent messages carry `parent_tool_use_id`; `ResultMessage` has `total_cost_usd`; needs `ANTHROPIC_API_KEY` | code.claude.com/docs/en/agent-sdk/python |
| Claude Code | Sub-agents in `.claude/agents/*.md` (`name`, `description`, `tools`, `model`), invoked through the `Agent` tool; skills appear as the `Skill` tool; permissions precedence deny > ask > allow, MCP names `mcp__<server>__<tool>`; Stop hooks block with `{"decision": "block", "reason": ...}`; agent teams need `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` | code.claude.com/docs (sub-agents, skills, permissions, hooks, agent-teams) |
| SerpAPI | `GET https://serpapi.com/search?engine=google&q=…&api_key=…`; `organic_results[]` with `title`, `link`, `snippet`; `num` no longer supported; no results returns HTTP 200 with an `error` key; free plan 250 searches a month, 50 an hour; cached and failed searches aren't counted | serpapi.com |
| `zoneinfo` | Correct for the 2026 London and Denver transitions on macOS; add `tzdata` for Windows | docs.python.org; local test |
| Claude models | Opus 5.5 $4/$20 per million tokens (cache reads $0.20); Sonnet 5.5 $2/$10; Haiku 4.5 $1/$5; Opus 5.5 rejects temperature, `budget_tokens` and forced tool choice; effort defaults to `medium` | claude-api skill, models table cached 2026-09-25 |

## Claude Code headless facts (checked 2026-10-04)

| Fact | Source |
|---|---|
| `claude -p` works on a Pro or Max login; `total_cost_usd` is still reported (client-side estimate) | code.claude.com/docs/en/headless |
| stream-json events: `system` (`init` with `mcp_servers`), `assistant` and `user` (with `parent_tool_use_id` for sub-agents), `result` (`session_id`, `usage`, `num_turns`, `duration_ms`, `is_error`, `permission_denials`) | headless, cli-reference |
| `--resume <session_id>`; `--model opus\|sonnet\|haiku` or full ids; `--effort low…max`; `--max-turns`; `--append-system-prompt(-file)`; `--allowedTools` and `--disallowedTools` | cli-reference |
| `--permission-prompt-tool` applies to MCP tools and to `ask` or non-allowed calls | headless |
| `MCP_TOOL_TIMEOUT` (ms) and a per-server `timeout`; long MCP calls may auto-background | mcp |
| Stop hooks run in `-p`; `--include-hook-events` puts hook events in the stream | hooks |
| No `--setting-sources` in `-p`, so personal settings load; `--bare` skips them but needs an API key | headless |
| Install: `curl -fsSL https://claude.ai/install.sh \| bash`; `irm https://claude.ai/install.ps1 \| iex`; `brew install --cask claude-code`; `winget install Anthropic.ClaudeCode`; login `claude auth login` | setup |

The Claude Agent SDK facts above are kept for the deferred independent harness; the current build doesn't use the SDK.

## Still to verify at build time

**Settled 2026-10-05** (see the decisions above): the `approval_prompt` reply shape (flat), `.claude/agents/` in `-p` mode (loads), `*.localhost` in Brave (resolves), `Skill`/`Agent` permission names, denying `Read` vs skills, Stop hook stdin, the `Agent` input field, no-tools mode, the `chrome-devtools-mcp` pin.

Then:
- the exact name of the MCP auto-background setting (`CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS`)
- how to run `claude -p` with no tools for the date-reasoning suite
- `.mcp.json` `${VAR:-default}` expansion (interactive use in `assistant/`)
- whether `Skill` and `Agent` are the right permission names
- whether denying `Read` affects skill loading
- the Stop hook's stdin fields (`transcript_path`)
- the `Agent` tool's input field naming the target agent
- OpenAI: current model ids (`gpt-6.1-sol`, `gpt-6-luna` were listed on 2026-10-04) and the Responses API structured-output request shape
- speech recognition support in Brave and Firefox
- that Brave resolves `*.localhost` host names to the local machine (otherwise use path routing)
- the `chrome-devtools-mcp` version to pin, and that `take_screenshot` accepts a file path
- that PreToolUse hooks with the matcher `mcp__browser__.*` fire for those MCP tools in `-p` mode

## Open items

- Check the three Instinct incidents against primary sources before they go on slides.
- Name-check Larkspur Supply, Ridgeway Builders, Brightpath Tools, Granite Homes, Fastlane Supply, Skyway, Harbor Point, Lakeshore Convention Center and the people.
- How many trials fit in a Pro or Max usage window (from the reference run); `opus` or `sonnet` as the student default.
- Workshop OpenAI project key with a spend limit.
- Whether students keep their assistant after the workshop.
