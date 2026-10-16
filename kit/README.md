# Intuition: build an executive assistant, then prove how far you can trust it

A four-hour workshop kit. You build Sarah Smith's executive assistant in Claude Code, text it from a mock phone, and test every layer with a golden dataset of 63 cases graded by rubrics.

1. Install Claude Code and log in, and install uv: [docs/SETUP.md](docs/SETUP.md).
2. `make setup`
3. Add `OPENAI_API_KEY` (and optionally `SERPAPI_API_KEY`) to `.env`.
4. `make doctor`, and fix anything red.
5. `make start` opens the mock phone; send "What needs me today?".
6. `uv run ea-eval run --slice setup-baseline` runs your first evals.
7. The guide is in `site/dist/index.html` (`make site` builds it).

Intuition is the assistant Sarah texts; in the workshop you text it from a mock phone in your browser (localhost:8765). Everything is fictional and runs on your laptop. No real accounts are touched.

## Optional: watch the assistant in Phoenix

`make trace` sends a mock-phone session or an eval run to a local [Arize Phoenix](https://phoenix.arize.com) as OpenInference spans, so you can see the same trace in an industry observability tool. Each session or trial becomes one trace: the request (and its grade, for eval runs) at the root, then every model step, tool call and sub-agent on the trace's own timeline.

1. In a second terminal: `make phoenix`. It runs `uvx arize-phoenix serve`, so nothing is added to the kit's own environment. Docker works too: `docker run -p 6006:6006 arizephoenix/phoenix`.
2. Text Intuition from the mock phone, then `make trace`. That sends the newest session under `runs/`. For an eval run: `make trace RUN=<run-id>`.
3. Open http://localhost:6006 and choose the `intuition` project. Blocked or failed tool calls show as errors; each eval trial's grade shows as `pass` and `overclaim` annotations.

Under the hood this is `uv run --extra phoenix ea-eval phoenix latest`. Add `ARGS=--dry-run` to print the span tree without sending anything. `--endpoint` (or `PHOENIX_COLLECTOR_ENDPOINT`) and `--project` choose where the spans go. The `phoenix` extra adds only OpenTelemetry and OpenInference, so a plain `uv sync` is unchanged.

## Instructor demo: real-site search

Off for students and in every eval. Turn it on only to show the assistant comparing live prices:

1. `make demo-real` (the same as `EA_REAL_SITES=on uv run ea-start --real-sites`). It opens a clean, throwaway Brave or Chrome window on port 9223 with a brand-new profile: no cookies, no saved logins, never your own profile. The profile is deleted when the mock phone stops (or run `uv run python tools/browser.py --real --close`).
2. The mock phone shows a **Real-site search (instructor demo)** badge. Leave "Book through the browser" on and ask, for example, "What do flights from Denver to Chicago on 4 November cost? Just compare, don't book."

What it can do: open expedia.com and google.com/travel (flights and hotels), type places, dates and the number of travellers, and read results and prices. Real sites give quotes only. Holds and bookings still happen on the workshop's mock sites, through the gated booking tools, after Sarah says yes.

What it can't do: the browser guard (`ea_harness/browser_guard.py --real-sites`) blocks any other site; sign-in, account, cart, checkout, payment and booking pages; typing names, emails, phone, card or loyalty numbers; and clicks on Book, Reserve, Sign in, Checkout and the like. Blocked steps appear in the trace. The allowlist and patterns live in one place: `ea_harness/real_sites.py`.

Real sites change their layout and can show bot checks or captchas; the assistant is told to stop and try the other site, never to get around them. `ea-eval` refuses `browser=real`, so evals stay deterministic.
