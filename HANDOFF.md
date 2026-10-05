# Handoff: Intuition, the executive-assistant evals workshop

Status on 4 October 2026: **design and specification complete; nothing built yet.** The next session starts development.

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

1. [PRD.md](PRD.md): what and why. Requirements A1–A19, B1–B11, C1–C6, D1–D5, E1–E16, F1–F7, G1–G8.
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

## Start of next session: do these first

1. **Environment on this Mac:**
   - Install uv (`brew install uv`), then `uv python install 3.12`. The system Python is 3.9.6, which is too old.
   - Install the Claude Code CLI (`brew install --cask claude-code`, or `curl -fsSL https://claude.ai/install.sh | bash`) and log in with `claude auth login`. It isn't on the PATH today; the VS Code extension doesn't provide it.
   - Node is already installed (`/opt/homebrew/bin/npx`) and Brave is in `/Applications`, which browser bookings need.
   - `cd evals && git init`. A `.gitignore` that excludes `.env` is already in place. **`.env` holds real OpenAI and Google keys; never commit or print it.**
2. **Settle three unknowns with quick live tests before building on them.** Nobody knows the answers yet; the docs disagree or are silent, and the CLI wasn't installed when the spec was written. The builder settles them, not the owner ([spec/13-decisions-log.md](spec/13-decisions-log.md#still-to-verify-at-build-time)). Write a throwaway MCP server with an `approval_prompt` tool and one gated tool, and run `claude -p` with `--permission-prompt-tool`:
   - **The approval tool's reply shape.** Reports differ between `{"behavior": …}` and `{"decision": {"behavior": …}}`.
   - **Whether `.claude/agents/` loads in `-p` mode.** If it doesn't, use `--agents` JSON.
   - **Whether Brave opens `http://skyway.localhost:8766`.** If it doesn't, the mock sites use paths instead (`localhost:8766/skyway`).

   Record the answers in the decisions log.
3. Then follow the build sequence in [spec/00-README.md](spec/00-README.md#build-sequence). Write the checkpoint tests at each step, make them pass, and commit.

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

## Prompt to start the build

> Build the Intuition workshop kit described in `evals/spec/`, in `evals/kit/`. Read HANDOFF.md, the PRD and every spec file first. Start with the environment setup in HANDOFF.md, then settle the two headless unknowns (approval-tool reply shape; whether `.claude/agents/` loads in `claude -p`) with a throwaway test, and record the answers in `spec/13-decisions-log.md`. Then follow `spec/00-README.md`'s build sequence exactly: at each step write the checkpoint tests, make them pass, and commit. Never print or commit `.env`. Load the dataviz skill before the spider charts. Ask before running anything larger than the smoke test against my Claude Code plan. Finish with `reports/acceptance.md` mapping every PRD requirement to its evidence.
