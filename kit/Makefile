# Intuition workshop kit. Run `make help` for the list.
UV ?= uv

.PHONY: help setup test lint doctor smoke start app browser sites report site starter clean-runs demo-real phoenix trace

help:
	@echo "make setup    install dependencies; create .env from .env.example"
	@echo "make doctor   check every prerequisite (uses a little of your Claude plan)"
	@echo "make start    quick checks, then open the mock phone"
	@echo "make test     offline tests (no keys needed)"
	@echo "make smoke    one golden case, one trial, through Claude Code"
	@echo "make browser  open Brave/Chrome with remote debugging for browser bookings"
	@echo "make demo-real  instructor only: the mock phone with real-site search (quotes only, clean throwaway browser)"
	@echo "make phoenix  start Arize Phoenix at http://localhost:6006 (second terminal)"
	@echo "make trace    send the latest mock-phone session to Phoenix (RUN=<run-id> for an eval run)"
	@echo "make site     build the guide into site/dist/"
	@echo "make starter  build ../starter/ from this kit"

setup:
	$(UV) sync
	@test -f .env || (cp .env.example .env && echo "Created .env: add OPENAI_API_KEY (and optionally SERPAPI_API_KEY).")

test:
	$(UV) run pytest -q

lint:
	$(UV) run ruff check .

doctor:
	$(UV) run python -m ea_evals.doctor $(ARGS)

smoke:
	$(UV) run ea-eval run --cases S01 --trials 1 --parallel 1

start:
	$(UV) run python -m ea_evals.doctor --quick
	@[ "$$EA_BROWSER" = "off" ] || $(UV) run python tools/browser.py || true
	$(UV) run ea-start

# Instructor-only real-site search demo (ea_harness/real_sites.py). Off for students and in evals.
demo-real:
	$(UV) run python -m ea_evals.doctor --quick
	EA_REAL_SITES=on $(UV) run ea-start --real-sites

app:
	$(UV) run ea-app --open

browser:
	$(UV) run python tools/browser.py

sites:
	$(UV) run ea-sites

report:
	$(UV) run ea-eval report $(RUN)

# Observability: Phoenix runs on its own (uvx keeps it out of the kit's venv); `trace` exports a session or run to it.
phoenix:
	uvx arize-phoenix serve

trace:
	$(UV) run --extra phoenix ea-eval phoenix $(or $(RUN),latest) $(ARGS)

site:
	$(UV) run python site/build.py

starter:
	$(UV) run python tools/make_starter.py

clean-runs:
	rm -rf runs evals/results reports
