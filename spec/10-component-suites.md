# Component suites

Single-layer tests that don't need the full agent loop. Each item is one short headless Claude Code call (`claude -p`, low `--max-turns`), so they run on the student's Claude login and stay small.

## `llm_dates.yaml`: date and time reasoning (Lesson 1.5)

One `claude -p` call with no tools (`--tools ""` or every tool disallowed; verify the flag) and `--append-system-prompt` set to: "Today is Monday 26 October 2026, 08:30 in Denver (America/Denver). Answer with only the value requested, in the format requested." Grader: exact match after normalization (dates `YYYY-MM-DD`, times `HH:MM` 24-hour, weekdays capitalized). Default 5 trials per item.

Workshop items (20; the full set adds 30 more of the same kinds). Avoid ambiguous phrasings such as "next Tuesday" said on a Monday: exact-match grading needs one right answer.

| # | Question | Answer |
|---|---|---|
| 1 | What date is the Tuesday of next week? (YYYY-MM-DD) | 2026-11-03 |
| 2 | What date is this Thursday? | 2026-10-29 |
| 3 | What date is a week from tomorrow? | 2026-11-03 |
| 4 | What weekday is 4 November 2026? | Wednesday |
| 5 | What is 11:00 Denver time on Wednesday 28 October 2026 in London? (HH:MM) | 17:00 |
| 6 | What is 11:00 Denver time on Wednesday 4 November 2026 in London? | 18:00 |
| 7 | What is 09:00 London time on Monday 26 October 2026 in Denver? | 03:00 |
| 8 | What is 2pm Central time on Thursday 29 October 2026 in Denver? | 13:00 |
| 9 | What is 14:00 Denver time on Monday 2 November 2026 in Chicago? | 15:00 |
| 10 | How many hours ahead of Denver is London on 28 October 2026? (number) | 6 |
| 11 | How many hours ahead of Denver is London on 4 November 2026? | 7 |
| 12 | How many hours ahead of Denver is London on 20 October 2026? | 7 |
| 13 | What date is the last Sunday of October 2026? | 2026-10-25 |
| 14 | What date is the first Sunday of November 2026? | 2026-11-01 |
| 15 | What date is "end of next week" if weeks end on Friday? | 2026-11-06 |
| 16 | What date is 10 business days after today? | 2026-11-09 |
| 17 | What date is Friday noon's deadline this week? | 2026-10-30 |
| 18 | If a meeting is at 17:00 London on 28 October 2026, what time is it in New York? | 13:00 |
| 19 | What date was 9 days ago? | 2026-10-17 |
| 20 | How many nights is a hotel stay from 2 November to 4 November 2026? (number) | 2 |

The interesting items are 5, 6, 10, 11 and 12: the clock-change week. They make the pass@k vs pass^k discussion concrete.

## `tool_use.yaml`: tool selection and arguments (Lesson 1.3)

One `claude -p` call in an overlay of `assistant/` where **every** `ea-world` tool is in `permissions.ask`, with `EA_APPROVAL_MODE=record_and_deny`. Every attempt is recorded and denied, so nothing executes; `--max-turns 2`. The **first** attempted tool call is graded. Two scores per item:
- **Selection:** the tool name is in `expected_tools`.
- **Arguments:** every `args_contain` fragment matches (strings case-insensitive; times compared as instants), and the tool isn't in `forbidden_tools`.

Item format (the multi-agent-course M5 manifest shape):

```yaml
- id: tu-01
  request: "What's on my calendar on Wednesday?"
  expected_tools: [calendar_list]
  args_contain: {start: "2026-10-28", end: "2026-10-28|2026-10-29"}
  forbidden_tools: [web_search]
```

Workshop items (30):

| id | Request | Expected first tool | Arguments must contain | Forbidden |
|---|---|---|---|---|
| tu-01 | What's on my calendar on Wednesday? | calendar_list, clock_now | start on 2026-10-28 | web_search |
| tu-02 | When is Lisa free on Wednesday? | calendar_find_free, contacts_lookup, calendar_list | attendees include lisa.park (if find_free) | |
| tu-03 | Who's Dan? | contacts_lookup | query "Dan" | |
| tu-04 | Find the email from Raj about Thursday. | email_search | query contains "Raj" or "Thursday" | web_search |
| tu-05 | Read Amy's agenda for today. | email_search | query contains "agenda" or "Amy" | |
| tu-06 | What's our travel policy for hotels in Chicago? | docs_search | query contains "travel" | web_search |
| tu-07 | Look up Fastlane's price cut online. | web_search | query contains "Fastlane" | |
| tu-08 | What's the time now? | clock_now | | |
| tu-09 | Find flights to Chicago on 2 November. | travel_search | kind flight; destination contains "ORD" or "Chicago"; date 2026-11-02 | |
| tu-10 | Find a hotel near the summit for 2 to 4 November. | travel_search | kind hotel; check_in 2026-11-02; check_out 2026-11-04 | |
| tu-11 | Find a table for 4 near Harbor Point on 3 November at 7pm. | restaurant_search | party_size 4; time 19:00; date 2026-11-03 | |
| tu-12 | Find 30 minutes with Dan Okafor and Lisa this week. | calendar_find_free, contacts_lookup | duration 30 (if find_free) | calendar_create |
| tu-13 | Is the Ridgeway meeting at 2pm today? | calendar_list, calendar_get | start on 2026-10-26 | web_search |
| tu-14 | Draft a reply to Kevin saying lunch is on. | email_draft, email_search | to kevin.osei (if draft) | email_send |
| tu-15 | Show me Maya's Granite call details. | calendar_get, calendar_list | | |
| tu-16 | Check the event you just made. | check_event | | |
| tu-17 | Check this draft before I send it: dr-1. | check_email | draft_id dr-1 | email_send |
| tu-18 | Save this brief. (with text) | brief_save | | |
| tu-19 | What did Priya send about the forecast? | email_search | query contains "forecast" or "Priya" | |
| tu-20 | Which emails are unread? | email_search | unread_only true | |
| tu-21 | Search our docs for Ridgeway's contract end date. | docs_search | query contains "Ridgeway" | web_search |
| tu-22 | Disconnect my email. | connector_disconnect | name "email" | |
| tu-23 | What time is 11am Denver in London on Wednesday? | clock_now, (no tool) | | web_search |
| tu-24 | Is Tom busy Thursday afternoon? | calendar_list, calendar_find_free | person tom.becker (if list) | |
| tu-25 | Find Dan Reyes's email address. | contacts_lookup | query contains "Reyes" | |
| tu-26 | Read the summit registration email. | email_search | query contains "summit" or "registration" | web_search |
| tu-27 | What's on the Fastlane pricing page? fastlane.example/pricing | web_fetch | url contains "fastlane.example/pricing" | |
| tu-28 | Look up the 2027 catalog in our docs. | docs_search | query contains "catalog" | |
| tu-29 | Find time with Raj next week. | contacts_lookup, calendar_list, calendar_find_free | | calendar_create |
| tu-30 | Book the 15:10 economy flight on 2 November. | travel_search | | travel_book |

`(no tool)` in tu-23 means answering without a tool also counts as correct selection.

## `skill_triggers.yaml`: skill loading (Lesson 2.4)

One `claude -p` call per prompt in the assistant folder, with every `ea-world` tool recorded and denied as in the tool-use suite, and `--max-turns 2`. Grade whether a `Skill` tool use for the expected skill appears before any other tool use.

For each of the four Lesson 2 skills (scheduling, inbox-triage, meeting-brief, meeting-deck): 20 prompts that should load it and 20 that shouldn't. The "shouldn't" prompts are near misses that belong to another skill or need no skill. Precision and recall per skill.

Writing rules for the builder (write all 160 prompts):
- Use Maya's world (real names, real meetings) and vary the phrasing: direct, indirect, short, long, with typos.
- Should-trigger sets: about 60% plain requests, 40% indirect ("Dan and Lisa need to see the forecast before Friday").
- Shouldn't-trigger sets: half from neighbouring skills (scheduling vs email-reply about a meeting; brief vs deck), half needing no skill ("What time is it?", "Who is Amy?").

Examples:

| Skill | Should trigger | Shouldn't trigger |
|---|---|---|
| scheduling | "Get me 30 minutes with Kevin today." / "Can you move Thursday's forecast review?" / "Dan and Lisa need to see the forecast before Friday." | "Reply to Raj that Thursday doesn't work." (email-reply) / "What's on my calendar Monday?" (no skill) |
| inbox-triage | "What needs me today?" / "Anything urgent in my inbox?" / "What have I let slip?" | "Reply to Kevin about lunch." (email-reply) / "Summarize the Ridgeway account." (meeting-brief) |
| meeting-brief | "Prep me for the 2pm." / "What do I need to know before Granite?" | "Make slides for Ridgeway." (meeting-deck) / "What time is the Granite call?" (no skill) |
| meeting-deck | "Five slides for Ridgeway." / "Turn the forecast into a short deck for Tom." | "Prep me for Ridgeway." (meeting-brief) / "Email Tom the forecast." (email-reply) |
