# Shadow Fleet. Run inside WSL2 from the repo root (see SETUP.md).
PY ?= uv run python
CLI = $(PY) -m shadowfleet.cli
DATE ?=
FULLRES ?= 0

.PHONY: setup test lint doctor probe probe-dma probe-gfw probe-ofac probe-opensanctions probe-mid \
        window-gate ingest-dma ingest-dma-bg ingest-dma-status ingest-dma-check ingest-day llm-smoke \
        report-phase0 cutoffs

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
