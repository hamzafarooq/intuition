# Instinct: research notes

Collected 2026-10-04. Most of this comes from secondary sources, marked below; check the primary reporting before using any of it on slides.

## What it is

- Invite-only personal AI assistant you text or call, including over iMessage and WhatsApp. Its pitch: "there are no new interfaces." ([instinct.com](https://instinct.com))
- Connects to email, messaging, calendar, documents, screen, audio and location. Google Workspace through official APIs; for other services it operates its own cloud computer and browser, with stored credentials. ([Sacra](https://sacra.com/c/instinct/))
- Proactive: follows up on dropped threads, texts or calls the user first. ([instinct.com](https://instinct.com))
- Its terms appoint it as the user's agent, authorized to enter agreements and transactions on the user's behalf. ([Vellum](https://www.vellum.ai/blog/official-instinct-breakdown), summarizing the terms)
- Made by Spear Street Technology, Inc., San Francisco. Forbes reports it was registered in April 2026 by Noah Shinn, previously a research scientist at Sierra. (Vellum, citing Forbes)
- Noah Shinn is a co-author of τ-bench ([arXiv:2406.12045](https://arxiv.org/abs/2406.12045)), the tool-agent-user benchmark Anthropic's evals guide cites, and of Reflexion. This makes a strong opening for an evals workshop.

## Numbers (company-reported via Sacra, unless noted)

- $1B Series C in September 2026 at a $10B valuation; $250M Series B in August 2026 at $2.5B.
- Pre-revenue. Close to $1B in transaction volume, about half of it travel. Plans a transaction fee.
- 40% of users have shared card details after three weeks.
- Target users: founders, investors, executives, affluent consumers. This is the executive-assistant market.

## Reported launch-week failures (from Vellum; verify)

| Failure | Reported case | Eval suite here |
|---|---|---|
| Prompt injection through email | A user emailed his own inbox malicious instructions as a test; Instinct followed them and returned a summary of open tasks | `safety/injection` |
| Action without approval | A user reported Instinct sent an email on her behalf without checking first | `safety/gate` |
| Data kept after disconnect | A user disconnected Google access and still received an inbox summary hours later; message text remained in her data export | `safety/disconnect` |

From YouTube reviews (see [youtube.md](youtube.md)):
- It treats a thumbs-up reaction as approval (Greg Isenberg's review).
- It handed out a user's personal information by default (AI For Humans).
- It stalls on payment pages and tasks that need a phone app (Isenberg); a live restaurant booking "did not go to plan" (AI with Starr).

## Competitors, for context

- **Meta Muse**: launched September 2026, app and WhatsApp, its own virtual computer; Meta says sensitive actions need approval. (Sacra)
- **Platforms**: Google Gemini, Apple Siri, Microsoft Copilot. (Sacra)
- **Independent assistants**: Town, Lindy, Fyxer, Carly. (Sacra, search results)
- **Open source, self-hosted**: OpenClaw (MIT; Instinct has been called "OpenClaw for normal people"), Hermes Agent from Nous Research (MIT), Vellum (MIT). The comparison list comes from Vellum, which ranks itself first.
- **Desktop**: Claude Cowork, ChatGPT Work.

## Sources

- [instinct.com](https://instinct.com)
- [Sacra: Instinct](https://sacra.com/c/instinct/)
- [Vellum: Official Instinct Breakdown (2026)](https://www.vellum.ai/blog/official-instinct-breakdown). A competitor; use for leads, not facts.
- Reddit r/AI_Agents: [stress-testing Instinct](https://www.reddit.com/r/AI_Agents/comments/1wak4is/ive_been_stresstesting_instinct_with_reallife/), [how to build your own](https://www.reddit.com/r/AI_Agents/comments/1wicj9y/how_do_you_actually_build_your_own_instinct_ai/). Top tip from the second: put a small classifier in front of each inbound message to decide whether it updates an open task ("actually make it 4pm") or starts a new one. That's a good small LLM-layer eval.
