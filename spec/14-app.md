# The app: Intuition

A local web app that makes the assistant feel like a real product. An onboarding page, in the spirit of signing up for a personal assistant, sets Maya up and "connects" her accounts. A messaging simulator then opens docked on the side. Behind it, the app runs a harness against the tool server and streams everything that happens, so students can watch skills, sub-agents, tool calls, searches, checks and approvals live.

## Name and look

- **Name:** **Intuition** (working name; one setting, `APP_NAME`). Use only this name and original wording. Don't use Instinct's name, logo or copy, or WhatsApp's name, logo, icons or brand colours. The app should read as its own product, so it can't be mistaken for either company.
- **Landing style:** minimal and editorial. A large serif headline, generous whitespace, one short paragraph, one primary action. Write original copy, for example: headline "Message it. It handles the rest." and a sub-line "Your assistant reads your inbox and calendar, gets things done, and asks before anything it can't undo."
- **Messaging style:** generic chat conventions (right-aligned user bubbles, left-aligned assistant bubbles, timestamps, a typing indicator, delivered ticks) in the app's own palette.
- Light and dark themes from design tokens. Readable from 360 px wide.

## Package and stack

- `kit/ea_app/` with Starlette and Uvicorn, using server-sent events for streaming. Static front end in `ea_app/static/` (vanilla HTML, CSS and JS, no build step).
- Command `ea-app [--port 8765] [--open]` serves http://localhost:8765 and optionally opens the browser.
- Each session drives Claude Code headless through the adapter ([05-harnesses.md](05-harnesses.md)) with `EA_APPROVAL_MODE=app`. Approval cards read `<run_dir>/approvals/pending/` and write the decision files.
- On start, the app runs the `make doctor` checks for Claude Code and shows a clear message if `claude` is missing or not logged in.
- The app hosts one harness instance per session ([05-harnesses.md](05-harnesses.md)) and forwards its trace events to the browser.

## Screens

### 1. Onboarding (the landing page)

Shown on the left (or full width before setup).

1. **Hero:** name, headline, sub-line, "Get started".
2. **How it works:** three short steps: "Connect your accounts", "Message it like a person", "Approve anything it can't undo".
3. **Set up:**
   - **You are:** Maya Chen, Regional Sales Director at Larkspur Supply (fixed persona card with initials).
   - **Connect accounts:** a toggle for each connector (Email, Calendar, Contacts, Documents, Web, Travel), showing what the assistant will be able to see and do with each. A "Connect all" button. The connections are mock; the toggles drive the tool server's connectors.
   - **Options:** search (Live by default when a SerpAPI key is set, otherwise Mock; note that the fictional companies won't appear in live results, and evals always use recorded results); Claude model (Opus by default, or Sonnet); **Book through the browser** (opens Brave; see [16-browser-bookings.md](16-browser-bookings.md)); "Auto-approve after I say yes" (on by default; see Approvals).
   - **Start messaging:** creates the session and opens the simulator.
4. After setup, this area becomes the **Behind the scenes** view (screen 3).

Turning a connector off after setup calls `connector_disconnect` and can't be undone within that session; the UI says so and offers "Reset world".

### 2. Messaging simulator (docked on the right)

- A phone-shaped panel about 390 × 780 px, docked to the right edge on desktop. Full screen on narrow viewports.
- **Header:** assistant avatar, "Intuition", status ("online", or "working…" while a turn runs).
- **Conversation:** Maya's messages and the assistant's replies. Timestamps use the world clock. The STATUS line is shown as a small chip under the reply (done, partial, failed, waiting), not as text.
- **Under the hood:** a chevron under each assistant reply expands an inline timeline for that turn: skills loaded, hand-offs to specialists, tool calls (name plus a short argument summary; tap for full JSON), search queries and pages fetched, checks (✓, or ✗ with problems), signals (for example "Lisa declined: I'm out of office"), reasoning summaries, and the turn's cost and time.
- **Approval cards:** when the harness asks for approval, a card appears in the chat with the action summary (recipient and text, event details, or booking and price) and **Approve** and **Deny** buttons.
- **Composer:** text box, send button, and a microphone button when voice is enabled and supported.
- **Suggested first messages** as chips on an empty conversation: "What needs me today?", "Prep me for my 2pm", "Find 30 minutes with Dan and Lisa this week".

### 3. Behind the scenes (left area after setup)

Two tabs:

- **Live trace:** the whole session's trace as a scrolling timeline, colour-coded by event type, filterable (tools, skills, agents, approvals, checks, signals).
- **Maya's world:** her week's calendar (a list by day, with RSVP badges), inbox (new arrivals such as decline notices highlighted), outbox (sent emails), bookings, saved briefs and decks (links to the rendered HTML), and connector status. Refreshes after every write.

Wide screens show both screens 2 and 3. Mobile shows the simulator, with "Behind the scenes" as a bottom sheet.

## Approvals

- The harness approval callback emits `approval_request`, then waits on a future that the UI resolves.
- With **Auto-approve after I say yes** on: if Maya's most recent message is an explicit yes (matches `^(yes|yep|go ahead|send it|book it|do it|approved?)\b`, case-insensitive), the call is approved automatically. The card shows "Approved by your 'yes'". Otherwise the card waits for a click.
- With it off, every gated call needs a click even after a typed yes. Lesson 4 uses this to show the two layers: the model asking in chat, and the harness gate.
- Recorded as `approval_response` with `by: app`.

## API

| Method and path | Body | Returns |
|---|---|---|
| `GET /api/config` | | App name, available harnesses, whether live search and voice are available |
| `POST /api/sessions` | `{harness, search_mode, auto_approve, variants?}` | `{session_id}`: creates a run directory and starts a harness |
| `GET /api/sessions/{id}/events` | | Server-sent events: each trace event as JSON, plus `world_changed` after writes |
| `POST /api/sessions/{id}/messages` | `{text}` | `202`; the turn runs in the background |
| `POST /api/sessions/{id}/approvals/{call_id}` | `{decision: "allow" \| "deny"}` | `204` |
| `POST /api/sessions/{id}/connectors/{name}` | `{connected: false}` | `{status}`; calls `connector_disconnect` directly on the tool server and records a `disconnect` signal |
| `GET /api/sessions/{id}/world` | | Snapshot: calendar for the week, inbox, outbox, bookings, outputs, connectors |
| `GET /api/sessions/{id}/outputs/{path}` | | A saved brief or deck HTML |
| `POST /api/sessions/{id}/reset` | | New run directory and harness |

Only one turn runs per session at a time; a message sent mid-turn is queued and shown as "sending…".

## Voice (stretch; flag `VOICE=1`, off by default)

- **Speak:** the microphone button uses the browser's Web Speech API (`SpeechRecognition`, with the `webkit` prefix where needed) to dictate into the composer. Maya reviews the text and presses send. Nothing sends automatically.
- **Listen:** a speaker toggle reads assistant replies aloud with `speechSynthesis`, skipping the status chip and the under-the-hood content.
- **Support:** works in Chrome, Edge and Safari. Brave and Firefox don't provide speech recognition (**verify at build time**), so hide the button when `SpeechRecognition` is missing. A tooltip notes that some browsers send audio to their vendor for recognition.
- No server changes and no extra keys.

## Workshop use

- **Setup:** students open the app, connect accounts, and send the first message. Claude Code in `assistant/` remains available for anyone who prefers the terminal.
- **Lessons:** the "Behind the scenes" view is the live demo surface. Lesson 4 uses the approval cards and the connector toggles; the disconnect case X07 can be shown here by hand.
- The guide site links to the app, and the app's footer links back to the guide.

## Tests

- `tests/test_app.py`: Starlette `TestClient` with a fake harness that emits scripted trace events. Check session creation, event streaming order, approval round-trip, connector disconnect, world snapshot, reset.
- Visual check in a browser at 1440 × 900 and 390 × 844, light and dark: the simulator is docked on the right on desktop, nothing overflows, approval cards and the under-the-hood timeline render.
