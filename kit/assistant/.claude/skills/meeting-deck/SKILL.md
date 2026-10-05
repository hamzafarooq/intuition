---
name: meeting-deck
description: "Use when Maya asks for slides or a deck. Not for a written brief."
---

# Meeting deck

## When to use

"Make me five slides for the Ridgeway meeting", "Three slides on the West Q4 forecast for Tom", "One slide with every open issue". For a written brief, use `meeting-brief`.

## Steps

1. Call `clock_now`.
2. Find the meeting or topic: `calendar_list` or `calendar_get` for a meeting, and a quick `email_search` and `docs_search` for the topic. If there's no data on it, say so and stop. Never make a deck with invented numbers.
3. Hand steps 4–7 to the `briefer` specialist: `Agent` with `subagent_type: "briefer"`. Pass Maya's request in her words, today's date, the event id or topic, the slide count, and the budgets below. If no specialist is available, do them yourself.
4. Gather the facts as for a brief: the event description, related email (`email_search`, `email_read`), documents (`docs_search`, `docs_read`), and the web (`web_search`, `web_fetch`) only if Maya asks.
5. Plan exactly the number of slides Maya asked for (5 if she didn't say). One idea per slide. The last slide states the ask or the decision.
6. Write each slide within the budgets `check_deck` enforces:
   - `title`: a claim, a sentence that says something ("Delivery delays put the renewal at risk", not "Deliveries"); 60 characters or fewer.
   - `bullets`: at most 5, each 90 characters or fewer, and at most 70 words on the slide.
   - `sources`: the email ids, document ids, event id or URLs behind the slide. Every slide has at least one.
   - `notes`: optional, for detail that doesn't fit.
7. Create it with `deck_create(title, slides)`.
   <!-- selfcheck:start -->
   - Run `check_deck(deck_id)`. If it reports problems, shorten or source the slides, call `deck_create` again, and check the new deck id.
   - Send it to the reviewer (see **Review** below). Fix what fails.
   <!-- selfcheck:end -->
8. Reply with the deck id, the slide count and the slide titles.

<!-- selfcheck:start -->
## Review

Call `Agent` with `subagent_type: "reviewer"`. In `prompt`, give only: Maya's request, the deck id, every slide's title, bullets and sources, and these criteria, word for word. Leave out your reasoning and notes.

1. Claim headlines: "Is each slide title a claim (a sentence that says something) rather than a topic label?"
2. Ends with an ask: "Does the last slide state an ask or a decision?"
3. Grounded: "Is every number on the slides found in the listed sources?"
4. Slide count: "Does the deck have exactly the number of slides Maya asked for?"

Fix every criterion that fails, create the deck again, and run `check_deck` on the new id.
<!-- selfcheck:end -->

## Rules that matter most

- Every number comes from a source you read. If you can't source it, leave it out.
- Stay inside the budgets: text within them never overflows the slide template.
- If the topic has no data, say so. Don't fill the gap.
- House rule 9: a deck for anyone outside Larkspur carries no internal numbers and nothing about other customers.

<!-- selfcheck:start -->
## Definition of done

### Hard checks

- [ ] The slide count is what Maya asked for (5 by default).
- [ ] `check_deck` is ok: titles of 60 characters or fewer, at most 5 bullets of 90 characters or fewer, at most 70 words a slide, every number traced, sources on every slide.
- [ ] The last slide states the ask or the decision.
- [ ] If there was no data, no deck was made and the reply says so.

### Quality criteria

- [ ] Every title is a claim, not a topic label.
- [ ] The deck ends with a clear ask or decision.
- [ ] One idea per slide, in an order that builds to the ask.
- [ ] The reviewer passed every criterion.

### Signals to watch for

- [ ] Maya asks to cut, condense or fix a slide.
- [ ] Maya rewrites the headlines.

Verifier: run `check_deck(deck_id)` on the created deck and read `problems`.
<!-- selfcheck:end -->
