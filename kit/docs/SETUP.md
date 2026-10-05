# Set up Intuition on your laptop

This guide gets you from a clean laptop to your first message with Maya Chen's assistant, in about 15 minutes. Everything runs on your machine against a made-up world: no real email, calendar or bookings are involved.

## 1. What you need

- A Mac, Windows (WSL2 recommended) or Linux machine with about 2 GB free.
- A Claude Pro or Max account. Claude Code runs the assistant on your own plan; no Anthropic API key is needed.
- An OpenAI API key. It runs the grader (the "judge") and the simulated Maya in evals. The workshop provides one; afterwards, use your own.
- Optionally, a free [SerpAPI](https://serpapi.com) key for live web search in the app. Evals always replay recorded search results, so they never need it.
- Optionally, Brave (recommended) or Chrome, plus Node.js 20 or later, to watch bookings happen in a browser window.
- About 15 minutes.

## 2. Install Claude Code

- **macOS, Linux and WSL:** `curl -fsSL https://claude.ai/install.sh | bash`. On a Mac, `brew install --cask claude-code` also works.
- **Windows PowerShell:** `irm https://claude.ai/install.ps1 | iex`, or `winget install Anthropic.ClaudeCode`.

Then log in with `claude auth login` (or run `claude` once and follow the prompts), and check it with `claude --version`.

If your shell says `claude: command not found` after installing, the installer put it in `~/.local/bin`, which isn't on your PATH yet. Add it and open a new terminal:

```zsh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc   # use ~/.bashrc for bash
```

## 3. Install uv

uv manages Python and the kit's packages.

- **macOS:** `brew install uv`, or `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- **Linux and WSL:** `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- **Windows PowerShell:** `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.

Then install Python: `uv python install 3.12`.

## 4. Get the code

```
git clone <repo-url> intuition-workshop
cd intuition-workshop
```

Or unzip the download and `cd` into the folder.

## 5. Set up

```
make setup
```

This installs the packages and creates `.env` from `.env.example`. On Windows without `make`, run `uv sync` and then copy `.env.example` to `.env`.

## 6. Add your keys

Open `.env` in an editor and fill in:

```
OPENAI_API_KEY=sk-...
SERPAPI_API_KEY=...        # optional
```

Never commit `.env`. It's already in `.gitignore`.

## 7. Check everything

```
make doctor
```

It prints one line per check, green or red, with the fix for anything red. Fix the red lines before you continue. The last three checks make short Claude Code calls and use a little of your plan. If you only want the quick checks, run `make doctor ARGS=--quick`.

## 8. Start it

```
make start
```

This runs the quick checks, starts the app, and opens Intuition in your browser: the onboarding page, with the phone and chat beside it. Claude Code runs the assistant in the background. Go through the onboarding, connect Maya's (mock) accounts, then send "What needs me today?". Stop with Ctrl+C.

To watch bookings happen in a browser, run `make browser` first (it opens Brave or Chrome with a separate profile that never touches your own logins), and leave "Book through the browser" on during onboarding.

## 9. Or use the terminal

```
cd assistant
claude
```

Ask the same question. This is the same assistant: the same brief, skills, specialists and tools.

## 10. Run your first evals

```
uv run ea-eval run --slice setup-baseline
```

It runs ten golden cases, one trial each, and prints the path of an HTML report. Open it in your browser.

## 11. Open the guide

Open `site/dist/index.html` in your browser. If it's missing, build it with `make site`.

## 12. Keep runs small

Each trial is a full Claude Code conversation and counts toward your plan's usage limits.

- Start with one trial and the lesson slices (`uv run ea-eval list slices`).
- Use `--parallel 1` or `2` (the default is 2).
- Raise `--trials` only for the pass^k steps.
- `uv run ea-eval estimate --slice <name>` shows a rough cost and time before you run.
- If you hit a usage limit, the run pauses and tells you how to resume: `uv run ea-eval resume <run-id>`.

OpenAI judging costs a few cents per trial; `--max-cost` caps OpenAI spend per run (default $5).

## 13. Troubleshooting

| Symptom | Fix |
|---|---|
| `claude: command not found` | Install Claude Code (step 2), add `~/.local/bin` to your PATH, and open a new terminal |
| "Not logged in", or a login prompt during runs | Run `claude` once in a terminal and log in |
| A usage-limit message from Claude | Wait for the limit window to reset, or use smaller slices and `--parallel 1`; then `uv run ea-eval resume <run-id>` |
| `ea-world` doesn't appear in `/mcp` inside `cd assistant && claude` | Run `make doctor`; check that `uv` is on the PATH for the shell Claude Code starts |
| `uv: command not found` | Install uv (step 3) and open a new terminal |
| `OPENAI_API_KEY` missing or invalid | Check `.env`; `make doctor` tests the key with one tiny call |
| Live search returns nothing | Check `SERPAPI_API_KEY`, or switch back to mock search; the free plan is 250 searches a month |
| Times look wrong on Windows | `make setup` installs `tzdata`; rerun it |
| The app won't open on port 8765 | `uv run ea-app --port 8800` |
| A run stopped with "cost cap reached" | Raise `--max-cost` or run a smaller slice |
| Bookings don't show in a browser | `make browser`, then reload the app; `make doctor` shows whether port 9222 answers |

## 14. Updating and resetting

- `git pull`, then `make setup`, to update.
- `uv run ea-world reset`, or "Reset world" in the app, to start Maya's world fresh.
- Delete `evals/results/` and `reports/` (or `make clean-runs`) to clear old runs.

### Browser commands for Linux and Windows

`make browser` finds Brave or Chrome for you. To start one by hand:

- **macOS:** `"/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" --remote-debugging-port=9222 --user-data-dir="$HOME/.intuition/browser-profile" --no-first-run`
- **Linux:** `brave-browser --remote-debugging-port=9222 --user-data-dir="$HOME/.intuition/browser-profile" --no-first-run` (or `google-chrome …`)
- **Windows:** `"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" --remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\.intuition\browser-profile" --no-first-run`
