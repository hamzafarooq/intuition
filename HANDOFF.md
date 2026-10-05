# Handoff: Intuition, the executive-assistant evals workshop

Status on 5 October 2026: **the kit is built and verified offline; smoke-sized live checks pass.** What's left is the reference run and the lesson slices on the owner's Claude plan (they need the owner's go-ahead), a dry run on a clean laptop, and a few decisions. See "Build status" below.

## What this is

A four-hour workshop kit. Students download a repo, add two keys and run one command. **Intuition** opens in the browser: an onboarding page with a mock phone and a messaging chat docked beside it. Behind it, Claude Code runs Maya Chen's executive assistant on the student's own Claude Code subscription, against a mock world (inbox, calendar, contacts, documents, travel, web).

In the lessons, students build the assistant's parts and evaluate each layer: tools, skills, single agent, multi-agent, harness settings and safety. They use a 63-case golden dataset graded with rubrics, judged by an OpenAI model, and shown as spider charts. Instinct's three reported launch-week failures become the safety suites.

## Settled decisions (latest first)

| Topic | Decision |
|---|---|
| Harness | **Claude Code only for now**, driven headless (`claude -p`, stream-json, `--resume`, approvals through an MCP tool) by the app and the eval runner. Independent harness deferred ([spec/later/](spec/later/independent-harness.md)) |
| Who pays for what | Students have **Claude Code subscriptions**, which run the assistant. The **OpenAI key** (provided during the workshop) runs the judge and the simulated Maya. Students bring a **SerpAPI key**. No Anthropic API key. Google key not used |
| Student path | Download the repo, add the OpenAI and SerpAPI keys to `.env`, run `make start`. The app opens with the mock phone and chat. Local only; cloud deployment instructions come separately, later |
| App | **Intuition**, with its own name, look and wording; nothing copied from Instinct or WhatsApp. Voice is a stretch goal using browser speech |
| Reservations | When Maya asks to book, the assistant visibly works in a **Brave** window on mock booking sites (Skyway, Stays, Tables), using the same Chrome DevTools MCP setup as this environment (Brave on port 9222). The browser only creates holds; the gated booking tools confirm them after approval ([spec/16-browser-bookings.md](spec/16-browser-bookings.md)) |
| Grading | Rubrics as the main tool (outcome, process and team criteria; facts graded in code, judgements by a calibrated judge); spider charts; overclaim rate for self-checks |
| Everything else | [spec/13-decisions-log.md](spec/13-decisions-log.md) |

## Read in this order

1. [PRD.md](PRD.md): what and why. Requirements A1–A19, B1–B12, C1–C6, D1–D5, E1–E17, F1–F7, G1–G9.
2. [spec/00-README.md](spec/00-README.md): how to use the spec, and the build sequence with checkpoints.
3. `spec/01` to `spec/15`, in order. They define the names used everywhere: tools, check functions, trace events.
4. [plan/01-lessons.md](plan/01-lessons.md): the lesson flow the guide site turns into steps.
5. [research/](research/): background and sources.

The spec wins over the plan on build details.

## What gets built

```
evals/
  kit/        reference build, every lesson completed
    assistant/   CLAUDE.md, .mcp.json, .claude/ (skills, agents, settings, hooks): what Claude Code runs
    world/       Maya's mock week, variants, recorded searches, gold labels
    ea_world/    MCP tool server (mcp v2): tools, verifiers, faults, world feedback, approval_prompt tool
    ea_harness/  Claude Code headless adapter (stream-json → trace), stop-check hook
    ea_evals/    runner, rubric engine, OpenAI judge, simulated Maya, metrics, report, doctor
    ea_app/      Intuition: onboarding, mock phone and chat, behind-the-scenes view
    ea_sites/    mock booking websites (flights, hotels, restaurants) for the visible browser
    evals/       golden cases, rubrics, component suites, slices, thresholds, fixtures
    site/        guide site
    docs/SETUP.md, Makefile (setup, doctor, start, test, smoke, site, starter)
  starter/    generated from kit/ by tools/make_starter.py
```

## Build status (5 October 2026)

Everything in the build sequence (spec/00-README.md) is done except step 16 (the reference run and the dry run). Sixteen commits on `master` in `evals/`.

| Area | State | Evidence |
|---|---|---|
| Mock world, tool server (34 tools + `approval_prompt`), verifiers, faults, feedback | Built | `make test` (1,608 offline tests) |
| Assistant (`kit/assistant/`: brief, 8 skills, 8 agents, settings, hooks) | Built | Live: S01 smoke passes; the Stop hook blocks an unchecked write |
| Claude Code adapter, stop check, overlays, browser guard | Built | Recorded stream-json fixtures; live runs |
| Golden dataset (63 cases), 11 rubrics, slices, thresholds, 14 fixture traces | Built | `tests/test_golden_schema.py`, `test_rubrics.py`, `test_checks.py` |
| Runner, judge (OpenAI `gpt-5.6-sol`), simulated Maya (`gpt-5.6-luna`), metrics, report with spider charts | Built | `reports/smoke-s01.html`, `reports/live-r01-browser.html` |
| Component suites (dates, tool use, skill triggers) | Built | One live item each |
| Intuition app (`make start`): live hero, docked phone, pop-out phone, behind-the-scenes view, approval cards, colourful connector tiles | Built | A real 3-turn session: triage, R01 holds made in Brave, booked on "Yes, go ahead." |
| Mock booking sites (Skyway, Stays, Tables) | Built | Live R01 through Brave passes the booking rubric |
| Guide site (`make site`, 11 pages, 14 replays) and starter repo (`make starter`) | Built | `tests/test_site.py`; the planted find_free test fails in `starter/` and passes in `kit/` |
| `make doctor` | Built | All green except a busy port while the app was running |
| Acceptance | `kit/reports/acceptance.md` | 46 verified, 28 verified so far with a larger live run or a person's check to do, 0 failing, 1 exception (independent harness, deferred) |

### Waiting for the owner

1. **The reference run** (63 cases × 3 trials, about 190 Claude Code conversations) and the lesson slices. They use the Claude plan, so they weren't started. Measured so far: about $0.35–$0.50 of plan usage (API-equivalent) per simple trial, about $2 for a browser booking, plus $0.10–$0.20 of OpenAI judging per trial (more than the PRD's "few cents": `gpt-5.6-sol` is $5/$30 per million tokens). Start with `uv run ea-eval run --slice setup-baseline` to see a first report, then `uv run ea-eval run --cases all --trials 3 --run-id reference`.
2. **Real Expedia search in the live demo** (requested 5 October): opening expedia.com from the browser tool was blocked by Claude Code's auto-mode classifier as a real-world transaction. If you want it, decide the scope (search only, no sign-in or checkout), add a permission rule for the Brave MCP, and it can be built as an opt-in instructor-demo mode.
3. The open decisions below (student default model, trials per plan, name check, Instinct sources).
4. A SerpAPI key in `.env` turns on live search in the app (none is set yet).

### Things a person still checks

The dry run from a clean account on macOS, Linux and WSL2, timed (docs/SETUP.md); voice in Chrome and Safari; the side-by-side originality review of the app (no Instinct or WhatsApp names, logos, colours or copy).

### Where to look

- How every decision was made during the build: spec/13-decisions-log.md (rows dated 2026-10-05).
- What each PRD requirement rests on: kit/reports/acceptance.md (`uv run python tools/acceptance.py` regenerates it).
- The live runs: kit/evals/results/ (`smoke-s01`, `live-r01-browser`) and the app session in kit/runs/app-*.

## Skills to load during the build

- **dataviz:** before the spider charts (`ea_evals/report/radar.py`) and any report colours.
- **claude-api:** only if Claude API code is written later (for example the deferred independent harness). The current build calls Claude only through the Claude Code CLI.
- OpenAI code (judge, simulated Maya): check OpenAI's current docs for the model ids and the Responses API structured-output shape. `gpt-6.1-sol` and `gpt-6-luna` were listed on 2026-10-04.

## Costs and limits to expect

- **Building and offline tests:** free. Offline tests use recorded stream-json and fixture traces.
- **Live runs:** they use the developer's Claude Code subscription (usage limits, not per-token billing) plus a few cents of OpenAI judging per trial.
- **Reference run:** 63 cases × 3 trials (about 190 Claude Code conversations), likely spread over several usage windows. Ask before starting it.
- **Students:** each lesson runs small slices with `--parallel 2`, and the runner pauses and resumes at plan limits. The reference run measures how many trials fit in a usage window, and slice sizes follow from that.

## Decisions still open

1. Student default model: `opus` or `sonnet`, after the reference run shows plan usage.
2. How many trials a student's plan comfortably runs in four hours, which sets live trial counts and what gets precomputed.
3. Whether students keep their assistant after the workshop.
4. A name check of the fictional companies and people.
5. Checking the three Instinct incidents against the original reports before they appear in the guide. So far they come from Vellum, a competitor.
