# Guide site and starter repo

## Guide site

A static site that walks students through the workshop step by step. It isn't public: it ships inside the starter repo and opens from disk at `site/dist/index.html`.

### Pages

| Page | Contents |
|---|---|
| `index.html` | What you'll build (the assistant, the evals, the app); what a personal assistant agent is; the Instinct story and its three reported first-week failures, framed as "reported"; the four-hour agenda; "Start with setup" |
| `setup.html` and `lesson-1.html` … `lesson-5.html`, `wrap.html` | One page per block of [plan/01-lessons.md](../plan/01-lessons.md) |
| `rubrics.html` | How to write a rubric: the rules from [08-rubrics.md](08-rubrics.md#rubric-file-schema), with a worked example and a bad-to-good rewrite |
| `cheatsheet.html` | The eval layers (LLM, tools, skills, agent, multi-agent, harness, safety): what to test, what to collect, how to grade, an example failure from Maya's world |
| `glossary.html` | The PRD glossary |

### Lesson page anatomy

Matches the lesson plan:

- **Header:** "Lesson N of 5", title, a one-line goal, duration, and "By the end of this lesson" (what will exist).
- **Steps,** each a card with:
  - a step number and title, plus a tick box
  - **Why** (two or three sentences)
  - **Prompt**: text to paste into Claude Code at the kit root, with a copy button
  - **Run**: commands to paste into a terminal, if any
  - **What to expect**: a bullet list of patterns, not exact numbers
  - **Replay** (optional): a recorded trace rendered as a timeline, for steps that are slow or may fail live
- **Check:** three multiple-choice questions with instant feedback and a one-line explanation.
- **What you have built so far:** each file the workshop produces, marked built or not yet, computed from ticked steps.
- Previous and next links. Later lessons show a soft lock ("Finish the earlier steps first, or show anyway").

### Content format

One YAML file per page in `site/content/` (`setup.yaml`, `lesson-1.yaml`, …):

```yaml
title: Tools and tool evals
number: 1
duration: 35 min
goal: Tools that do what their descriptions say, and evidence the model calls them correctly.
by_the_end: [...]
steps:
  - id: "1.2"
    title: Run the tool unit tests
    why: >
      ...
    prompt: |
      ...
    run: "uv run pytest tests/test_tools.py -k find_free"
    expect: [..., ...]
    builds: ["ea_world/calendar.py (fixed)"]
    replay: replays/lesson1-find-free.jsonl
check:
  - q: "A tool call has the right name but the wrong time zone. Which score catches it?"
    options: ["Selection accuracy", "Argument accuracy", "pass@k"]
    answer: 1
    why: "..."
```

### Writing the prompts

The builder writes every prompt during the build, following these rules:

- Prompts run in Claude Code at the **kit root**, where `CLAUDE.md` is the builder's brief. They name the exact files to create or change under `assistant/`, `evals/` or `ea_world/`.
- Each prompt is self-contained. It states the goal, the files, the required content (for example the skill's frontmatter description, verbatim, and the definition-of-done items), and how to check the result.
- Prompts never paste the reference solution. They give the requirements, so students' versions vary, and the evals measure the difference.
- Expected outcomes are written as patterns ("selection accuracy is high; argument accuracy is lower, mostly time-zone mistakes"), checked against the reference run.

### Build and behaviour

- `site/build.py` renders `site/templates/*.j2` with Jinja into `site/dist/`, inlining CSS and JS, so pages work from `file://` with no server and no network.
- Progress is saved in `localStorage` under `intuition-guide:v1:<step-id>`, wrapped in try/catch so the page still works when storage is blocked.
- Replays are pre-rendered from `.jsonl` traces at build time.
- Design tokens are shared with the app (`ea_app/static/tokens.css`), in light and dark. Readable at 360 px. A link to the app (http://localhost:8765) sits in the header.
- **Visual check:** every page in a browser at 1440 and 390 widths, light and dark.

## Starter repo

`tools/make_starter.py` builds `../starter/` from `kit/`.

**Copied unchanged:**
- world data, the tool server (except the file below), harnesses, runner, report and app
- the golden dataset, component suites, and every rubric except `email.yaml`
- the three pre-built skills: `email-reply`, `travel-booking`, `web-research`
- the builder's `CLAUDE.md`, and the prebuilt `site/dist/`

**Removed** (students build these in the lessons):

| Removed from `kit/` | Built in |
|---|---|
| `assistant/.claude/skills/{scheduling,inbox-triage,meeting-brief,meeting-deck}/` | Lesson 2 |
| `assistant/.claude/agents/{scheduler,inbox,briefer,travel,reviewer}.md` | Lesson 3 |
| `evals/rubrics/email.yaml` | Lesson 3 |
| `assistant/.claude/skills/monday-brief/`, `assistant/.claude/agents/{planner,challenger,qa}.md` | Lesson 5 |
| `evals/labels/*`, `evals/results/*`, `reports/*` | Created by runs |

**Replaced with starter versions** from `starter_overrides/`:

| File | Starter version |
|---|---|
| `ea_world/calendar.py` | The fixed-offset bug and the vague `calendar_find_free` description (Lesson 1) |
| `assistant/CLAUDE.md` | Who you are, the world, house rules, asking for approval, untrusted content, privacy, times, status line. **No** routing table, specialists, self-check bullets or reviewer (added in Lesson 3) |
| `assistant/.claude/settings.json` | `allow` and `deny` only: no `ask` list (Lesson 4) and no hooks (Lesson 3) |

**Checks:** the script verifies that `uv run pytest tests/test_tools.py -k find_free` **fails** in `starter/` and passes in `kit/`, and that `ea-eval list cases` works in both.
