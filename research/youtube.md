# YouTube survey: what people have built

Collected 2026-10-04 from titles, descriptions and chapter lists, not full viewing. "No evals" means none appear in the description or chapters.

## Eval builds

| Video | Channel | Date, views | What was built |
|---|---|---|---|
| [Ship Real Agents](https://www.youtube.com/watch?v=Xfl50508LZM) | AI Engineer (Laurie Voss) | May 2026, 21K | Financial-analysis agent: Phoenix traces, failures grouped by cause, code evals plus judges plus a custom "actionability" rubric; experiments to prove a prompt change |
| [Beginner's Course on Evals](https://www.youtube.com/watch?v=TL527yTpxlk) | Peter Yang (Aman Khan) | Aug 2025, 60K | Support agent: human-labeled golden set, scaled with a judge, judge checked against humans |
| [Evals Clearly Explained](https://www.youtube.com/watch?v=uiza7wp1KrE) | Peter Yang (Hamel Husain) | Sep 2025, 22K | 100 traces reviewed in a spreadsheet; binary pass/fail; TPR and TNR over raw agreement |
| [How to Automate AI Evals (Correctly)](https://www.youtube.com/watch?v=tqUDjc1HzO4) | Hamel Husain (Shreya Shankar) | Jul 2026, 9.2K | Error-discovery skill, review UI, failure-type list, choosing which traces to review next |
| [8 Claude Code skills for evals](https://www.youtube.com/watch?v=c5Ur73GosR4) | Hamel Husain | Sep 2026, 4.5K | Real-estate assistant: annotation UI, varied sampling, eval-audit skill, judge vs humans |
| [Better Evals with Claude Code](https://www.youtube.com/watch?v=bdMHQLvtVaQ) | Peter Yang (Shreya and Hamel) | Aug 2026, 12.8K | Live audit of evals for skills; one grader per criterion; failures become reusable evals |
| [Claude Skills Tutorial (Evals + Memory)](https://www.youtube.com/watch?v=uT3EQPVIEb0) | Peter Yang | Jun 2026, 13.8K | Writing skill: description edits for reliable triggering, self-fix eval loop |
| [Skill Creator Got Evals & Benchmarking](https://www.youtube.com/watch?v=pryf2-jesTg) | The Art of Vibe Coding | Mar 2026, 2.3K | Security-review skill: with vs without, 6 parallel agents, pass rate, tokens and time, trigger tuning |
| [How to evaluate agents in practice](https://www.youtube.com/watch?v=vuBvf7ZRKTA) | Google Cloud Tech | Dec 2025, 52K | ADK three-tier pyramid: unit tests, trajectory tests, human review |
| [How to Evaluate Tool-Calling Agents](https://www.youtube.com/watch?v=JytjrbDaI44) | Arize AI | Mar 2026, 7.5K | Travel assistant: one judge for "right tool", one for "right arguments", checked against labels |
| [Agent trajectories with AgentEvals](https://www.youtube.com/watch?v=FCKjETgr54U) | LangChain | Mar 2025, 8.5K | ReAct agent trajectory evals in LangSmith |
| [Evaluating Multi Agent Systems](https://www.youtube.com/watch?v=PvdaIqIUpnQ) | Galileo and CrewAI | Dec 2025, 1K | Research crew: context lost at handoffs; observability metrics only |
| [Agent Eval Crash Course](https://www.youtube.com/watch?v=rwxcoEglXA4) | DSwithBappy | Aug 2026, 6.7K | Notebook: final answer, tool selection, trajectory, latency, safety, a prompt-injection test |
| [Agent Evals: Test What Matters](https://www.youtube.com/watch?v=jefzKhIN1rk) | Vanishing Gradients | Sep 2026, 561 | Refund agent that never called the refund tool; judge-human alignment; pass@k vs pass^k |
| [Calibrate LLM-as-Judge](https://www.youtube.com/watch?v=G_uNX1aR6xQ) | CIMO Labs | Feb 2026, 301 | Calibrating a judge on about 100 human labels |
| [Agent Harness in 20min](https://www.youtube.com/watch?v=p5-hHg2YRTA) | Maryam Miradi | Sep 2026, 5.2K | Legal agent; harness as experiment rig: swap parsers, retrievers, models |

## Assistant builds

| Video | Channel | Date, views | What was built | Evals |
|---|---|---|---|---|
| [Turn Claude Code Into Your Executive Assistant](https://www.youtube.com/watch?v=mi4hcipESKQ) | Nate Herk | Mar 2026, 208K | Claude Code project: context and rules, skills and sub-agents, memory | None |
| [Personal Assistant AI Agent in n8n](https://www.youtube.com/watch?v=ZP4fjVWKt2w) | Nate Herk | Nov 2024, 165K | Orchestrator with email, calendar and research sub-agents; Telegram and voice | Manual testing only |
| [Hermes Agent: Build Your Own Personal AI Assistant](https://www.youtube.com/watch?v=erMROJqU5t4) | Komputer Mechanic | Jul 2026, 6.6K | Multi-agent "chief of staff" on a VPS and Telegram: triage, drafts, calendar, follow-ups, digests | "Test end to end", no detail |
| [I Made Claude My Personal Assistant](https://www.youtube.com/watch?v=3ZT0upsICHk) | Systems Made Better | Sep 2026, 39K | Claude Cowork and Notion daily brief | None |
| [Build a Personal AI Assistant Like Grok Bot](https://www.youtube.com/watch?v=5auulroARF0) | Damian Galarza | Oct 2026, 702 | Mastra agent, memory, Composio connectors, E2B computer use | None |
| [AI Agent with Gmail, Calendar and Slack](https://www.youtube.com/watch?v=8ROL45j6ps8) | Nicolai Nielsen | Sep 2026, 4.8K | Executive assistant with Arcade auth and per-tool scopes | None |
| [5 OpenClaw agents run my home](https://www.youtube.com/watch?v=96Vl8s3EQhk) | How I AI (Jesse Genet) | Feb 2026, 53K | Five role-scoped agents on separate Mac Minis | None; says handoffs break |
| [Claude Code AI Email Assistant](https://www.youtube.com/watch?v=sjIzZEOfQ10) | Kumo Explains | Apr 2026, 5.2K | Email-reply skill, drafts for review | Manual review |
| [Instinct AI is For Real](https://www.youtube.com/watch?v=mUAsaprJ66s) | Greg Isenberg | Sep 2026, 156K | Review of real Instinct chats | None; notes thumbs-up as approval, stalls |
| [The $10B AI Assistant](https://www.youtube.com/watch?v=Am7IWP8IpEc) | Invest Like The Best (Noah Shinn) | Sep 2026, 137K | Founder interview: proactivity, cost, trust with cards and data | None |
| [Instinct & Muse AI Agents](https://www.youtube.com/watch?v=-VL4VKHrnpU) | AI For Humans | Sep 2026, 21K | Living with Instinct and Muse | Notes oversharing of personal info |
| [I Used Instinct AI for 5 Days](https://www.youtube.com/watch?v=WACbikvvIGE) | AI with Starr | Sep 2026, 4.9K | Calendar, morning brief, live restaurant booking | Booking failed |
| [Indirect Prompt Injection in an Email Agent](https://www.youtube.com/watch?v=GiXH4LQXLGs) | Donato Capitella | Jun 2024, 2.5K | Attacks on the open-source "Damn Vulnerable Email Agent", including data exfiltration | Exploit demo |
| [Meta AI Researcher explains ARE](https://www.youtube.com/watch?v=lT4qtOlvhak) | Arize | Nov 2025, 511 | Meta's Agents Research Environments: simulated apps with verifiers | The only systematic eval content |
| [Human in the Loop (n8n)](https://www.youtube.com/watch?v=CdnR-fNVPKI) | Nate Herk | 45K | Approval gate pattern | None |

## Patterns

- Eval videos favour customer support and refunds, then travel assistants and research crews.
- Commonly shown: trace review, judges plus code checks, golden sets, judges checked against humans, tool selection, trajectories.
- Assistant builds always include Gmail and calendar connectors, a morning brief, chat delivery (Telegram, iMessage, WhatsApp, Slack), memory or context files and scheduled jobs. Sometimes: sub-agents per domain, a credential vault, an approval step.
- Not one assistant build shows a test set, pass/fail scoring, a judge, trajectory checks or a regression run.

## Gaps this workshop fills

- Harness fault injection: timeouts, malformed output, retries that repeat side effects. No video found.
- Multi-agent handoff evals with code graders. Only an observability webinar.
- pass^k on real repeated trials. One chapter in a 561-view video.
- A prompt-injection regression suite with attack success rate, especially injection inside email and web pages.
- Skill-trigger test sets. Short chapters only.
- Statistical judge calibration. One 301-view video.
- Simulated-user evals and end-state grading for assistants. None.

## Borrow

1. The "wrong eval" demo (Laurie Voss): the same agent scored 0/13 on one metric and 13/13 on another.
2. Keep reviewing traces until new ones show no new failure types; binary pass/fail; report TPR and TNR (Hamel and Shreya).
3. Separate judges for right tool and right arguments (Arize).
4. Run the same task with and without the skill in parallel; compare pass rate, tokens, time (skill-creator benchmarking).
5. The "says it acted but didn't" trap (refund agent; the voice-agent judge in the multi-agent-course repo).
6. Google's three-tier pyramid as a way to explain the order of the lessons.
7. Damn Vulnerable Email Agent attacks as seeds for the injection suite.
