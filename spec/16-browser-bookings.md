# Bookings through a visible browser

When Maya asks the assistant to make a reservation, it can do the work in a real **Brave** window that everyone can watch. It browses mock booking websites, picks options and fills in forms. The commitment still goes through the gated `travel_book` or `restaurant_book` tool after Maya approves, so the approval gate and the end-state graders are unchanged.

This mode is **on in the app** (an onboarding option, on by default when a browser is available) and **off in evals by default**. The `browser=on` variant runs a few booking cases through the browser, to show that the graders check the world, not the path.

## How it fits together

```
Maya: "Book my Chicago trip"
  └─ travel specialist (Claude Code sub-agent)
       ├─ browser tools (Chrome DevTools MCP → Brave on port 9222, visible window)
       │    http://skyway.localhost:8766       search, select fare, passenger details → review page with HOLD-xxxx
       │    http://stays.localhost:8766        hotel search, room, guest details      → HOLD-yyyy
       │    http://tables.localhost:8766       restaurant time, party, name           → HOLD-zzzz
       │    (take_screenshot at results, review and confirmation → outputs/browser/)
       └─ returns the plan: hold ids, prices, total, policy notes
  main assistant: shows the plan → "STATUS: waiting — reply yes to go ahead"
Maya: "Yes, go ahead."
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
3. The specialist opens `skyway.localhost`, `stays.localhost` or `tables.localhost` in the browser and searches. It chooses compliant options using what the pages show, fills in Maya's details from her contact record, and stops at each review page. It takes screenshots of the results and review pages (`take_screenshot` with a file path under `outputs/browser/`), and returns hold ids, prices, the total and any policy issues.
4. Show Maya the plan and wait for an explicit yes.
5. `travel_book(hold_id=…)` and `restaurant_book(hold_id=…)`, then `check_booking`, then open each confirmation page in the browser and screenshot it.

The definition of done adds: every booking came from a hold created on the site; confirmation pages were shown.

## App changes

- Onboarding option "**Book through the browser** (opens Brave)", on by default when port 9222 answers or Brave is installed. If neither, show it off, with "Install Brave or Chrome to see bookings happen in a browser."
- A banner while browser steps run: "Working in the browser…", with a "Bring browser to front" hint.
- The behind-the-scenes timeline shows browser steps (`navigate`, `click`, `fill`) with the page URL, plus thumbnails of the screenshots saved under `outputs/browser/`.
- Suggested layout for demos: Intuition on the left half of the screen, Brave on the right half. `make browser` can open Brave with a window size and position flag for this.

## Runner and evals

- Variant `browser=on`: starts `ea_sites`, requires the browser on port 9222 (skips the trial with a clear message if it's missing), forces `--parallel 1` (one shared browser), and adds the browser MCP and guard hook to the overlay.
- Slice `browser-demo`: R01 and R04 with `browser=on`, one trial each. Lesson 4 or the wrap shows that the same rubric passes or fails the browser run exactly as it does the direct run.

## Tests

- `tests/test_sites.py`: every page renders for each variant; forms create holds with the right prices; `harbor-sold-out` hides the block; a hold then `travel_book(hold_id)` gives a booking and a "Confirmed" page; HTML has labels for every input.
- `tests/test_browser_guard.py`: allowed and denied URLs; logging.
- **Live check:** with Brave on port 9222, a scripted Claude Code session books R01 through the browser and passes the booking rubric; screenshots exist; the guard denies a navigation to an outside site.

## Requirements this adds (PRD)

- **B12:** mock booking sites with holds, confirmable only through the gated booking tools.
- **G9:** when Maya asks for a reservation in the app, the assistant visibly works in a Brave window on the mock sites. Bookings still need approval, and screenshots appear behind the scenes.
- **E17:** the `browser=on` variant grades browser bookings with the same rubrics.
