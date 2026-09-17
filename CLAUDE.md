# CLAUDE.md: Shadow Fleet

Read fully before doing anything in this repo. Then read `PREREG.md`, the prompt for the phase you were asked to implement, and `reports/phaseN.md` for every earlier phase.

## What this is
A portfolio system that scores tankers on sanctions-evasion indicators derived from cooperative data (AIS and open watchlists) and measures, via a point-in-time backtest, how many later-designated tankers it would have ranked highly before OFAC, the EU, or the UK listed them, and how many weeks early. Frame it exactly that way: "evasion-indicator scoring from cooperative data with a point-in-time backtest," never "dark fleet detection." Single developer (Adam, undergrad, ~12 h/week), single machine (Ryzen 7600X3D, 32 GB RAM, RTX 5070 12 GB VRAM, CUDA 12.8+), free data only, all LLM inference local.

Theater: tankers observed by Danish shore-based AIS (the Baltic exit chokepoint, including the Skagen anchorage where ship-to-ship transfers are documented). Global behaviour for those hulls comes from the Global Fishing Watch (GFW) Events API. Iran-trade tankers are out of scope.

## Runtime (ADR-13)
Everything runs in WSL2 Ubuntu on Adam's PC, with the repo and `data/` on the WSL filesystem (`~/shadowfleet`), never under `/mnt/c`. See `SETUP.md`; `make doctor` checks Python, disk, GPU and tokens. A Cowork session shell is a small VM with no GPU: use it to write and test code with synthetic fixtures, and hand long jobs to Adam as exact `make` commands. The host drive was 98 percent full on Sep 17 2026; the disk guard (`MIN_FREE_GB`) is not optional.

## Non-negotiable rules
1. **Point-in-time or nothing.** Every raw record has `observed_at`. `features(hull_id, T)` may only read records with `observed_at <= T`. If a source has no reliable date on a field, that field is not a feature. OpenSanctions and OFAC data are used for labels and `listed_as_of_T` only.
2. **Pre-registration governs evaluation.** `PREREG.md` (written and committed in Phase 2, before labels are built) fixes: the cutoff rule (last day of every calendar month inside the window; quarterly aggregates are derived, never chosen), the primary endpoint (precision@50 within the B1 Russia-port stratum, label = OFAC∪EU∪UK), the primary model (LightGBM), and the frozen feature list. Anything added or changed after Phase 5b results are seen is labelled post-hoc in every report. Feature families are never removed after results are seen; ablations are reporting only.
3. **Population excludes anyone already listed.** At cutoff T the population contains hulls observed in the feature window and not listed by any of OFAC, EU, or UK on or before T. "Already on another Western list" is baseline B1b, reported, never a feature.
4. **Never invent metrics.** Metrics come from `make backtest` output in `reports/`. Placeholders stay placeholders until the harness produces the number.
5. **Do not store raw AIS beyond the files in flight.** Stream from the zip (never unzip to disk), write Parquet, delete the zip. The ingest schema is frozen in Phase 0 (ADR-14) because deleted raw data cannot be re-read: anything that needs all vessels (`jump_baseline`, `vessel_day`) is computed at ingest time.
6. **One GPU tenant at a time.** Training is CPU (LightGBM, sklearn, igraph). Only a llama.cpp server touches the GPU, and only in Phases 0 (smoke test), 8, and 9.
7. **Respect licences.** GFW and OpenSanctions are non-commercial; do not scrape Equasis or any site whose terms forbid it. Never commit GFW-derived tables or OpenSanctions bulk files; the repo publishes code, aggregates, and figures only. State this in `README.md`.
8. **Report failures as findings.** If a model cannot beat the rules baseline, if positives are thin, if GFW lacks tanker loitering, if self-built STS detection finds nothing: write it in `reports/phaseN.md` and the README. Do not tune until it looks good.
9. **Ask what is Adam's to decide, then assume and record.** If Adam is present at the start of a session, ask the questions only he can answer (scope, spend, anything irreversible). Otherwise assume, proceed, and write every assumption in `reports/phaseN.md` under "Assumptions to confirm".
10. **Do not break earlier phases.** Before ending a session, run the full `make test`, not only the new tests. A change to a table schema or config key used by an earlier phase needs a migration note in the phase report.

## Repo layout
```
shadowfleet/
  data/raw/  data/parquet/  data/cache/gfw/     # all gitignored
  shadowfleet/
    config.py                 # paths, window, cutoff rule, horizon, polygons, flag lists, port lists (single source of truth)
    ingest/   dma.py gfw.py ofac.py opensanctions.py mid.py
    resolve/  identity.py
    detect/   sts.py loitering.py draught.py spoof.py     # self-built detectors on DMA tracks (Phase 4b)
    features/ asof.py ais.py gfw.py identity.py detect.py graph.py
    labels/   labels.py
    models/   rules.py tabular.py anomaly.py
    backtest/ harness.py metrics.py drift.py
    briefs/   bundle.py generate.py verify.py
    util/     disk.py net.py logs.py ids.py probes.py     # disk guard, HTTP, logging, IMO check digit, probe files
    cli.py                    # `python -m shadowfleet.cli <command>`
  config/window.json          # written by `make window-gate` in Phase 0; committed
  tests/  tests/fixtures/
  notebooks/                  # exploration only, never imported
  reports/                    # phaseN.md, metrics tables, figures, audit sheets
  Makefile  pyproject.toml  README.md  SETUP.md  CLAUDE.md  PREREG.md  phase-prompts.md  phase1-prompt.md  shadow-fleet-plan.md
```

## Phases (one Opus session each)
0 probes, scaffold, frozen ingest schema, DMA one-day benchmark, bulk ingest start, llama.cpp smoke test, window gate · 1 DMA ingest completion, population, gap evidence, STS readiness · 2 labels and PREREG.md · 3 identity resolution · 4a GFW events · 4b self-built detectors · 5a feature store and leakage suite · 5b baselines and harness · 6 models, ablations, drift, SHAP · 7 graph layer (stretch only; skip unless ahead of schedule) · 8 briefs · 9 faithfulness · 10 report · F forward test (score on or after 2026-10-01, evaluate spring 2027).

## Conventions
- Python 3.11+, `uv` or `pip` with `pyproject.toml`. Core deps: duckdb, pyarrow, polars or pandas, shapely, pyproj, lightgbm, scikit-learn, igraph, httpx, pydantic, typer, pytest, matplotlib. Add nothing heavy without a note in the phase report.
- Storage: Hive-partitioned Parquet under `data/parquet/<table>/dt=YYYY-MM-DD/`; DuckDB queries them directly. No database server.
- Timestamps: UTC, `TIMESTAMP` columns, never strings. Every table has `observed_at`.
- Keys: `hull_id` (string; IMO when valid, else `gfw:<id>`, else `syn:<hash>`), `mmsi` (int), `imo` (int or null), `cutoff` (date).
- Config: window bounds (fixed in Phase 0), cutoff rule, horizon (182 d), feature window (180 d), Danish-water, Skagen-anchorage and Russian-port polygons, convenience-flag list, tanker ship-type codes, all in `config.py` with a comment citing where each value came from.
- CLI over scripts: each phase adds `shadowfleet.cli` commands and Makefile targets. Everything resumable and idempotent.
- Tests: `pytest tests/` must pass at the end of every phase. The leakage suite (Phase 5a) runs inside `make backtest` and blocks it on failure.
- Logging: structured, to `reports/logs/`. Long jobs print progress and write a checkpoint file.
- Phase report: every session ends by writing `reports/phaseN.md` with: what was built, commands to run it, measured numbers, assumptions to confirm, what the next phase depends on.
- Style: type hints, small functions, docstrings only where behaviour is non-obvious. No notebooks in the critical path.

## Key decisions (short form; full ADRs in shadow-fleet-plan.md)
- Raw AIS from Danish Maritime Authority daily CSVs, filtered to tanker-class at ingest (tanker-class MMSI set = today's tanker-class rows ∪ persistent registry ∪ allowlist). NOAA is US-only and dropped. Window recorded in `config/window.json` (see the window line below). Machine-hours are never a reason to shrink it; only disk or a DMA deletion race are, and never below 21 months without Adam (a 15-month window leaves zero supervised cutoffs; see ADR-11 addendum).
- Population membership: tanker by AIS-reported type OR by GFW vessel classification OR by dimensions consistent with a tanker; hulls that change reported type are tracked in a table.
- Global gaps, encounters, loitering, port visits from GFW Events API v3, population-limited, cached. All public gap events are intentional since Aug 2025 (no separate intentional feature). GFW registry ownership fields are as-of-now and never features. Self-built detectors on DMA tracks (STS candidates, anchorage loitering, draught inconsistency, MMSI-IMO churn) so the project owns at least one detection layer; ablations report GFW-only vs self-built-only vs both.
- DMA gaps are coverage, not evasion; dark-gap features come only from GFW GAP events.
- Spoof-jump counts are normalised per day and 0.5-degree cell by the share of all vessels jumping, so GNSS-interference days wash out.
- Labels: OFAC add dates from SDN advanced XML `EntryEvent/Date` (every current entry is dated), cross-checked against the yearly change archive, which is still required for removals and for vessels no longer listed (2024 onward is PDF-only there). EU = Annex XLII to Reg 833/2014, from `eu_sanctions_map` (programId `EU-MARE`, dates in `startDate`, CELEX fallback), not `eu_fsf`. UK = `gb_fcdo_sanctions` (dates in `startDate`). Dated OpenSanctions exports are paid-only, so dates are cross-checked against CELEX/Official Journal and manual spot checks. Headline label OFAC∪EU∪UK; OFAC-only reported as a sensitivity table.
- Hull identity = IMO by majority vote over DMA static messages; GFW identity linking used as fallback only, with a silver test set from OpenSanctions and GFW IMO-MMSI pairs and a sensitivity arm that disables GFW-based merges.
- Window (decided Sep 17 2026): 2024-03-01 (first daily DMA file) to the latest file; monthly-archive backfill is optional and needs the stage-2 day-column optimisation first.
- Strait of Hormuz closure (2026-02-28) is a pre-registered regime break (`config.REGIME_BREAKS`): metrics and drift are reported before and after it; it is never a window bound. Forward test (Phase F): top 50 committed to git on or after 2026-10-01 and scored against real designations through spring 2027 (ADR-17).
- Backtest: monthly cutoffs, 180 d feature window, 182 d horizon, expanding-window training on cutoffs whose horizon closed before T. Metrics per cutoff, monthly and quarterly aggregates, stratified by the B1 Russia-port rule, plus a first-appearance evaluation (each hull scored only at its first eligible cutoff).
- Lead time is event-study: designation date minus the earliest cutoff at which the hull ranked in the top k. Per-cutoff lead time is a supplement.
- Metrics: precision@k and recall@k (k = 25/50/100), PR-AUC, alert volume needed for 50 percent recall, FPR at the operating point, calibration; mandatory qualitative review of the top 20 non-listed flags per cutoff with a labelled reason.
- Models: rules B0/B1/B1b/B2, logistic regression, LightGBM, isolation forest; per-cutoff PSI drift check and performance by training-window age. Graph PPR only as stretch. No GNN.
- LLM: llama.cpp server, Gemma 4 12B Q4_K_M (fallback Qwen3-14B Q4_K_M), 8k context, JSON-schema constrained output, briefs cite evidence ids. Judge is a different model family from the generator; judge-vs-human kappa on a 30-finding audit is the credibility number.

## Known limitations to keep visible
- Ownership through shell companies is largely invisible in free data; the graph layer, if built, uses dated PSC manager edges at best.
- Many listed tankers were designated for ownership or price-cap reasons, not observable evasion; recall is capped and reported.
- Feature design was informed by public reporting through 2026, so it is not blind to the test period; PREREG.md limits post-hoc changes but cannot remove this.
- GFW event and identity models postdate the cutoffs; disclosed, quantified for identity merges, not fixable.
- Adversary behaviour shifted during 2025; drift tables are shown next to pooled numbers.
- Lead time is bounded by the horizon and right-censored at the last cutoff.
