# Build specification

These documents specify the whole kit in enough detail to build it in one pass without further design decisions. [PRD.md](../PRD.md) says what and why; [PLAN.md](../PLAN.md) and [plan/](../plan/) give the teaching plan; this folder says exactly what to build.

## How to use this spec

1. Read the files in order. Later files refer to names defined earlier: tool names, check functions, trace events.
2. Build in the order of [the build sequence](#build-sequence). Each step has a checkpoint; don't start the next step until it passes.
3. Where a spec says **verify at build time**, read the named documentation before writing that code. Don't guess APIs.
4. Where this spec and the PRD disagree, this spec wins for build details and the PRD wins for intent. Record any change in [13-decisions-log.md](13-decisions-log.md).

## Files

| File | Covers |
|---|---|
| [01-architecture.md](01-architecture.md) | Components, repo layout, tech stack, dependencies, configuration, commands, conventions |
| [02-world.md](02-world.md) | The mock world: clock, people, calendars, inbox, documents, catalogs, web cassettes, variants, gold labels, world feedback |
| [03-mcp-server.md](03-mcp-server.md) | Every tool: inputs, outputs, errors, side effects, gating, logging, faults, modes, verifier algorithms |
| [04-assistant.md](04-assistant.md) | CLAUDE.md, skills, sub-agents, the team pattern, permissions, hooks, the status line, approval policy |
| [05-harnesses.md](05-harnesses.md) | The harness: Claude Code driven headless (approvals through an MCP tool, stream-json to trace, sessions, stop check, configuration variants). The independent harness is deferred ([later/](later/independent-harness.md)) |
| [06-trace.md](06-trace.md) | The trace event schema, the server call log, and the results file |
| [07-golden-dataset.md](07-golden-dataset.md) | The case schema and all 63 cases, slices and the workshop subset |
| [08-rubrics.md](08-rubrics.md) | Rubric schema, the check-function library, every rubric's criteria, the judge, scoring |
| [09-runner-and-report.md](09-runner-and-report.md) | Runner CLI and flow, simulated Maya, approvals in evals, variants, metrics, spider charts, the report, CI |
| [10-component-suites.md](10-component-suites.md) | Date reasoning, tool-use and skill-trigger suites |
| [11-guide-site.md](11-guide-site.md) | Guide site structure, content format, components, lesson content, the starter repo |
| [12-acceptance.md](12-acceptance.md) | Tests, fixture traces, acceptance checks mapped to PRD requirement IDs, the dry run |
| [13-decisions-log.md](13-decisions-log.md) | Decisions made while writing the spec, and changes during the build |
| [15-setup.md](15-setup.md) | Local setup for students taking the code home: `docs/SETUP.md`, `make doctor`, troubleshooting |
| [16-browser-bookings.md](16-browser-bookings.md) | Reservations made visibly in a Brave window on mock booking sites, with holds confirmed only through the gated booking tools |
| [14-app.md](14-app.md) | The Intuition app: onboarding page, docked messaging simulator, behind-the-scenes view, approvals, voice |

## Build sequence

| # | Build | Spec | Checkpoint |
|---|---|---|---|
| 1 | Repo skeleton, `pyproject.toml`, config, CLI stubs | 01 | `uv run pytest` runs (no tests yet) and `ea-world --help` works |
| 2 | World data, variants, gold labels | 02 | `tests/test_world.py` passes, including the brute-force check that S01's slot is unique |
| 3 | MCP server: tools, state, logging, faults, feedback, verifiers | 03, 06 | `tests/test_tools.py`, `test_verifiers.py`, `test_faults.py`, `test_feedback.py` pass; server lists all tools over stdio |
| 4 | Trace schema and check-function library | 06, 08 | `tests/test_checks.py` passes on fixture traces |
| 5 | Golden dataset, rubrics, slices | 07, 08 | `tests/test_golden_schema.py` passes; coverage tables complete |
| 6 | Assistant files in `assistant/`: CLAUDE.md, skills, sub-agents, settings, hooks; the builder's CLAUDE.md at the kit root | 04 | Claude Code started in `assistant/` lists the skills and agents and can't use Read or Bash; the stop hook blocks a "done" without checks (manual check) |
| 7 | Approval tool and stop-check hook | 03, 05 | `tests/test_approval_tool.py` and `test_stopcheck.py` pass |
| 8 | Claude Code headless adapter | 05 | `tests/test_claude_adapter_offline.py` passes on recorded stream-json; one golden case runs end to end through `claude -p` and writes a valid trace |
| 9 | Runner, judge, simulated Maya, metrics | 09 | `tests/test_runner_offline.py` passes; fixture traces grade as expected |
| 10 | Report with spider charts | 09 | Report renders from fixture results; charts follow the rules |
| 11 | Component suites | 10 | Each suite runs offline with fixtures and live with one item |
| 12 | The Intuition app | 14 | `tests/test_app.py` passes; visual check at desktop and phone widths |
| 12b | Mock booking sites, holds, browser guard, browser mode | 16 | `tests/test_sites.py` and `test_browser_guard.py` pass; a live R01 booking through Brave passes the booking rubric |
| 13 | Live smoke run and `make doctor` | 12, 15 | `make doctor` all green; `make smoke` passes through Claude Code; one conversation works in the app |
| 14 | Starter repo generation | 11 | `starter/` builds; the planted tool test fails there and passes in `kit/` |
| 15 | Guide site | 11 | `site/dist/` opens locally; every lesson page renders; progress survives reload |
| 16 | Reference run and dry run | 12 | All acceptance checks recorded in `reports/acceptance.md` |

## Conventions

- Python 3.11+, managed with `uv`. All code type-annotated; `ruff` for lint and format.
- Times are stored as ISO 8601 with offset (`2026-10-28T11:00:00-06:00`) and converted with `zoneinfo`. Never use fixed UTC offsets except in the deliberate starter bug.
- All world domains use the reserved `.example` top-level domain, so nothing in the world can point at a real company.
- No secrets in the repo. Keys come from environment variables or a git-ignored `.env`.
- Logs go to stderr. MCP stdio servers must never print to stdout.
- Every generated file under `runs/`, `evals/results/` and `reports/` is git-ignored.
