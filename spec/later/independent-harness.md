# Independent harness (deferred design)

Not part of the current build. Kept so it can be added later behind the same `Harness` interface ([../05-harnesses.md](../05-harnesses.md)) and trace format.

## Purpose

A short, readable loop (about 300 lines) that runs the same assistant files as Claude Code. It enables two things: a cross-harness comparison (same model, same tools, different harness), and running the assistant on another model family.

## Loading

- **System prompt:** the assistant's CLAUDE.md, then "Available skills" (each skill's `name: description`), then "Available specialists" (each agent's `name: description`).
- **Tools:** every `ea-world` tool from `list_tools()` minus anything denied in `settings.json`; `load_skill(name)`, which returns the skill body; and `delegate(agent, task)`, main agent only.
- **Permissions:** parsed from `settings.json` (map `mcp__ea-world__X` to `X`); precedence deny, then ask, then allow; `ask` tools go through the approval callback.
- **MCP client:** `mcp` v2 `stdio_client` and `ClientSession`. Pass `EA_*`, `SERPAPI_API_KEY`, `PATH` and `HOME` explicitly, because the child doesn't inherit the environment. Read `structured_content`, falling back to text content.

## Model call (if on Claude; load the claude-api skill first)

- Stream with `max_tokens=64000`, then take the final message.
- Always send effort explicitly. Adaptive thinking with summarized display.
- No temperature, no forced tool choice.
- Prompt caching on system and tools.
- Server-side refusal fallback on by default for Opus.
- **History is append-only:** append the response content exactly as returned, thinking blocks included.

## Loop (per user turn)

```
messages.append(user(text))
loop:
    check budgets → stop
    response = model_call(messages); record usage, thinking summaries and text
    messages.append(assistant(response.content))
    if refusal: stop
    if tool_use: run each tool_use block in order; append all results in ONE user message; continue
    # end_turn
    if stop check returns a reason and fewer than 2 blocks so far:
        messages.append(user("[Harness check] " + reason)); continue
    return
```

`run_tool`:
1. `load_skill` returns the skill body.
2. `delegate` runs a nested loop with the agent file's body plus the house rules as the system prompt, the agent's tools plus `load_skill`, and the parent's budgets; it returns the final text.
3. `ask` tools go through the approval callback; a denial returns an error result ("Not approved…").
4. With `idempotency=auto`, write tools get an idempotency key from a hash of the tool and its arguments.
5. Rate limits wait and retry once. Read tools retry once on timeout. Write tools never auto-retry.

## Differences from Claude Code to teach

| Area | Claude Code | Independent |
|---|---|---|
| System prompt | Claude Code's own plus CLAUDE.md | Only CLAUDE.md and the lists |
| Skills | Built-in discovery and the `Skill` tool | `load_skill`, listed up front |
| Sub-agents | `Agent` tool, built-in context management | Nested loop |
| Context management | Automatic compaction | None (budgets keep runs short) |
| Stop check | Command hook | In the loop |
