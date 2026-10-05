# Executive Assistant Evals: plan

Status: planning, 2026-10-04. Nothing is built yet.

What to build and why is in [PRD.md](PRD.md). This file and [plan/](plan/) cover the teaching plan. The build-ready specification is in [spec/](spec/00-README.md); where they differ, the spec wins for build details.

Students build an executive assistant in the spirit of Instinct: it reads your inbox and calendar, schedules meetings, books travel, searches the web, writes a short deck, and asks before doing anything it can't take back. It runs on the student's laptop against a mock world, so nobody connects a real Gmail or calendar. Every lesson builds one part and then evaluates it against a golden dataset graded with rubrics, so by the end of four hours each student has an assistant, an eval suite, and a report with spider charts that shows how far to trust it.

It's written for students who are new to agents and have never seen a personal assistant agent. The workshop opens by showing what one is, and each idea (tools, skills, sub-agents, approvals, evals) is introduced when it's built.

## Why this example

- **The domain is in the news.** Instinct, an invite-only assistant you text, launched in summer 2026 and was valued at $10B by September (Sacra). In its first week, users publicly reported three failures: it followed instructions planted in an email, sent an email without checking, and kept inbox data after Google was disconnected. Each one becomes an eval suite here. Details and sources: [research/instinct.md](research/instinct.md).
- **Nobody evaluates assistants on YouTube.** The popular builds (Nate Herk's Claude Code executive assistant, 208K views; his n8n assistant, 165K) test by trying things live. The eval videos use customer support, refunds and travel, and skip harness faults, multi-agent handoffs, pass^k, injection suites, skill-trigger tests and simulated users. Details: [research/youtube.md](research/youtube.md).
- **There's code to reuse.** The multi-agent-course repo has trajectory rules, thresholds, a tool-eval format and a skill evaluator that can be adapted. It has no calendar or inbox world and no end-state checks, which this workshop adds. Details: [research/course-reuse.md](research/course-reuse.md).

## What students build

```
the assistant = files
   ├── CLAUDE.md          brief: who Maya is, house rules, what needs approval
   ├── skills             scheduling · inbox-triage · meeting-brief · meeting-deck   (each with its definition of done)
   ├── sub-agents         scheduler · inbox · briefer · travel · reviewer            (least-privilege tools)
   ├── agent team         Monday brief: planner · challenger · QA
   └── permissions        reads allowed; send, book and create always ask
          │ loaded by
Claude Code (the harness), run headless on the student's subscription
   ▲ driven by: the Intuition app (mock phone + chat) and the eval runner
   │ calls                                            writes
MCP server: ea-world ── mock world, verifiers, approval tool      one trace format
                                                                         │
golden dataset (63 cases, rubrics) ──► eval runner, k trials ──► OpenAI judge ──► report: pass@k, pass^k,
                                                                   per-criterion rates, spider charts,
                                                                   overclaim rate, cost, harness settings
```

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Format | Step-by-step guide site plus a starter repo | Each step is a prompt pasted into Claude Code, with a list of what to expect |
| Data | Synthetic seeded world, one fictional executive's week | Traps can be planted on purpose and gold labels are known, so graders are exact |
| Tools | Local MCP server in Python with mock and live modes, plus verifier tools | Same tool names in both modes: evaluate in mock, demo in live |
| World feedback | Attendees accept or decline, contacts reply, simulated Maya approves, edits, denies or corrects | The assistant can learn it got something wrong without explicit feedback, and that's testable |
| Test set | Golden dataset of 63 cases; 20 in the workshop | One definition of "good", run automatically and repeatedly |
| Grading | Rubrics as the main tool: outcome, process and team criteria; each criterion graded by code where it's a fact, by a calibrated model judge where it's a judgement | Agent runs vary and many paths are right; rubrics grade properties, not one path |
| Results display | Spider charts per run, task type and team, up to three runs overlaid, exact numbers beside them | Shows the assistant's shape and how fixes change it |
| Self-checks | Definition of done in each skill, verifier tools, read-back after writes, a reviewer sub-agent, and a stop check in the harness | Grounded in state and code, not the model's opinion of itself; measured by overclaim rate |
| Harness | Claude Code only for now, driven headless by the app and the runner on each student's subscription; independent harness later | No Anthropic API key needed; students already have Claude Code subscriptions |
| Search | `web_search` and `web_fetch` tools on SerpAPI, each student's own key; live in the app, recorded results in evals | Replayed results make trials comparable and keep within the 250-search free plan |
| Models | Assistant: Claude Code `opus`. Judge and simulated Maya: OpenAI (`gpt-6.1-sol`, `gpt-6-luna`; confirm ids) | A different family as judge avoids self-grading |
| Keys | OpenAI key provided during the workshop; students bring SerpAPI; no Anthropic API key; Google key unused | Owner decision |
| Setup | Download the repo, add keys, `make start` (checks, server, app); local only; cloud deployment later and separate | [spec/15-setup.md](spec/15-setup.md) |
| Guide site | Not public; ships inside the starter repo and opens locally | Shared with attendees only for now |
| App | **Intuition**: onboarding page plus a messaging simulator docked on the side, with a behind-the-scenes view; voice as a stretch | Makes the assistant feel like a product and shows the machinery live ([spec/14-app.md](spec/14-app.md)) |
| Phone | Deferred | Students use the app on the laptop for now; options are in [plan/04-phone.md](plan/04-phone.md) |

## The four hours

| Block | Time | Build | Evals |
|---|---|---|---|
| Setup | 20m | What a personal assistant agent is; brief, world, MCP server | First request; baseline golden run and first spider chart |
| 1. Tools | 35m | Fix tool descriptions and a time-zone bug | Tool unit tests; right tool vs right arguments; description A/B; date reasoning with pass@k vs pass^k |
| 2. Skills | 35m | Four skills, each with its definition of done | Skill-trigger set; with vs without the skill |
| 3. Sub-agents | 50m | Orchestrator, four specialists, verifiers, reviewer, stop check | Writing rubrics; outcome and process rubrics; simulated Maya; judge calibration; overclaim rate |
| Break | 10m | | |
| 4. Human gate | 35m | Approval for send, book, create | The three Instinct incidents as suites: unapproved action, injection, disconnect; plus privacy |
| 5. Agent team and harness | 35m | Monday-brief team; how Claude Code runs the assistant | Team rubrics; single vs multi-agent; fault injection; harness settings (hooks, sub-agents, model) |
| Wrap | 10m | Report | Spider charts, baseline vs final; failures become golden cases |

Lesson details: [plan/01-lessons.md](plan/01-lessons.md).

## Plan files

- [plan/01-lessons.md](plan/01-lessons.md): each lesson's steps, what to expect, evals, checks, files produced
- [plan/02-world-and-tools.md](plan/02-world-and-tools.md): persona, clock, people, planted traps, world feedback, data files, MCP and verifier tools, modes, fault injection
- [plan/03-evals.md](plan/03-evals.md): golden dataset, rubrics, scoring, spider charts, self-check evals, suites, thresholds, metrics, harnesses, runner, report
- [plan/04-phone.md](plan/04-phone.md): deferred; research on phone access kept for later
- [research/](research/): Instinct, YouTube survey, what to reuse from the multi-agent-course repo
- [spec/](spec/00-README.md): the build specification, in build order

## Build order

The detailed sequence with checkpoints is in [spec/00-README.md](spec/00-README.md#build-sequence). In short:


1. World data, world feedback rules and gold labels (`world/`).
2. MCP server in mock mode with verifier tools and unit tests (`mcp/ea_world/`).
3. Golden dataset, rubrics and definitions of done (`evals/golden/`, `rubrics/`).
4. Assistant files: brief, skills, sub-agents, reviewer, team, permissions, stop hook (`.claude/`).
5. Independent harness and the shared trace format (`harness/`).
6. Eval runner, thresholds and report with spider charts (`evals/`, `reports/`).
7. Live mode: search provider, recorded search cassettes, browser booking demo with a recorded replay.
8. Guide site (`site/`).
9. Dry run on a clean machine, every step timed, plus an instructor reference run of all 63 cases with costs.

## Done when

- Every MCP tool and verifier has unit tests, and they pass.
- The runner completes the 20-case subset on both harnesses and writes the report with spider charts.
- A deliberately weak setup fails the right rubric criteria: no approval rule, no skill, a vague tool description, no idempotency key, self-checks off.
- With self-checks on, the overclaim rate is lower than with them off.
- Every judged rubric criterion reaches 85% TPR and TNR against human labels.
- The guide site works at phone and desktop widths.
- One student's workshop run costs a known amount and fits in the lesson time.

## Open items

- Check the three Instinct incidents against primary sources (TechCrunch, Forbes, the users' own posts) before they go on slides. So far they come from Vellum, which sells a competing assistant.
- Measure in the reference run how many trials fit in a Pro or Max plan's usage window, and size each lesson's live runs to it; precompute the most expensive comparisons.
- Decide whether students default to `opus` or `sonnet`.
- Create the workshop's OpenAI project key with a spend limit, and revoke it afterwards.
- Name-check the fictional company and people so none matches a real firm.
