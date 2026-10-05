# Intuition: build an executive assistant, then prove how far you can trust it

A four-hour workshop kit. You build Maya Chen's executive assistant in Claude Code, meet it in the Intuition app, and test every layer with a golden dataset of 63 cases graded by rubrics.

1. Install Claude Code and log in, and install uv: [docs/SETUP.md](docs/SETUP.md).
2. `make setup`
3. Add `OPENAI_API_KEY` (and optionally `SERPAPI_API_KEY`) to `.env`.
4. `make doctor`, and fix anything red.
5. `make start` opens the app; send "What needs me today?".
6. `uv run ea-eval run --slice setup-baseline` runs your first evals.
7. The guide is in `site/dist/index.html` (`make site` builds it).

Everything is fictional and runs on your laptop. No real accounts are touched.
