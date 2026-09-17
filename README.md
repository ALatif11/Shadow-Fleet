# Shadow Fleet

Evasion-indicator scoring for tankers from cooperative data (terrestrial AIS and open watchlists), with a point-in-time backtest. The question it measures: how many tankers later listed by OFAC, the EU or the UK would this system have ranked highly before they were listed, and how many weeks early?

It is not "dark fleet detection". All inputs are cooperative (ships broadcasting AIS, public sanctions lists), and the evaluation is pre-registered (`PREREG.md`, committed in Phase 2 before any labels are inspected).

**Status (Sep 17 2026): Phase 0 scaffold.** The plan and the frozen ingest are written and unit-tested on synthetic data. None of the live-data probes has run yet, so there are no results. Every number in this repo will come from `reports/`.

## Theater and data
- Danish Maritime Authority AIS: every tanker-class hull seen from Danish shore stations (the Baltic exit).
- Global Fishing Watch Events API v3: satellite-informed gaps, encounters, loitering and port visits for those hulls.
- Labels: the OFAC SDN change archive, EU Annex XLII to Reg 833/2014, and the UK FCDO list (via OpenSanctions).

## Licences and what is published
Global Fishing Watch data and OpenSanctions data are licensed for **non-commercial use only** (OpenSanctions: CC BY-NC 4.0). This repo publishes code, aggregate tables and figures only. It never includes GFW-derived tables, OpenSanctions bulk files or raw AIS; `data/` is gitignored. The project does not scrape Equasis or any site whose terms forbid it.

## Run it
See `SETUP.md` (WSL2, uv, llama.cpp) and `phase-prompts.md`. Short version:
```bash
make setup && make doctor && make test
make probe-dma && make window-gate && make ingest-dma
```

## Analyst console
`ui/` is a local React console (ADR-18): a ranked watchlist per cutoff, a map of each hull's DMA track and GFW / self-built events, and a timeline that shows each hull only as it was knowable at the chosen instant. It currently runs on a synthetic bundle that is labelled as such; it is wired to live outputs after Phase 6. It is local only, because its data includes GFW-derived events. See `SETUP.md` section 9.

## Documents
- `shadow-fleet-plan.md`: architecture, risks, ADRs, evaluation design.
- `CLAUDE.md`: rules for every build session.
- `phase-prompts.md`, `phase1-prompt.md`: one prompt per phase.
- `reports/`: phase reports and every measured number.
