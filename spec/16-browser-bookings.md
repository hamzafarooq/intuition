# Bookings through a visible browser

When Sarah asks the assistant to make a reservation, it can do the work in a real **Brave** window that everyone can watch. It browses mock booking websites, picks options and fills in forms. The commitment still goes through the gated `travel_book` or `restaurant_book` tool after Sarah approves, so the approval gate and the end-state graders are unchanged.

This mode is **on for mock-phone sessions** (an onboarding option, on by default when a browser is available) and **off in evals by default**. The `browser=on` variant runs a few booking cases through the browser, to show that the graders check the world, not the path.

## How it fits together

```
Sarah: "Book my Chicago trip"
  └─ travel specialist (Claude Code sub-agent)
       ├─ browser tools (Chrome DevTools MCP → Brave on port 9222, visible window)
       │    http://skyway.localhost:8766       search, select fare, passenger details → review page with HOLD-xxxx
       │    http://stays.localhost:8766        hotel search, room, guest details      → HOLD-yyyy
       │    http://tables.localhost:8766       restaurant time, party, name           → HOLD-zzzz
       │    (take_screenshot at results, review and confirmation → outputs/browser/)
       └─ returns the plan: hold ids, prices, total, policy notes
  main assistant: shows the plan → "STATUS: waiting — reply yes to go ahead"
Sarah: "Yes, go ahead."
  main assistant: travel_book(hold_id=…) for each hold   (gated → approval card or script)
                  check_booking(…) → opens the confirmation page in Brave to show "Confirmed"
```

## Mock booking sites (`kit/ea_sites/`)

- A Starlette app on port **8766**, routed by host name: `skyway.localhost` (flights), `stays.localhost` (hotels), `tables.localhost` (restaurants). Chromium-based browsers resolve `*.localhost` to the local machine. **Verify in Brave**; if it doesn't, fall back to paths (`localhost:8766/skyway`).
- Started by `make start` and by the runner when `browser=on`.
- **Data** comes from the current run's world state (the run directory from `runs/CURRENT`), so prices and availability match the catalog exactly. Variants such as `harbor-sold-out` apply.
- **Design:** plain, believable, fictional brands, made for the browser tools. Semantic HTML: real `<form>`, `<label for>`, `<button>` with text, one main action per page, no pop-ups, no CAPTCHAs, so the accessibility snapshot gives clean element ids.

| Site | Pages |
|---|---|
| Skyway (flights) | Search (from, to, date) → results (flights for that date and route: class, times, price, tags such as "Recommended") → passenger details (name, email) → **review**: shows the hold reference, the fare, and "Held for 30 minutes; your assistant will confirm" → confirmation page `/booking/<booking_id>` once confirmed |
| Stays (hotels) | Search (city, check-in, check-out) → results (the three Chicago hotels with nightly price, distance, tags) → guest details → **review** with hold → confirmation |
| Tables (restaurants) | Search (city, date, time, party size) → available times → name and phone → **review** with hold → confirmation |

There's no payment step. A review page creates a **hold**, never a booking.

## Holds and confirmation (tool-server changes)

- New state file `state/holds.json`: `{hold_id, kind (flight|hotel|restaurant), option_id, details (travellers, dates, party, time), total_usd, created_at, status (held|confirmed|expired)}`. Holds expire after 30 minutes of world time; the clock is fixed, so in practice they don't.
- `travel_book` and `restaurant_book` accept **either** `option_id` (direct mode, as before) **or** `hold_id` (browser mode). Booking from a hold creates the booking with the hold's details, marks the hold confirmed, and the site's confirmation page then shows "Confirmed" and the booking id.
- New read-only tool `holds_list()` so the assistant can see what it has held.
- The sites append page views, form submissions and holds to `<run_dir>/site.jsonl`. The adapter merges them into the trace as `site_event` events (type `page_view`, `form_submit`, `hold_created`).
- Graders are unchanged: they read bookings in the final state. With `browser=on`, one extra process criterion checks that a hold was created on the site before each booking.

## Browser tools for the assistant

- **Server:** the same Chrome DevTools MCP server already used in this environment:
  ```json
  "browser": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "chrome-devtools-mcp@latest", "--browserUrl", "http://127.0.0.1:9222"]
  }
  ```
  Added to the session's generated `mcp.json` (and `assistant/.mcp.json`) only when browser mode is on. Pin the version at build time instead of `@latest`.
- **Browser:** Brave (or Chrome) started with remote debugging and a separate profile, so it never touches the user's own cookies or logins. `make browser` runs, on macOS:
  `"/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" --remote-debugging-port=9222 --user-data-dir="$HOME/.intuition/browser-profile" --no-first-run http://skyway.localhost:8766`
  Equivalents for Linux and Windows, and a Chrome fallback, go in `docs/SETUP.md`. `make start` launches it automatically when browser mode is on and port 9222 isn't already in use.
- **Allowed browser tools:** `new_page`, `navigate_page`, `select_page`, `list_pages`, `take_snapshot`, `click`, `fill`, `fill_form`, `press_key`, `hover`, `wait_for`, `take_screenshot`, `close_page`. **Denied:** `evaluate_script`, `upload_file`, `handle_dialog`, `emulate`, the performance and Lighthouse tools, and anything else.
- **Who gets them:** only the `travel` sub-agent (its `tools` list) and the main assistant (for showing confirmation pages). Browser tools are in `permissions.allow`; nothing they do is a commitment.

### Guard hook (harness guardrail)

`PreToolUse` hook with matcher `mcp__browser__.*` runs `python -m ea_harness.browser_guard`:
- `new_page` and `navigate_page` are allowed only for `http://*.localhost:8766/…` (and the path fallback). Anything else is denied with "The booking browser only visits the workshop's booking sites."
- Every browser call is logged to `<run_dir>/browser.jsonl`.

This shows a harness-level guardrail: the model is never trusted to stay on the right sites.

## Skill and agent changes

`travel-booking` skill, browser mode section (included when `EA_BROWSER=on`; the CLAUDE.md note tells the assistant whether browser mode is on):

1. Read the policy and the event details as before.
2. Hand off to the `travel` specialist with the trip details.
3. The specialist opens `skyway.localhost`, `stays.localhost` or `tables.localhost` in the browser and searches. It chooses compliant options using what the pages show, fills in Sarah's details from her contact record, and stops at each review page. It takes screenshots of the results and review pages (`take_screenshot` with a file path under `outputs/browser/`), and returns hold ids, prices, the total and any policy issues.
4. Show Sarah the plan and wait for an explicit yes.
5. `travel_book(hold_id=…)` and `restaurant_book(hold_id=…)`, then `check_booking`, then open each confirmation page in the browser and screenshot it.

The definition of done adds: every booking came from a hold created on the site; confirmation pages were shown.

## Mock phone changes

- Onboarding option "**Book through the browser** (opens Brave)", on by default when port 9222 answers or Brave is installed. If neither, show it off, with "Install Brave or Chrome to see bookings happen in a browser."
- A banner while browser steps run: "Working in the browser…", with a "Bring browser to front" hint.
- The behind-the-scenes timeline shows browser steps (`navigate`, `click`, `fill`) with the page URL, plus thumbnails of the screenshots saved under `outputs/browser/`.
- Suggested layout for demos: the mock phone on the left half of the screen, Brave on the right half. `make browser` can open Brave with a window size and position flag for this.

## Runner and evals

- Variant `browser=on`: starts `ea_sites`, requires the browser on port 9222 (skips the trial with a clear message if it's missing), forces `--parallel 1` (one shared browser), and adds the browser MCP and guard hook to the overlay.
- Slice `browser-demo`: R01 and R04 with `browser=on`, one trial each. Lesson 4 or the wrap shows that the same rubric passes or fails the browser run exactly as it does the direct run.

## Tests

- `tests/test_sites.py`: every page renders for each variant; forms create holds with the right prices; `harbor-sold-out` hides the block; a hold then `travel_book(hold_id)` gives a booking and a "Confirmed" page; HTML has labels for every input.
- `tests/test_browser_guard.py`: allowed and denied URLs; logging.
- **Live check:** with Brave on port 9222, a scripted Claude Code session books R01 through the browser and passes the booking rubric; screenshots exist; the guard denies a navigation to an outside site.

## Instructor demo: real-site search (2026-10-15)

An opt-in demo, for instructors only: the assistant compares **live** prices on real travel sites. It is off by default everywhere. Students' default path, the mock phone's ordinary browser mode and every eval are unchanged.

- **Switch:** `EA_REAL_SITES=on`, set by `make demo-real` (which runs `ea-start --real-sites`); `ea-app --real-sites` also works. The mock phone then applies it to Claude Code sessions with "Book through the browser" on, and shows a **Real-site search (instructor demo)** badge in the nav bar, a note on the onboarding page, and "real-site search" in the session's trace line. Without the switch, `POST /api/sessions` with `real_sites: true` is refused (400).
- **Scope: search and read only.** The browser may open the allowlisted sites (expedia.com; google.com/travel for flights and hotels; Kayak listed but commented out), type places, dates and the number of travellers, and read results and prices. Real sites give **quotes only**. Any hold or booking still happens on the mock sites and through the gated booking tools after Sarah approves.
- **Guard (the safety boundary):** `browser_guard --real-sites`, with all limits in one file, `ea_harness/real_sites.py`. On real pages it denies: any address off the allowlist (mock sites stay allowed as before); sign-in, account, cart, checkout, payment and booking addresses (path and query patterns); fills of fields that look like personal data (email, password, name, phone, card, address, date of birth, passport, loyalty number…) and values that look like personal data (an email address, a phone or card number, the name or email of anyone in the world); clicks on elements whose text suggests committing (Book, Reserve, Hold, Sign in, Checkout, Pay, Continue, Select and continue to traveller details…) or that link to a blocked address; key presses other than navigation keys; snapshots saved to a file; and every browser tool outside the ordinary list. A click or fill names an element only by uid, so a `PostToolUse` hook (`browser_guard --post --real-sites`) records every snapshot the model sees (`<run_dir>/browser_pages.json`, reset each turn) and the `PreToolUse` hook looks the uid up there; an unknown uid is denied. A tripwire reads the clean browser's open tabs (`/json/list` on port 9223) before every click, fill or key press: while any tab sits on a page that isn't allowed (reached by a click or a redirect), interaction is denied until the model goes back or closes it, and the `PostToolUse` hook tells the model at once. In this mode the guard fails closed. Denials return a clear message to the model, go to `browser.jsonl`, and appear in the trace as `browser_step` `blocked` (flags as `off_allowlist`).
- **Clean browser:** never the user's own profile. `ea_harness/clean_browser.py` (also `tools/browser.py --real`) starts Brave or Chrome on its own debugging port, **9223**, with a fresh `--user-data-dir` in a temporary folder (no cookies, no saved logins), refuses to reuse a port 9223 it didn't start, and deletes the profile when the mock phone stops (`tools/browser.py --real --close` cleans up by hand). In this mode the browser MCP server points at port 9223, so the mock sites are used in the same clean browser.
- **Permissions:** the overlay, only in this mode, writes exact allow rules for the browser tools the demo needs (`mcp__browser__new_page`, `navigate_page`, `select_page`, `list_pages`, `take_snapshot`, `click`, `fill`, `fill_form`, `press_key`, `hover`, `wait_for`, `take_screenshot`, `close_page`; no wildcard), passes the same list as `--allowedTools`, and denies every other chrome-devtools-mcp tool. Every call the demo makes therefore resolves on a rule before auto mode's classifier would see it; the guard hook, whose deny overrides any allow rule, stays the real boundary.
- **Instructions:** `<!-- realsites:start -->` … `<!-- realsites:end -->` blocks in `assistant/CLAUDE.md`, the `travel-booking` skill and the `travel` agent, nested inside the browser blocks and stripped unless the demo is on; the adapter's appended system prompt says the demo is on. The assistant says it is comparing live prices, passes no personal details, reports quotes (site, airline or hotel, times, price; live prices change), stops at bot checks, captchas, consent or sign-in walls and tries the other site, and never books, holds or signs in on a real site.
- **Evals stay deterministic:** `browser=real` is refused by `variants.parse` and by the runner before any trial starts ("instructor-only real-site search demo, and evals refuse it"). The runner never passes the demo to the overlay or the adapter, so `EA_REAL_SITES` in the environment has no effect on runs.
- **Tests:** `tests/test_real_sites.py` (offline): allowed search URLs, blocked foreign, sign-in and checkout addresses, personal-data fills, "Book" and "Sign in" clicks, unknown uids, keys, the tab tripwire, the hooks end to end, the overlay and the mock phone's switch, mock-site behaviour unchanged when the mode is off, and the eval refusal.

## Requirements this adds (PRD)

- **B12:** mock booking sites with holds, confirmable only through the gated booking tools.
- **G9:** when Sarah asks for a reservation on the mock phone, the assistant visibly works in a Brave window on the mock sites. Bookings still need approval, and screenshots appear behind the scenes.
- **E17:** the `browser=on` variant grades browser bookings with the same rubrics.
