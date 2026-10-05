# Intuition workshop kit. Run `make help` for the list.
UV ?= uv

.PHONY: help setup test lint doctor smoke start app browser sites report site starter clean-runs

help:
	@echo "make setup    install dependencies; create .env from .env.example"
	@echo "make doctor   check every prerequisite (uses a little of your Claude plan)"
	@echo "make start    quick checks, then open the Intuition app"
	@echo "make test     offline tests (no keys needed)"
	@echo "make smoke    one golden case, one trial, through Claude Code"
	@echo "make browser  open Brave/Chrome with remote debugging for browser bookings"
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

app:
	$(UV) run ea-app --open

browser:
	$(UV) run python tools/browser.py

sites:
	$(UV) run ea-sites

report:
	$(UV) run ea-eval report $(RUN)

site:
	$(UV) run python site/build.py

starter:
	$(UV) run python tools/make_starter.py

clean-runs:
	rm -rf runs evals/results reports
