# What to reuse from the multi-agent-course repo

Source: [hamzafarooq/multi-agent-course](https://github.com/hamzafarooq/multi-agent-course), surveyed 2026-10-04 (read-only). Paths are relative to the repo root; M1 to M7 stand for `modules/Module_N_*`.

## Modules at a glance

| Module | Topic | Eval-relevant files |
|---|---|---|
| M1 Agent Foundations, Harness, System Design | Agent loop, ReAct, harness | `Assignment_1_Lumina/` quality rules, gates, rubric, gold sets |
| M2 Skills, Subagents, Product Architecture | Sub-agents, orchestrators | Study material only |
| M3 Production Agentic RAG | Router, cache, knowledge graphs | `Evaluation_and_Guardrails/AI_Eval_Metrics.ipynb`, `Knowledge_Graphs/DETERMINISTIC_JUDGING.md` |
| M4 Multi-Agent Systems | MCP, A2A, ADK | `Assignment_3_Customer_Support/EVALS.md`, `THRESHOLDS.md`, `agent_subagent_orchestrator_starter.ipynb` |
| M5 Voice Agents | Cascade vs speech-to-speech | `benchmarking_voice_agents/manifest.json`, `bench_grading.py` |
| M6, M7 | Leadership, demo day | None |

Also: `.claude/skills/` (skill-evaluator, email-writer, exec-summary, meeting-analyzer), `FDE-01-assignments/`, `Starter_Projects/`.

## Reuse map

| Need here | Reuse | Change |
|---|---|---|
| Trajectory rules | M1 `Assignment_1_Lumina/quality/rules.json` and `check.mjs`: A1 failed tools carry an error, A2 run ends done, A3 no thrashing, R1/R2 must and must-not call, B1–B3 token, time and cost budgets; each rule cites a precedent | Port to Python over EA transcripts and `calls.jsonl`; add Instinct incidents as precedents |
| Grading rule | Lumina E3: no error-severity verdict from a model's opinion | Keep as stated |
| Gates and expectations | Lumina `expectations.json`, `eval/eval.mjs` (static, contract, run, trajectory, eval, human), `eval/rubric.json` | Same order; thresholds before the first run |
| Thresholds | M4 `THRESHOLDS.md` with a "why this number" column; T-LEAK = 0, T-MUTATE = 0 | EA thresholds in `evals/thresholds.md` |
| Attack vs legitimate pairs | M4 `EVALS.md`: 30 attack, 30 legitimate, cross-user probes | Same pairing, but attacks arrive in email and web pages, not user messages |
| Tool-eval format | M5 `manifest.json`: `expected_tools` with `args_contain`, `forbidden_tools`, `ground_truth_facts`, `should_block` | Same fields in EA task files |
| Tool scoring and judge | M5 `bench_grading.py`: tool precision and recall; judge that flags a reply claiming an action with no matching tool call | Reuse the "lied about acting" criterion in the agent-conduct rubric |
| Skill scoring | `.claude/skills/skill-evaluator`: 0/1/2 per case, confidence bands | Add trigger sets, which it doesn't cover |
| Skills to adapt | `email-writer`, `exec-summary`, `meeting-analyzer` ("flag it rather than fabricate") | Base for inbox-triage and meeting-brief |
| Judge settings | M3 `DETERMINISTIC_JUDGING.md` (temperature 0, fixed seed) | Same for EA judges |
| Vocabulary | M3 `AI_Eval_Metrics.ipynb` eval pyramid: task success, tool-use accuracy, trajectory efficiency, coordination, error propagation | Use the same names in the report |
| Action gate | FDE-01 `Assignment_4_Final_Epyhia/README.md`: sandbox by default, approval before anything irreversible, emails to a catcher | Mock `outbox.json` is the catcher |
| Seed-data style | M1 `Alex_Perplexity_Clone/data/` (ACME meeting notes, action items) | Style for customer notes |
| Offline mode | `PROVIDER=mock` in FDE-01 Assignment 2; mocked Tavily in Alex `backend/tools.py` | `EA_MODE=mock` and search cassettes |

## Fix before reusing

`M4/agent_subagent_orchestrator_starter.ipynb` has three bugs:
- `api_key` is undefined when the Colab import fails.
- It writes `skills/market-sizing.md` but reads `full-stack-developer.md`.
- `RAW_URL` points to a github.com `/blob/` page instead of the raw file.

## What the repo doesn't have yet

The EA example adds:
- A mock calendar, inbox and contacts world, with end-state checks
- pass@k and pass^k over real repeated trials
- Judge calibration against human labels
- Skill-trigger evals
- Multi-agent handoff evals on real runs
- Harness A/B and fault injection
- Approval-gate evals
- Injection through email and web content
- A Python eval runner for Claude

Stack note: the repo's modules use the raw Anthropic SDK, Google ADK and OpenAI. None uses the Claude Agent SDK, so the runner here is new code.
