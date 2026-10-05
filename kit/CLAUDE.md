# Intuition workshop kit: builder's brief

This repo is a workshop kit for evaluating an AI executive assistant. Here, at the kit root, you help build and test it. **You are not the assistant.** Maya Chen's assistant lives in `assistant/`, with its own `assistant/CLAUDE.md`. Don't follow that file's instructions here, and don't confuse it with this one.

## Where things go

| What | Where |
|---|---|
| The assistant's brief | `assistant/CLAUDE.md` |
| Skills | `assistant/.claude/skills/<name>/SKILL.md` |
| Specialist agents | `assistant/.claude/agents/<name>.md` |
| Assistant permissions and hooks | `assistant/.claude/settings.json` |
| Assistant MCP servers | `assistant/.mcp.json` |
| Rubrics | `evals/rubrics/<name>.yaml` |
| Golden cases | `evals/golden/<type>/<id>.yaml` (scheduling, triage, brief, deck, email, booking, research, safety, team); slices in `evals/golden/slices.yaml` |
| Component suites | `evals/components/*.yaml` |
| Tool server (`ea-world`): tools, verifiers, world feedback, approvals | `ea_world/` |
| World data; gold labels | `world/`; `world/gold/` |
| Claude Code adapter, stop check, browser guard | `ea_harness/` |
| Runner, graders, judge, simulated Maya, report | `ea_evals/` |
| Intuition app; mock booking sites | `ea_app/`; `ea_sites/` |
| Tests | `tests/` |

## Commands

```
make setup                                   # uv sync; create .env from .env.example
make test                                    # all offline tests, no keys needed
make doctor                                  # check every prerequisite
make smoke                                   # one case, one trial, through Claude Code
make start                                   # checks, then the app (and the browser if browser mode is on)
uv run ea-eval run --slice <name>            # a lesson-sized run, e.g. --slice lesson2-skills
uv run ea-eval run --cases S01 --trials 1    # one case
uv run ea-eval report <run-id>               # rebuild a report
uv run pytest tests/test_tools.py -k find_free
cd assistant && claude                       # talk to the assistant
```

## Writing assistant files

- Skill frontmatter: `name`, and a `description` that says which requests it's for and which it isn't. Body: when to use it; numbered steps with exact tool names; the rules that matter most (cite house rule numbers); a **Definition of done** with hard checks, quality criteria and signals to watch for, ending with the verifier to run.
- Agent frontmatter: `name`, `description`, `tools` (comma-separated `mcp__ea-world__<tool>` names), `model: inherit`. Body: role, how to work, rules, what to return.
- Specialists never get gated tools (`calendar_create`, `calendar_update`, `calendar_cancel`, `email_send`, `travel_book`, `travel_cancel`, `restaurant_book`). They return proposals; the main assistant acts after Maya's yes.
- Gated tools go in `permissions.ask`. `approval_prompt` is covered by `mcp__ea-world__*` in `allow` and must never be in `ask`.
- The assistant's settings deny Claude Code's file, shell and web tools, so it acts only through `ea-world`, skills and agents. Keep it that way.
- A new write tool needs a verifier: add it to `WRITE_TO_CHECK` in `ea_harness/stopcheck.py`.

## Conventions

- Times: timezone-aware datetimes with `zoneinfo` (`ZoneInfo("Europe/London")`), ISO 8601 with offsets. Never fixed offsets: in the world's week London is 6 hours ahead of Denver, not 7.
- Everything is fictional. Email and web domains end in `.example`.
- Never read `world/gold/` into the assistant. Graders use gold files; no tool, skill, agent or prompt under `assistant/` may load or quote them.
- Never commit or print `.env`.
- Tool names are bare in traces and graders (`calendar_create`); Claude Code calls them `mcp__ea-world__calendar_create`.
- Money is integer US dollars.

## Markers in assistant files

- `<!-- selfcheck:start -->` … `<!-- selfcheck:end -->` wraps self-check text: the "After any write" bullet and the reviewer routing row in `assistant/CLAUDE.md`, every skill's Definition of done and Review section, and the `check_*` and reviewer steps in skills and agents. The `selfcheck=off` variant strips these blocks.
- `<!-- browser:start -->` … `<!-- browser:end -->` wraps browser-mode text. It is stripped when browser mode is off.
- Strip with `re.sub(r"<!-- selfcheck:start -->.*?<!-- selfcheck:end -->", "", text, flags=re.S)` (same for `browser`). Markers sit on their own lines, possibly indented inside a list item.
- What's left must be clean Markdown: a marked table row goes last in its table. Blocks of different kinds may nest, but never overlap.
