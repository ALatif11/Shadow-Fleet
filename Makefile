# Shadow Fleet. Run inside WSL2 from the repo root (see SETUP.md).
PY ?= uv run python
CLI = $(PY) -m shadowfleet.cli
DATE ?=
FULLRES ?= 0

.PHONY: setup test lint doctor probe probe-dma probe-gfw probe-ofac probe-opensanctions probe-mid \
        window-gate ingest-dma ingest-dma-bg ingest-dma-status ingest-dma-check ingest-day llm-smoke \
        report-phase0 cutoffs \
        ui-schema ui-fixtures ui-export ui-check ui-install ui-dev ui-build test-ui test-all

setup:
	uv sync --extra dev
	mkdir -p data reports/logs reports/probes config
	@test -f .env || (cp .env.example .env && echo "created .env: add GFW_TOKEN")

test:
	$(PY) -m pytest -q

lint:
	uv run ruff check shadowfleet tests

doctor:
	$(CLI) doctor

cutoffs:
	$(CLI) cutoffs

# ---- Phase 0 probes (order matters: opensanctions before gfw; dma before window-gate)
probe: probe-dma probe-opensanctions probe-gfw probe-ofac probe-mid report-phase0

probe-dma:
	$(CLI) probe-dma $(if $(DATE),--day $(DATE),)

probe-opensanctions:
	$(CLI) probe-opensanctions

probe-gfw:
	$(CLI) probe-gfw

probe-ofac:
	$(CLI) probe-ofac

probe-mid:
	$(CLI) probe-mid

window-gate:
	$(CLI) window-gate

# ---- DMA bulk ingest (resumable; safe to Ctrl-C and rerun)
ingest-dma:
	$(CLI) ingest-dma

ingest-dma-bg:
	mkdir -p reports/logs
	nohup $(CLI) ingest-dma > reports/logs/ingest_dma.out 2>&1 &
	@echo "started; follow with: make ingest-dma-status"

ingest-dma-status:
	@tail -n 5 reports/logs/ingest_dma.out 2>/dev/null || echo "no background run log"
	@$(CLI) ingest-dma-check | head -n 8

ingest-dma-check:
	$(CLI) ingest-dma-check

ingest-day:
	@test -n "$(DATE)" || (echo "usage: make ingest-day DATE=YYYY-MM-DD [FULLRES=1]" && exit 1)
	$(CLI) ingest-day $(DATE) $(if $(filter 1,$(FULLRES)),--fullres,)

llm-smoke:
	$(CLI) llm-smoke

report-phase0:
	$(CLI) report-phase0

# ---- UI console (ADR-18). Node 22+ inside WSL; see SETUP.md section 9.
NPM ?= npm --prefix ui

ui-schema:        ## after any change to shadowfleet/ui_export/contract.py
	$(CLI) ui-schema
	@test ! -d ui/node_modules || $(NPM) run gen:types

ui-fixtures:      ## SYNTHETIC bundle into ui/public/ui_data (replaces what is there)
	$(CLI) ui-fixtures

ui-export:        ## LIVE bundle; reports missing phases until Phase 6 is done
	$(CLI) ui-export

ui-check:
	$(CLI) ui-check

ui-install:
	$(NPM) ci

ui-dev:
	@test -d ui/node_modules || $(NPM) ci
	@test -f ui/public/ui_data/manifest.json || $(CLI) ui-fixtures
	$(NPM) run dev

ui-build:
	$(NPM) run build

test-ui:
	@if [ -d ui/node_modules ]; then $(NPM) run check; \
	else echo "SKIPPED UI tests: run make ui-install first"; fi

test-all: test test-ui
