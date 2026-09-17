# Shadow Fleet: Phase Prompts for Opus

Paste one prompt per session. Every prompt assumes Opus has read `CLAUDE.md`, `SETUP.md`, and all existing `reports/phaseN.md`. Each session ends with `reports/phaseN.md` and a passing `make test` (the whole suite, not only the new tests).

Reconciled Sep 17 2026 against `shadow-fleet-plan.md` (revision items 12 to 19). Where a prompt and CLAUDE.md disagree, CLAUDE.md wins; fix the prompt and note it in the phase report.

---

## PHASE 0: Probes, scaffold, frozen ingest

You are completing Phase 0 of Shadow Fleet. Read CLAUDE.md and SETUP.md first.

State on entry: the repo scaffold and Phase 0 code were written and unit-tested on Sep 17 2026 against synthetic fixtures only, because the planning sandbox could not reach DMA, OFAC, OpenSanctions or GFW. Nothing in `ingest/` has touched live data yet. Your job is to run it against the real sources on Adam's machine (WSL2), fix what live data breaks, and record measured numbers.

Goal: prove every data source works with real calls, measure one DMA day end to end with the frozen ingest (ADR-14), start the bulk ingest oldest first, smoke-test llama.cpp on the GPU, and fix the window.

Inputs: the repo; `.env` with `GFW_TOKEN`; `make doctor` passing; at least 100 GB free on the WSL disk.

Tasks:
1. `make doctor`. Fix anything red before continuing.
2. DMA (`make probe-dma`): parse the live index, record earliest and latest available dates and whether older data is daily or monthly archives. Ingest one day from roughly 14 days ago with `--keep-fullres`. Record zipped bytes, rows in, rows after dedupe, tanker-class MMSIs (and how many came from the registry vs that day's rows), dynamic/static rows out, `vessel_day` rows, Parquet bytes per table, seconds per step, peak temp bytes, and unknown or missing header columns. If the header differs from `config.DMA_COLUMN_ALIASES`, extend the aliases and add a fixture test for the new variant. Check the Class A, length >= 100 m, unknown-type MMSI count (how many possible tankers the type filter misses).
3. Window gate (`make window-gate`): write `config/window.json` per the ADR-11 addendum: start = earliest available day; shrink only if projected Parquet + temp exceeds free disk minus `MIN_FREE_GB`, or measured throughput cannot outrun the DMA deletion cadence. Never below 21 months without Adam. Print evaluable and supervised cutoff counts.
4. Start the bulk ingest (`make ingest-dma`), oldest first, 2 workers, disk guard on. Leave it running; record the command, the first ETA line, and how to resume.
5. GFW (`make probe-gfw`): pick 5 tanker IMOs with a sanctions topic from the OpenSanctions maritime CSV (never from memory). Vessels search by IMO; fetch GAP, ENCOUNTER, LOITERING, PORT_VISIT for 2025. Save one vessel's raw JSON to `reports/phase0_gfw_vessel.json`. Record: are identity entries dated (`transmissionDateFrom/To`), which event types return tanker events, dataset versions, rate-limit headers, pagination.
6. OFAC (`make probe-ofac`): current SDN CSV/XML (count vessels, extract IMOs), SDN advanced XML (are entry-event dates present per vessel?), and `sdnnew25.pdf` through the PDF parser. Save the extracted text of one page to `reports/phase0_ofac_sample.txt`. If the parser yields < 100 vessel actions with IMO, fix the parser against the real text and add that page as a fixture.
7. OpenSanctions (`make probe-opensanctions`): maritime CSV counts (vessels, with IMO, tankers, by source dataset); locate the dataset carrying EU Annex XLII vessels (program `EU-MARE`) and whether it has listing dates; `gb_fcdo_sanctions` vessel count and listing dates; whether dated exports exist for those slugs (probe 3 dates); PSC datasets with inspection dates and company IMO.
8. MID table (`make probe-mid`): fetch and vendor `shadowfleet/ingest/mid.csv` with source URL and fetch date.
9. LLM smoke (`make llm-smoke`, 30 minutes max): llama.cpp built per SETUP.md, one Q4_K_M model of about 12 to 14B, one prompt with a JSON schema, record tok/s and VRAM peak. If the build is fighting you after 30 minutes, record the error and move on; Phase 8 depends on it, Phase 1 does not.
10. `make report-phase0` renders `reports/phase0.md` from `reports/probes/*.json`. Add go/no-go per source and "Assumptions to confirm".

Acceptance criteria:
- `make test` passes.
- One DMA day in every ADR-14 table, with numbers in the report; bulk ingest running or a documented reason it is not.
- GFW returns >= 1 vessel id and >= 1 GAP or ENCOUNTER event for tankers, or ADR-3 is flagged for revisit.
- OFAC 2025 archive parsed to >= 100 vessel actions with IMO.
- EU and UK vessel sources located, with whether listing dates exist.
- `config/window.json` written, with cutoff counts in the report.

Next phase depends on: the running ingest, `config/window.json`, GFW client and cache, OFAC parsers, OpenSanctions source slugs.

---

## PHASE 1: DMA ingest completion

Use `phase1-prompt.md` (it supersedes the old text that was here). The per-day ingest itself was built and frozen in Phase 0 (ADR-14); Phase 1 finishes and audits the run and builds the tables derived from it.

---

## PHASE 2: Labels

Read CLAUDE.md, phase0.md, phase1.md.

Goal: a dated sanctions-action table and a `labels(cutoff, horizon)` function; the count of positives per cutoff that decides the headline label.

Inputs: OFAC probe parser; OpenSanctions downloads; `population.parquet`.

Tasks:
0. Before loading any label, write and commit `PREREG.md` (cutoff rule, primary endpoint precision@50 in the B1 stratum with label OFAC∪EU∪UK, primary model LightGBM, frozen feature list by family, ablations are reporting only, the regime break `hormuz_closure = 2026-02-28` with before/after reporting, and the Phase F forward-test protocol from ADR-17). The commit must precede the first commit that contains a positives count.
1. `shadowfleet/ingest/ofac.py`: build `sanctions_actions.parquet` from two sources. (a) SDN advanced XML: for every vessel entry with an IMO feature, read `SanctionsEntry/EntryEvent` with its `Date` and `EntryEventTypeID`, giving `action=add` (and the type id's meaning, recorded from a sample) with an exact date; 21,749 such events existed on Sep 17. (b) The yearly change archives 2022 to date (text through 2023, PDF after) for removals, modifications, and any vessel no longer on the current list. Columns: `source`, `action`, `date`, `name`, `imo`, `program`, `raw`, `via` (xml or archive). Report how many add dates agree between the two, and every disagreement over 7 days.
2. `shadowfleet/ingest/opensanctions.py`: EU = `eu_sanctions_map` Sanction entities with `programId = EU-MARE` (Annex XLII to Reg 833/2014); UK = `gb_fcdo_sanctions` vessels. Take IMO and the date from `startDate` (or `listingDate` when present). For the EU records without a date (about 55 of 729 on Sep 17), parse the CELEX id out of `sourceUrl` (for example `celex%3A32024R1745`) and map it to that regulation's Official Journal date with a small table in config, citing each entry. Dated exports return 403 without a paid token, so there is no snapshot diff; instead spot-check 5 vessels per source against the official EU and UK publications and report the deltas.
3. `shadowfleet/labels/labels.py`: `sanctioned_as_of(imo, T, sources)` (has an unrevoked add on or before T) and `labels(T, horizon, sources)` returning, for every hull in population with a valid IMO (Phase 3 refines hull ids; for now key on IMO from `ais_static` majority vote with a check-digit test), `label` in {0,1}, `designation_date`, `program`, `source`. Positive = first add by any source in `sources` within (T, T+horizon]. Population exclusion always uses all three lists regardless of `sources`: a hull listed by any of OFAC, EU or UK on or before T is not in the population (CLAUDE.md rule 3).
4. Report a table: monthly cutoff (from `config/window.json`) x {OFAC-only, OFAC∪EU∪UK} -> population size, positives, positive rate. For the OFAC-only variant also report how many positives were already EU/UK-listed at T and therefore excluded (R12). The headline label is fixed by PREREG as OFAC∪EU∪UK; there is no gate that changes it. If union positives are below 30 at more than a third of cutoffs, record it as a finding and switch reporting to quarterly aggregates per ADR-11 (scoring stays monthly).
5. Spot-check 10 positives by hand: name, IMO, designation date, and the OFAC/EU press release that names them. Put the table in the report.
6. Tests: label logic on a synthetic action table (add before T, add after T within horizon, add after horizon, remove before T).

Acceptance criteria:
- `sanctions_actions.parquet` exists with row counts per source and year.
- Cross-check mismatch with current SDN < 5 percent, or explained.
- `PREREG.md` commit precedes the positives table in git history.
- Positives table per cutoff for both label variants, with the R12 count.
- Tests pass.

Next phase depends on: `sanctions_actions.parquet`, `labels()`.

---

## PHASE 3: Identity resolution

Read CLAUDE.md and phase reports 0 to 2.

Goal: stable `hull_id`, dated identity intervals, flag-from-MID, and as-of identity-change features.

Inputs: `ais_static`, `population.parquet`, GFW vessel identity JSON structure from phase0.

Tasks:
1. `shadowfleet/resolve/identity.py`: for each (MMSI, 30-day window) take the majority IMO among check-digit-valid values in static messages; assign `hull_id = str(imo)` when a majority exists with >= 60 percent support and >= 5 messages. Otherwise query the GFW Vessels API by MMSI (cached) and use `gfw:<id>` if it resolves to a single vessel with a matching name; otherwise `syn:<sha1(mmsi, modal name, length, width)>`. Write `data/parquet/hull_map.parquet` (mmsi, window_start, window_end, hull_id, method, support).
2. Identity intervals: from static messages ordered by time per hull, build `identity_intervals.parquet` (hull_id, mmsi, name_normalised, callsign, flag_iso3 from MID, start, end). Merge intervals that differ only by whitespace/case. A boundary is an identity change event with `observed_at = start of the new interval`.
3. GFW identity merge: if phase0 shows GFW identity records carry date ranges, add them as additional intervals with `source = gfw`; if not, record GFW identity as undated and do not use it for features.
4. As-of features in `shadowfleet/features/identity.py`: `n_name_changes`, `n_mmsi_changes`, `n_flag_changes`, `flag_to_convenience_registry` (list in config with source), `days_since_last_identity_change`, `current_flag`, `vessel_age_years` (from GFW registry `built` year if available, else null), all computed from records with `observed_at <= T`.
5. Fragmentation test: for 20 IMOs from `sanctions_actions` that appear in population, assert each maps to exactly one `hull_id` and list any that fragment.
6. Silver test set: IMO-MMSI pairs from OpenSanctions vessels and from GFW identity records; report resolution precision/recall on it, and the share of hulls whose hull_id depends on a GFW merge (these are disabled in the Phase 5a sensitivity arm).
7. Population cross-check: from `vessel_day` and `type_changes`, list MMSIs that GFW classifies as tanker or whose dimensions fit a tanker but were never tanker-class in AIS; report the count and whether any are later-listed.
8. Report: share of population by hull_id method; distribution of identity-change counts; examples of the most-churned hulls.

Acceptance criteria:
- >= 80 percent of population MMSI-days map to an IMO-based hull_id; otherwise report and note ADR-5 revisit.
- Fragmentation test passes for >= 18 of 20.
- `features/identity.py` returns identical output from full and truncated stores for 50 hulls at the first cutoff in `config/window.json` (write this as a test; it becomes part of the Phase 5 leakage suite).
- Tests pass.

Next phase depends on: `hull_map.parquet`, `identity_intervals.parquet`, identity features.

---

## PHASE 4a: GFW events

Read CLAUDE.md and phase reports 0 to 3.

Goal: population-limited, cached pull of GFW events with `observed_at`, and a coverage report.

Inputs: GFW client and cache; `hull_map.parquet`; window bounds.

Tasks:
1. Resolve GFW vessel ids for every hull (by IMO first, then MMSI + date range). Store `gfw_vessel_map.parquet` (hull_id, gfw_vessel_id, match_method).
2. Pull GAP, ENCOUNTER, LOITERING, PORT_VISIT events for each vessel id for the full window plus 180 days before it. Respect rate limits with backoff; cache every response; resumable. Flatten to `data/parquet/gfw_events/` with columns: hull_id, event_type, start, end, `observed_at = end`, lat, lon, duration_h, and type-specific fields: gap `implied_speed_knots`, `distance_km`, off/on positions; encounter `partner_gfw_id`, `partner_hull_id` (via map), `median_speed_knots`, `median_distance_km`; port_visit port name, country, `confidence`; loitering `avg_speed`, `distance_from_shore_km`.
3. Russian-port flag: port visits at ports in a config list (Primorsk, Ust-Luga, Vysotsk, St Petersburg, Novorossiysk, Tuapse, Taman, Murmansk, Kozmino, plus any others documented) or port_visit positions inside Russian-port polygons in config.
4. Record the dataset version string from each response in the Parquet metadata. Never persist registry ownership fields into feature tables.
4b. Re-test the Phase 0 finding that the public encounter dataset returns nothing for tankers: report the share of the population with any ENCOUNTER event. If it is effectively zero, record it as a finding, keep the (empty) features in the registry, and note that Phase 4b carries the at-sea transfer signal alone.
5. Coverage report: percent of population hulls with any GFW id; with any event of each type; events per hull distribution; whether LOITERING returns tanker events at all.
6. `--no-gfw` flag in config so downstream phases run with empty GFW tables.
7. Tests: flattening on a saved sample response; observed_at assignment; partner mapping.

Acceptance criteria:
- `gfw_events/` populated; coverage report with numbers.
- Pipeline runs end to end with `--no-gfw`.
- Tests pass.

Next phase depends on: `gfw_events/`, `gfw_vessel_map.parquet`.

---

## PHASE 4b: Self-built detectors

Read CLAUDE.md and phase reports 0 to 4a.

Goal: detection layers the project owns, built on DMA tracks, each record with `observed_at`, so the evaluation can separate GFW's detections from ours (R14, ADR-12).

Inputs: `ais_dynamic` (60 s, 30 s below 3 kn), `ais_static`, `hull_map.parquet`, `identity_intervals.parquet`, port and anchorage polygons in config, the STS-readiness count from phase1.md.

Tasks:
1. `detect/sts.py`: STS candidates = two tanker-class hulls within 500 m, both SOG < 2 kn, for > 2 h, outside port polygons. Record start, end (`observed_at = end`), centroid, min distance, and each hull's reported draught in the 48 h before and after. Use a 0.01-degree spatial bucket join per minute in DuckDB, not a Python double loop.
2. `detect/loitering.py`: anchorage loitering = SOG < 1 kn for > 12 h inside the Skagen anchorage polygon or other configured anchorages, outside port polygons.
3. `detect/draught.py`: declared-vs-observed draught inconsistency = draught change > 1 m with no port polygon visit and no STS candidate in between; and draught changes that do coincide with an STS candidate (the lightering signal).
4. `detect/spoof.py`: per hull per day, jump rate minus the `jump_baseline` for the cells visited (the normalised feature input for 5a).
5. MMSI-IMO churn: per hull, dated changes in MMSI for a stable IMO and in IMO for a stable MMSI, from `identity_intervals`.
6. Plot STS candidates at Skagen by month; hand-check 10 candidates (tracks plotted, both hulls named) and describe them in the report.
7. Tests: each detector on synthetic tracks with known answers, including a near-miss (400 m for 1.5 h) that must not fire.

Acceptance criteria:
- Detection tables with counts and `observed_at`; Skagen plot; 10 hand-checked candidates.
- If STS candidates are zero, the detector is kept, the finding is written, and 5a drops nothing (ablations report it).
- Tests pass.

Next phase depends on: `detect/` tables.

---

## PHASE 5a: Feature store and leakage suite

Read CLAUDE.md, PREREG.md, and phase reports 0 to 4b. This is the most important phase; take it slowly and keep the leakage tests honest.

Goal: `features(hull_id, T)` for the frozen PREREG feature list and the leakage test suite. No model is scored in this phase.

Inputs: all Parquet tables from Phases 1 to 4b; `labels()` (for population exclusion only).

Tasks:
1. `shadowfleet/features/asof.py`: `store(T)` returns DuckDB views of every table filtered to `observed_at <= T`. `features(T)` returns a DataFrame with one row per hull in population_T (observed in [T-180d, T] and not listed by any of OFAC, EU or UK on or before T). Feature families, all computed only from `store(T)` and the 180 d window unless noted:
   - identity (Phase 3), plus lifetime counts of changes as of T.
   - ais (from DMA): n_transits (entering and leaving Danish waters), n_laden_transits (draught above hull's 75th percentile), n_ballast_transits, share of transits with destination text matching a Russian port, mean transit speed, spoof_jump_rate_excess (vessel jump rate minus the cell-day baseline), n_days_observed.
   - gfw: n_gaps, gap_hours_total, max_gap_distance_km, n_gaps_offshore (start > 50 nm from shore if available), n_encounters, n_encounters_with_sanctioned_partner (partner listed by any of OFAC, EU or UK as of T), n_loitering, loitering_hours, n_port_visits, n_russian_port_visits, days_since_last_russian_port_visit.
   - detect (Phase 4b): n_sts_candidates, n_sts_with_draught_change, anchorage_loitering_hours, n_draught_inconsistencies, n_mmsi_imo_churn.
   - static: vessel_age_years, length, dwt if available.
   Each feature is tagged with its source (`dma`, `gfw`, `self_built`) so 6 can run GFW-only and self-built-only arms.
   Every feature has a docstring-level one-liner in a `FEATURES` registry (name, family, description) used by the brief bundler.
2. Leakage suite in `tests/test_leakage.py`: (a) equality of `features(T)` computed from the live store vs a physically truncated copy for 200 sampled hulls at each cutoff; (b) static analysis: no module under `features/` imports from `labels/` except `sanctioned_as_of`, no feature column name contains `sanction` except the partner feature, and no GFW registry ownership field is read; (c) permutation test hook for the harness; (d) reverse-time check hook; (e) entity-resolution sensitivity hook (features rebuilt with GFW-based merges disabled).
3. Freeze: the FEATURES registry must equal the PREREG list; a test asserts it. Anything added later is flagged `post_hoc=True`.
4. Write `feature_matrix/cutoff=T/` for every monthly cutoff, with build time per cutoff.

Acceptance criteria:
- All leakage tests pass (a, b now; c, d, e wired as hooks for 5b).
- Feature matrix for every monthly cutoff; registry equals PREREG.
- Tests pass.

Next phase depends on: `features(T)`, FEATURES registry, leakage hooks.

---

## PHASE 5b: Baselines and backtest harness

Read CLAUDE.md, PREREG.md, and phase reports 0 to 5a.

Goal: rules baselines, the harness, metrics, and the first honest tables.

Tasks:
1. Rules in `shadowfleet/models/rules.py`: B0 random; B1 = `n_russian_port_visits > 0 or share_russian_destination > 0`; B1b = listed by a Western authority other than the one defining the label (OFAC-only table only; exposes R12); B2 = fixed weighted sum with weights written in the file before any label is loaded (document them): 3·B1 + 2·[gap_hours_total>24] + 2·[n_encounters_with_sanctioned_partner>0] + 1·[flag_to_convenience_registry] + 1·[vessel_age>15] + 1·[n_name_changes>=1] + 1·[spoof_jump_rate_excess>0]. B3 = logistic regression (sklearn, standardised, class_weight balanced).
2. Harness `shadowfleet/backtest/harness.py`: for each cutoff: build features and labels, score every model, compute metrics (`metrics.py`: precision@k, recall@k for k in 25/50/100, PR-AUC, alert volume at 50 percent recall, FPR at k=50, calibration inputs), also on the B1-positive stratum, for both label sets. Also a first-appearance evaluation (each hull scored only at its first eligible cutoff). Lead time is event-study: designation date minus the earliest cutoff at which the hull entered the top k; per-cutoff lead time is a supplement. Run the permutation, reverse-time and entity-resolution hooks. Supervised models train on earlier cutoffs whose horizon closed before T; the first cutoff gets rules and unsupervised only. Write `reports/metrics_<label>_<cutoff>.csv`, monthly and quarterly aggregate tables, a lead-time figure, and `reports/phase5b.md`.
3. `make backtest` runs the leakage suite then the harness. It must fail if any leakage test fails.

Acceptance criteria:
- All leakage tests pass; permutation test drops PR-AUC to near base rate for B3; reverse-time check is not dramatically better than forward.
- Metrics tables exist for every cutoff and both label sets, with the stratified table, first-appearance table and entity-resolution delta.
- Report states plainly how much of the lift is B1 and what B3 adds.
- Runtime of `make backtest` under 30 minutes on the target machine.

Next phase depends on: harness, metrics, rules.

---


## PHASE 6: Models, ablations, drift, calibration, SHAP

Read CLAUDE.md, PREREG.md, and phase reports 0 to 5b.

Goal: LightGBM and isolation forest in the harness, ablations by feature family, calibration, and per-(hull, T) SHAP contributions for the brief bundler.

Tasks:
1. `models/tabular.py`: LightGBM with conservative settings (num_leaves 15, min_child_samples 20, feature_fraction 0.8, early stopping on the most recent training cutoff held out as validation, `scale_pos_weight` set from the training base rate). No hyperparameter search beyond three seeds averaged.
2. `models/anomaly.py`: isolation forest on the feature matrix (no labels), score reported as a model in its own right and as an extra feature to LightGBM in an ablation arm.
3. Ablations (reporting only; never drive feature removal): drop one family at a time (identity, ais, gfw_gaps, gfw_encounters, gfw_ports, detect, static), plus GFW-only and self-built-only arms; table of PR-AUC and P@50 per arm per cutoff.
3b. Drift: PSI per feature family per cutoff against the training window, and performance by training-window age. Report both split at `config.REGIME_BREAKS['hormuz_closure']` (cutoffs before vs on or after 2026-02-28), and say how many post-break cutoffs have a closed horizon.
4. Calibration: reliability diagram and Brier score for LightGBM at each scored cutoff.
5. SHAP: TreeExplainer contributions for every scored (hull, T); write `data/parquet/shap/cutoff=T/` with top-10 features and values per hull.
6. Top-k lists: `reports/flagged_<cutoff>.csv` with hull_id, score, rank, label, designation_date, and the top-5 SHAP features.
7. False-positive review sheet: `reports/fp_review_<quarter>.csv` with the top 20 non-listed flags per quarterly aggregate and a blank `reason` column (lightering, CPC crude, ice-class, unknown) for Adam.
8. Update the harness and reports; extend `reports/phase6.md` with the honest comparison to B2 and B3.

Acceptance criteria:
- Ablation, drift and calibration outputs exist; FP review sheet generated.
- SHAP tables exist for every cutoff.
- The report contains a paragraph answering: does ML beat rules, and by how much within the B1 stratum; anything added after 5b results is labelled post-hoc. Either answer is acceptable.
- Leakage suite still passes; runtime under 45 minutes.

Next phase depends on: flagged lists, SHAP tables.

---

## PHASE 7 (optional): Graph layer

Read CLAUDE.md and phase reports 0 to 6. Skip this phase if the schedule is behind; Phase 8 does not need it.

Goal: encounter graph built strictly as-of T, personalised PageRank from sanctioned seeds as a feature, optional dated manager-company edges, and the marginal lift.

Tasks:
1. `features/graph.py`: for each T, build an igraph undirected graph of hulls from GFW encounters with `end <= T` (edge weight = number of encounters in the last 365 d). Seeds = hulls sanctioned as of T. Features: `ppr_from_sanctioned`, `n_sanctioned_neighbors_1hop`, `n_sanctioned_neighbors_2hop`, degree.
2. Manager edges: from OpenSanctions PSC datasets, edges (hull, company_imo, inspection_date) with `observed_at = inspection_date`; add company nodes; a company is "linked to sanctioned" if any of its vessels was sanctioned as of T. Feature `shares_manager_with_sanctioned_as_of_T`. Report coverage (percent of population with any dated manager edge).
3. Harness arm: LightGBM + graph features; marginal lift table.
4. Analysis for the README: among hulls that met an already-sanctioned hull at sea before T, the fraction designated within the horizon vs the base rate.

Acceptance criteria:
- Graph built from as-of data only (the leakage equality test extended to graph features).
- Lift table and the encounter analysis in `reports/phase7.md`.

---

## PHASE 8: Analyst briefs

Read CLAUDE.md and phase reports 0 to 6 (and 7 if done).

Goal: evidence bundles, local LLM serving, schema-constrained brief generation, and rendered prose for the top 50 flagged hulls per cutoff.

Setup notes: build or install llama.cpp with CUDA 12.8 support (Blackwell). Download the Q4_K_M GGUF of the configured model (config `LLM_MODEL`, default a Gemma 4 12B instruct quant; fallback Qwen3-14B instruct quant; verify current file names on Hugging Face). Start `llama-server` with `-ngl 99 -c 8192 --jinja` and confirm it fits in VRAM with headroom; log peak VRAM. Nothing else may use the GPU.

Tasks:
1. `briefs/bundle.py`: for (hull, T), assemble an evidence bundle: header (hull_id, names and flags as of T, age, size), the top-8 SHAP features with values and their FEATURES descriptions, and the concrete records behind each feature (each identity change, each gap with dates/positions/duration/implied distance, each encounter with partner hull and partner listed-as-of-T status, each Russian port visit, each self-built STS candidate or draught inconsistency, spoof artefact days), every record with an id `E1..En`, dates as ISO strings, positions rounded to 2 decimals. Serialize to JSON. Cap at roughly 3,000 tokens by truncating the least-important families.
2. `briefs/generate.py`: system prompt describing an analyst writing a sanctions-risk brief that must only state facts present in the bundle and must attach evidence ids to every claim; user message = the bundle. JSON schema for the output: `{summary: str, risk_level: enum, findings: [{claim: str, evidence_ids: [str], severity: enum}], caveats: [str]}`. Use llama.cpp's JSON-schema constrained decoding. Temperature 0.2. Retry once on schema failure.
3. Renderer: JSON -> Markdown brief with `[E#]` citations inline and an appendix listing the cited evidence records.
4. Batch run for the top 50 at each cutoff for the best model from Phase 6; store `reports/briefs/<cutoff>/<hull>.md` and `.json`; log tokens/s and total time.
5. Tests: bundle builder on a synthetic hull; renderer; schema validation.

Acceptance criteria:
- Briefs generated unattended for all cutoffs; throughput and VRAM peak in `reports/phase8.md`.
- Every brief has a JSON with valid schema and a rendered Markdown.
- A sample of 3 briefs pasted in the report for Adam to sanity-read.

Next phase depends on: brief JSONs and bundles.

---

## PHASE 9: Faithfulness evaluation

Read CLAUDE.md and phase reports 0 to 8.

Goal: measure brief faithfulness with a deterministic verifier, an LLM judge, and a human audit sheet.

Tasks:
1. `briefs/verify.py`: for each brief: (a) every `evidence_ids` entry exists in the bundle; (b) coverage: each of the top-5 SHAP features has at least one finding citing one of its records; (c) grounding: extract all dates, integers, durations, coordinates, and capitalised names from the rendered prose; each must appear in the bundle after normalisation; list violations; (d) no claim cites an evidence id whose record family is unrelated to the claim's key terms (simple keyword map per family). Output `faithfulness.json` per brief and an aggregate table.
2. LLM judge: a different model family from the generator (Qwen judges Gemma, or the reverse; only one llama-server on the GPU at a time, so run generation and judging as separate passes), temperature 0, one call per finding: given the cited evidence records only, answer `entailed`, `partially`, or `not_entailed` with a one-line reason, JSON-constrained. Aggregate entailment rates per cutoff and per severity.
3. Human audit sheet: `reports/audit_sheet.csv` with 30 randomly sampled findings (stratified by judge verdict), columns for Adam to fill: human_verdict, error_type (fabricated_fact, wrong_date, unsupported_inference, misattributed_evidence, none). Provide a tiny CLI to compute judge-vs-human agreement (Cohen's kappa) once filled.
4. Optional second arm if time: free-form generation with the same verifier, to compare faithfulness of schema-first vs free-form.
5. `reports/phase9.md`: faithfulness headline (percent of briefs with zero verifier failures and full entailment), failure taxonomy, and the agreement number placeholder until Adam fills the sheet.

Acceptance criteria:
- Verifier and judge run over all briefs; aggregate tables exist.
- Audit sheet generated; agreement CLI works on a filled example.

---

## PHASE 10: Report, reproducibility, portfolio

Read CLAUDE.md and all phase reports.

Goal: a README that a hiring manager in compliance or defense can read in five minutes, a reproducible `make all`, and the numbers for resume bullets.

Tasks:
1. `README.md`: problem, theater and why, data sources with licences, point-in-time design (with the leakage test description), headline results table (per cutoff and pooled, both label sets, stratified), lead-time figure, rules-vs-ML finding, ablations, brief example with faithfulness numbers, limitations (ownership invisibility, label reasons, GFW model timing, lead-time bounds, non-commercial licences), how to run. No invented numbers; every number links to a file in `reports/`.
2. `make all` = ingest (skips completed days), labels, identity, gfw, backtest, briefs, verify. Test it from a clean clone with data directories present. Document machine time per stage.
3. `reports/portfolio.md`: the resume bullets from the plan with placeholders replaced by measured values, one version phrased for fintech compliance and one for defense MDA, plus a 60-second spoken summary of the project and its honest limitations.
4. Repo hygiene: remove dead code, ensure tests pass, tag `v1.0`.

Acceptance criteria:
- Clean-clone `make all` completes or the README documents the exact prerequisites.
- README numbers match `reports/` files.
- `reports/portfolio.md` complete with no placeholders.

---

## PHASE F: Forward test (on or after 2026-10-01; evaluation in spring 2027)

Read CLAUDE.md, PREREG.md, ADR-17, and phase reports 0 to 6.

Goal: a prospective, tamper-evident prediction that complements the backtest.

Tasks:
1. Ingest DMA through the scoring date (`make ingest-dma --end <date>` after extending `config/window.json` end with a recorded reason) and refresh GFW events for the population.
2. Score the population at T = the scoring date with the frozen PREREG model. If Phase 6 is not finished, score with B2 and label the file as the rules baseline.
3. Write `reports/forward/top50_<T>.csv` (rank, hull_id, imo, score, top-5 feature names; no GFW-derived values) and its SHA-256 in `reports/forward/README.md`. Commit and push the same day; the commit timestamp is the evidence.
4. Add `make forward-eval T=<date>` that reads `sanctions_actions` as of the evaluation date and reports precision@k, recall@k, and lead time for the committed list, never modifying the list.
5. Run the evaluation monthly until T + 182 d; report the final numbers in the README next to the backtest, labelled prospective.

Acceptance criteria:
- The ranked list is in git history dated on or after 2026-10-01 and before any evaluation.
- `forward-eval` is read-only with respect to the list; a test asserts the file hash is unchanged.
