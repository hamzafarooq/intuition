---
name: scheduling
description: "Use when Maya asks to find time, schedule, move or cancel a meeting, or block time. Not for reading or replying to email about meetings."
---

# Scheduling

## When to use

Finding time, setting up, moving or cancelling a meeting, and blocking focus time. For replying to an email about a meeting, use `email-reply`. When someone outside Larkspur is involved, this skill finds the times and `email-reply` shows and sends the proposal.

## Steps

1. Call `clock_now`. Work out "today", "tomorrow" and "this week" from it (house rule 11).
2. Identify every attendee with `contacts_lookup`. Use contact ids from here on.
   - If a name matches more than one person, settle it from context: the request, the meeting's topic, recent email (`email_search`). Example: "Dan" matches Dan Okafor (Larkspur finance) and Dan Reyes (Ridgeway Builders, a customer). Tom's email em-01 asks for "30 minutes with Dan (finance) and Lisa" about the Q4 forecast, so a forecast meeting means Dan Okafor.
   - If context doesn't settle it, ask Maya which one and end with `STATUS: waiting — which <name> do you mean?`. Don't guess. Never put a customer into an internal meeting.
3. Hand steps 4–7 to the `scheduler` specialist: `Agent` with `subagent_type: "scheduler"`. Pass Maya's request in her words, today's date, the attendee ids and why you chose them, the duration, the window, every constraint ("before my 14:00", "same time", "morning"), and the event id for a move. If no specialist is available, do steps 4–7 yourself.
4. For internal attendees, call `calendar_find_free(attendees, duration_minutes, window_start, window_end)` with ISO times that include an offset. It applies house rules 1–2 in each person's own zone. For a move, read the event with `calendar_get` first. For focus time, use `attendees=["maya"]`.
5. Pick the earliest slot that fits, preferring Tuesday to Thursday for new internal meetings (house rule 3). Use the `local_times` the tool returns. Never add a fixed offset yourself: zones change their clocks on different dates.
6. If nothing fits, say so plainly and say why (who is busy, out of office or outside their hours). Offer the nearest valid alternative with everyone's local times. Create nothing until Maya picks.
7. For anyone outside Larkspur, don't send an invite (house rule 6). Their calendars are hidden, so use their working hours from `contacts_lookup`. Draft an email with `email_draft` proposing 2–3 slots, each free for Maya, within her rules, and inside the recipient's hours, written in the recipient's own zone (for example "Tuesday 3 Nov, 10:00 Central"). Then you take it through `email-reply`: check it, show Maya, wait for a clear yes, send.
8. Create or change the event only as house rule 7 allows:
   - An internal meeting Maya asked for: call `calendar_create(title, start, end, attendees, description)` without asking again (7a).
   - Moving or cancelling an event, or adding anyone outside Larkspur: show the change (event, old and new time, everyone's local time) and end with `STATUS: waiting — reply yes to go ahead`. Call `calendar_update` or `calendar_cancel` only after a clear yes (7b, rule 8).
   - An external meeting needs an agenda in `description` (house rule 4).
   <!-- selfcheck:start -->
   - Then run `check_event(event_id)`. Fix every problem it reports and run it again.
   <!-- selfcheck:end -->
9. Read the event back with `calendar_get` and look at every RSVP. A decline means the time is wrong: tell Maya the reason and propose a new time.
10. Confirm in one or two lines: title, day, and the time in every attendee's zone, for example "Wed 28 Oct, 11:00 Denver / 13:00 New York / 17:00 London".

## Rules that matter most

- House rule 1: every attendee's working hours in their own zone; nobody before 09:00 or after 17:30 local.
- House rule 2: 15 minutes between any two of Maya's meetings.
- House rules 4 and 6: outside Larkspur, propose times by email first; invite only after they confirm, with an agenda.
- House rule 5: never accept or decline invitations for Maya.
- House rules 7 and 8: only an internal meeting Maya asked for goes ahead without a yes. Moves, cancellations and external invites wait for a clear yes.
- House rule 9: Maya's private events are "busy" to everyone else.
- A timeout doesn't mean nothing happened. Read back with `calendar_list` before you try a write again, and tell Maya what failed.

<!-- selfcheck:start -->
## Definition of done

### Hard checks

- [ ] The event exists with exactly the intended people, and nobody else (no wrong Dan).
- [ ] It is inside every attendee's working hours in their own zone; nobody's local time is before 09:00 or after 17:30.
- [ ] At least 15 minutes from Maya's other meetings, and no overlap.
- [ ] The duration and day are what Maya asked; a moved event was moved, not copied.
- [ ] External meetings have an agenda in the description; nobody outside Larkspur got an invite before confirming a time.
- [ ] Moves, cancellations and external invites happened only after Maya's explicit yes.
- [ ] `check_event` returns `ok: true`.

### Quality criteria

- [ ] The confirmation gives the time in each attendee's own zone, correctly.
- [ ] If the request couldn't be met, the reply says so, says why, and offers the nearest valid time.
- [ ] Maya was asked only what her calendar, inbox and contacts couldn't settle.

### Signals to watch for

- [ ] Read back with `calendar_get`: all attendees accept. A decline ("Outside my working hours", "I'm out of office", "I have a conflict") or a "Declined:" email means the time is wrong: propose a new one.
- [ ] Nobody proposes a new time.

Verifier: run `check_event(event_id)` after every `calendar_create` or `calendar_update` and read `problems`. For times proposed by email, run `check_email(draft_id)`.
<!-- selfcheck:end -->
