# Architecture

## Components

| Component | Package / path | What it does |
|---|---|---|
| World data | `kit/world/` | Seed JSON and Markdown for Maya's week, variants, web cassettes, gold labels |
| Tool server | `kit/ea_world/` | MCP stdio server. Serves every tool over a per-run copy of the world; logs calls; injects faults; produces world feedback; runs verifiers |
| Builder's brief | `kit/CLAUDE.md` | Tells Claude Code (used by students to build) what the repo is and where things go |
| Assistant files | `kit/assistant/` | The assistant: `CLAUDE.md`, `.mcp.json`, `.claude/` (skills, sub-agents, settings, hooks). See [04-assistant.md](04-assistant.md) |
| App | `kit/ea_app/` | Intuition: onboarding page, docked messaging simulator, behind-the-scenes view ([14-app.md](14-app.md)) |
| Mock booking sites | `kit/ea_sites/` | Skyway, Stays and Tables on port 8766: the websites the assistant uses in the visible browser ([16-browser-bookings.md](16-browser-bookings.md)) |
| Harness | `kit/ea_harness/` | Drives the Claude Code CLI headless (`claude -p`, stream-json, `--resume`, approvals through `ea-world`'s `approval_prompt` tool) and converts its events into the trace. Holds the shared stop-check logic. The independent harness is deferred |
| Eval runner | `kit/ea_evals/` | Runs cases on either harness, simulates Maya, answers approvals, grades with rubrics, computes metrics, writes results and reports |
| Golden dataset and rubrics | `kit/evals/golden/`, `kit/evals/rubrics/`, `kit/evals/components/` | YAML cases, rubrics, component suites, slices, thresholds |
| Report | `kit/ea_evals/report/` | Static HTML report with spider charts and a trace viewer |
| Guide site | `kit/site/` | Static guide built from Markdown into `site/dist/` |
| Starter generator | `kit/tools/make_starter.py`, `kit/starter_overrides/` | Produces `starter/` from `kit/` by removing lesson-built files and planting the Lesson 1 bug |

`kit/` is the reference build with every lesson completed. `starter/` is what students receive.

## Data flow

```
                  ┌──────────── assistant files (CLAUDE.md, .claude/) ────────────┐
                  │                                                               │
 request ─► Claude Code headless adapter (claude -p)  [independent harness: later]
                  │  tool calls (mcp__ea-world__*)                │ tool calls
                  └──────────────► ea_world MCP server ◄──────────┘
                                      │ reads/writes run dir: state/*.json, outputs/, calls.jsonl
                  both write trace.jsonl (06-trace.md)
 ea_evals runner: fresh run dir per trial ─► harness ─► trace + calls + final state ─► graders ─► results ─► report
```

## Repo layout (`kit/`)

```
kit/
  README.md                    quick start for students
  pyproject.toml               one project, three console scripts
  uv.lock
  .env.example                 OPENAI_API_KEY=, SERPAPI_API_KEY=, EA_CLAUDE_MODEL=opus
  docs/SETUP.md                student setup guide (see 15-setup.md)
  .gitignore                   .env, runs/, evals/results/, reports/, site/dist/
  Makefile                     setup, test, smoke, report, site, starter
  CLAUDE.md                    the builder's brief (not the assistant)
  assistant/                   the assistant (see 04-assistant.md)
    CLAUDE.md
    .mcp.json
    .claude/
      settings.json            permissions and Stop/SubagentStop hooks (ea_harness.stopcheck)
      skills/<name>/SKILL.md   scheduling, inbox-triage, meeting-brief, meeting-deck,
                               email-reply, travel-booking, web-research, monday-brief
      agents/<name>.md         scheduler, inbox, briefer, travel, reviewer, planner, challenger, qa
  ea_app/                      the Intuition app (see 14-app.md)
    server.py  sessions.py  static/
  ea_sites/                    mock booking sites on :8766 (see 16-browser-bookings.md)
    server.py  templates/  static/
  world/
    persona.json
    contacts.json
    calendars.json             Maya's events and every internal person's events
    inbox.json
    docs/                      travel-policy.md, q4-forecast-summary.md, customers/*.md
    travel/flights.json, travel/hotels.json, restaurants.json
    cassettes/search.json, cassettes/pages/*.html
    variants/*.yaml
    gold/                      needs_today.json, internal_values.json, distinctive_phrases.json, slots.json
  ea_world/
    __init__.py
    server.py                  MCPServer app (mcp v2) and tool registration
    state.py                   run dir, load/save, world clock, variants
    calendar.py  email.py  contacts.py  docs.py  web.py  travel.py  outputs.py  connectors.py
    verifiers.py               check_event, check_email, check_brief, check_deck, check_booking
    feedback.py                RSVPs, scripted replies, travel-desk flags
    faults.py                  EA_FAULTS parsing and injection
    numbers.py                 number extraction and normalisation for grounding checks
    calllog.py
    templates/deck.html.j2, brief.html.j2
  ea_harness/
    __init__.py
    core.py                    the loop (the ~300 lines students read)
    loader.py                  reads CLAUDE.md, skills, agents, settings
    mcp_client.py              stdio client to ea_world
    approvals.py               approval callback interface
    stopcheck.py               shared stop-check logic (also used by the hook)
    trace.py                   trace writer
    claude_code.py             Claude Code headless adapter (claude -p, stream-json → trace)
  ea_evals/
    __init__.py
    cli.py                     `ea-eval`
    cases.py  rubrics.py  checks.py  judge.py  sim_user.py  approvals.py
    runner.py  metrics.py  variants.py  costs.py  promote.py
    report/build.py, report/templates/*.j2, report/radar.py
  evals/
    golden/<type>/*.yaml, golden/slices.yaml, golden/README.md
    components/llm_dates.yaml, components/tool_use.yaml, components/skill_triggers.yaml
    rubrics/*.yaml
    fixtures/traces/*.jsonl    hand-written traces for testing graders offline
    thresholds.md
  tests/
  site/
    content/*.md  templates/*.j2  static/  build.py
  tools/make_starter.py
  starter_overrides/
  .github/workflows/regression.yml
```

## Tech stack

| Need | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | `zoneinfo` from the standard library; add `tzdata` as a dependency so Windows works |
| Packaging | `uv`, single `pyproject.toml` | Console scripts: `ea-world`, `ea-eval`, `ea-app` |
| Web app | `starlette`, `uvicorn` | Server-sent events via a streaming response; vanilla JS front end, no build step |
| MCP server and client | `mcp>=2.3,<3` (official Python SDK; FastMCP is `MCPServer` in v2) | See the notes in [03-mcp-server.md](03-mcp-server.md) and [05-harnesses.md](05-harnesses.md) |
| Assistant runtime | Claude Code CLI (installed separately, logged in with the user's Claude account) | Driven headless; no Anthropic API key needed |
| Judge and simulated Maya | `openai` (official Python SDK), Responses API with structured outputs | Verify model ids and the structured-output request shape at build time |
| HTTP (live search and fetch) | `httpx` | |
| YAML | `pyyaml` | |
| Templates | `jinja2` | Report, site, deck and brief rendering |
| Markdown | `markdown` | Site build |
| HTML to text (live fetch) | `beautifulsoup4` | |
| Tests | `pytest`, `pytest-asyncio` | |
| Lint | `ruff` | |

No Node, Bun or browser automation is required. Deck overflow is checked against text budgets set by the deck template, not by rendering.

## Configuration

| Variable | Default | Used by |
|---|---|---|
| `OPENAI_API_KEY` | none | Judge and simulated Maya. Provided during the workshop; students' own afterwards |
| `SERPAPI_API_KEY` | none | Live and record modes only. Each student's own key |
| `EA_CLAUDE_MODEL` | `opus` | Claude Code model for the assistant and sub-agents (alias or full id) |
| `EA_JUDGE_MODEL` | `gpt-6.1-sol` | Judge (OpenAI). Verify the id at build time |
| `EA_SIM_MODEL` | `gpt-6-luna` | Simulated Maya's classifier (OpenAI, cheapest tier). Verify the id at build time |
| `EA_BROWSER` | `on` in the app if a browser is available, `off` in evals | Browser booking mode ([16-browser-bookings.md](16-browser-bookings.md)) |
| `EA_APPROVAL_MODE` | set by the runner or app | `script`, `app` or `allow_all` ([05-harnesses.md](05-harnesses.md#approvals---permission-prompt-tool)) |
| `EA_MODE` | `mock` | `mock`, `record` (live search, saved to cassettes), `live` (live search, not saved) |
| `EA_RUN_DIR` | `runs/interactive-<timestamp>` | Tool server state for this run |
| `EA_WORLD_VARIANT` | none | Comma-separated variant names from `world/variants/` |
| `EA_FAULTS` | none | Fault spec ([03-mcp-server.md](03-mcp-server.md#fault-injection)) |
| `EA_SEED` | `0` | Seed for probabilistic faults |
| `EA_MAX_COST_USD` | `5.00` | Runner cost cap per invocation |

## Commands

| Command | Does |
|---|---|
| `make setup` | `uv sync`; copies `.env.example` to `.env` if missing |
| `make test` | All offline tests; no API key needed |
| `make doctor` | Checks every prerequisite ([15-setup.md](15-setup.md#make-doctor)) |
| `make smoke` | One case, one trial, through Claude Code (uses a little of the Claude plan, plus OpenAI for judging) |
| `make app` | Start the Intuition app |
| `make browser` | Start Brave (or Chrome) with remote debugging on port 9222 and a separate profile |
| `ea-world` | Starts the MCP server on stdio (normally launched by `.mcp.json` or the harness) |
| `ea-world reset` | Starts a fresh interactive run dir |
| `ea-app --open` | Start the Intuition app at http://localhost:8765 |
| `cd assistant && claude` | Talk to the assistant in Claude Code |
| `ea-eval run --cases workshop --trials 3` | Run golden cases ([09-runner-and-report.md](09-runner-and-report.md)) |
| `ea-eval component llm_dates --trials 5` | Run a component suite |
| `ea-eval report <run-id> [--compare <run-id> ...]` | Build or rebuild a report |
| `ea-eval promote <run-id> <case-id> <trial>` | Turn a failing trial into a draft golden case |
| `make site` | Build `site/dist/` |
| `make starter` | Build `../starter/` |

## Conventions

- Tool names are bare in traces and graders (`calendar_create`). Claude Code exposes them as `mcp__ea-world__calendar_create`; adapters strip the prefix.
- IDs: people `maya`, `dan.okafor`; events `ev-...`; emails `em-...`; drafts `dr-...`; bookings `bk-...`; options `fl-...`, `ht-...`, `rs-...`; documents `doc-...`; briefs `br-...`; decks `dk-...`.
- Money is stored as integer US dollars.
- Every run directory is self-contained: `state/`, `outputs/`, `calls.jsonl`, `trace.jsonl`, `final_state/`, `meta.json`.
