# Local setup (take-home)

Students take the code home and run everything on their own laptop. There's no cloud deployment. The kit ships a student-facing guide, `docs/SETUP.md`, a short quick start in `README.md`, and a `make doctor` command that checks everything. This file specifies all three.

## The student's path

Download the repo, add two keys, run one command. `make start` checks prerequisites, starts the server, and opens the app (the mock phone and chat); Claude Code runs the assistant behind it. Separate cloud-deployment instructions will come later and aren't part of this kit.

## What runs where

Everything runs on the laptop:

| Piece | Needs |
|---|---|
| The assistant | Claude Code CLI, logged in with the student's own Claude account (Pro or Max) |
| Tool server, runner, report, app, guide | Python 3.12 managed by uv |
| Judge and simulated Maya | `OPENAI_API_KEY` |
| Live web search in the app | `SERPAPI_API_KEY` (evals always use recorded results) |
| Bookings in a visible browser (optional, recommended) | Brave or Chrome, and Node.js 20+ (for `npx chrome-devtools-mcp`). `make start` launches the browser with remote debugging on port 9222 |

No Anthropic API key is needed. Claude usage counts against the student's plan limits, so the guide explains how to keep runs small.

## `docs/SETUP.md` (student-facing; write in full)

Sections, in order:

1. **What you need:**
   - a Mac, Windows (WSL2 recommended) or Linux machine with about 2 GB free
   - a Claude Pro or Max account
   - an OpenAI API key (provided during the workshop; your own afterwards)
   - optionally a free SerpAPI key
   - about 15 minutes
2. **Install Claude Code** (commands from code.claude.com/docs/en/setup, checked 2026-10-04; re-check at build time):
   - macOS, Linux and WSL: `curl -fsSL https://claude.ai/install.sh | bash`, or on a Mac `brew install --cask claude-code`.
   - Windows PowerShell: `irm https://claude.ai/install.ps1 | iex`, or `winget install Anthropic.ClaudeCode`.
   - Log in with `claude auth login`, or run `claude` once. Check with `claude --version`.
3. **Install uv**, using the official installer for each OS (`brew install uv` on Mac also works). Then `uv python install 3.12`.
4. **Get the code:** `git clone <repo-url> intuition-workshop` (or unzip the download), then `cd intuition-workshop`.
5. **Set up:** `make setup`. On Windows without `make`, run `uv sync`, then copy `.env.example` to `.env`.
6. **Add keys:** edit `.env` and set `OPENAI_API_KEY=...` and optionally `SERPAPI_API_KEY=...`. Never commit `.env`; it's already in `.gitignore`.
7. **Check everything:** `make doctor` (below). Fix anything red before continuing.
8. **Start it:** `make start`. This runs the quick checks, starts the server, and opens Intuition in your browser: the onboarding page with the phone and chat beside it, and Claude Code running the assistant in the background. Go through the onboarding, then send "What needs me today?". Stop with Ctrl+C.
9. **Or use the terminal:** `cd assistant && claude`, and ask the same question.
10. **Run your first evals:** `uv run ea-eval run --slice setup-baseline`, then open the report it prints.
11. **Open the guide:** `site/dist/index.html` in your browser.
12. **Keep runs small:** each trial is a full Claude Code conversation and counts toward your plan's usage limits. Start with one trial and the lesson slices; use `--parallel 1` or `2`; raise trials only for the pass^k steps. OpenAI judging costs a few cents per trial.
13. **Troubleshooting:** the table below.
14. **Updating and resetting:**
    - `git pull` and `make setup` to update
    - `ea-world reset` or "Reset world" in the app to start the mock world fresh
    - delete `evals/results/` and `reports/` to clear old runs

### Troubleshooting table

| Symptom | Fix |
|---|---|
| `claude: command not found` | Install Claude Code (step 2) and open a new terminal |
| "Not logged in" or a login prompt during runs | Run `claude` once interactively and log in |
| Usage-limit message from Claude | Wait for the limit window to reset, or use smaller slices and `--parallel 1` |
| `ea-world` doesn't appear in `/mcp` | Run `make doctor`; check that `uv` is on the PATH for the shell Claude Code starts |
| `uv: command not found` | Install uv (step 3) and open a new terminal |
| `OPENAI_API_KEY` missing or invalid | Check `.env`; `make doctor` tests the key with one tiny call |
| Live search returns nothing | Check `SERPAPI_API_KEY`, or switch back to mock search; the free plan is 250 searches a month |
| Times look wrong on Windows | `make setup` installs `tzdata`; rerun it |
| The app won't open on port 8765 | `uv run ea-app --port 8800` |
| A run stopped with "cost cap reached" | Raise `--max-cost` or run a smaller slice |

## `make start`

`make start` = `make doctor --quick` (Claude Code present and logged in, keys set, port free) followed by `uv run ea-app --open`. On Windows without `make`: `uv run ea-start`, a console script that does the same.

## `make doctor`

`uv run python -m ea_evals.doctor` prints one line per check, green or red, with the fix for anything red:

| Check | How |
|---|---|
| Python ≥ 3.11 via uv | `sys.version_info` |
| Dependencies installed | Import every package |
| `claude` on the PATH, and its version | `shutil.which`, `claude --version` |
| Claude Code logged in | One tiny headless call (`claude -p "Reply with OK" --max-turns 1`) returns OK; warn that it uses a little of the plan |
| Tool server starts | Launch `ea-world` over stdio, `list_tools()`, count the tools |
| Claude Code sees the tool server | Headless call in `assistant/` asking it to call `clock_now`; expect the world date |
| OpenAI key works | One minimal call to the judge model |
| SerpAPI key (if set) | One search, which uses 1 of the monthly quota; skipped unless `--with-search` |
| `tzdata` and London/Denver offsets | London is UTC+0 and Denver UTC−6 on 28 Oct 2026 |
| Port 8765 free | Socket bind |
| Browser mode | Node present; Brave or Chrome found; port 9222 answers after `make browser`; the guard denies an outside URL |
| Approval tool round-trip | A tiny headless session where one gated call must be allowed and one denied through `approval_prompt` (confirms the reply shape) |
| Personal Claude Code settings | Warn if `~/.claude/CLAUDE.md` or user-level hooks exist, because they also load in runs |

## `README.md` quick start

Ten lines: what this is, `make setup`, add keys to `.env`, `make doctor`, `make app`, and links to `docs/SETUP.md` and the guide.

## Acceptance

- Following `docs/SETUP.md` from a clean user account on macOS gets to a first assistant reply in the app in under 15 minutes. Repeat on Linux, and on Windows with WSL2. Record the times.
- `make doctor` turns red with the right fix for each of: no `claude`, not logged in, no OpenAI key, port busy.
- `git status` after setup shows no tracked `.env` and no run artifacts.
