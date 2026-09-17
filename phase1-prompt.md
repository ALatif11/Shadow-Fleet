# PHASE 1: DMA ingest completion (revised Sep 17 2026; supersedes the Phase 1 section of phase-prompts.md)

You are implementing Phase 1 of Shadow Fleet. Read CLAUDE.md, then reports/phase0.md.

Change note (Sep 17): because raw files are deleted as they are read, the per-day ingest (old task 1), the spoof artefact and jump-baseline tables (old task 7) and the full-resolution sample for task 8 were moved into Phase 0 and frozen there (ADR-14). The bulk run was started in Phase 0. In this phase you audit that run rather than write it; if you find a defect in the frozen ingest, stop the run, fix, add a test, and list the affected days for re-ingest only if DMA still has them.

Goal: complete and audit the unattended ingest of the DMA window fixed in Phase 0 (`config/window.json`) into tanker-class Parquet, preserving everything Phases 3 and 4b need (static messages, draught, dimensions, 60 s positions for proximity detection), plus the population table, a reported-type-change table, normalised spoof artefacts, and the evidence that DMA gaps are coverage artefacts. This phase is the one most likely to overrun on wall-clock time; design for interruption, throttling, and schema drift from the first line.

Inputs: the frozen ingest in `shadowfleet/ingest/dma.py`; `config/window.json` (gated in Phase 0 per the ADR-11 addendum); the per-day benchmark numbers in phase0.md; the per-day `.done` markers and `reports/logs/dma_ingest.csv` from the running job.

Tasks:

1. **Audit the frozen per-day ingest (built in Phase 0).** Confirm on a sample of 10 completed days that the markers, row counts and schemas match the spec below and phase0.md; list any day whose malformed-row share exceeds 5 percent. Spec for reference: `ingest_day(date)`: download if missing (single retry with backoff; honour DMA throttling; never more than two concurrent downloads), stream-filter, write, delete. Read with an explicit schema, not inference (Phase 0 streams the zip member through pyarrow into a temporary Parquet, then DuckDB); DMA has changed columns and encodings across years, so detect the header per file, map to a canonical snake_case schema in `config.DMA_SCHEMA`, and log any unknown or missing column with the date. Malformed rows are skipped and counted, never fatal; a day with more than 5 percent malformed rows is flagged in the report. Outputs per day:
   - `ais_dynamic/dt=<date>/`: position reports for tanker-class MMSIs, deduplicated, downsampled to one row per MMSI per 60 s, or per 30 s when SOG < 3 kn (keep the first), columns: `observed_at`, `mmsi`, `lat`, `lon`, `sog`, `cog`, `heading`, `nav_status`, `rot`, `mobile_type` (Class A/B), `pos_fix_type`, `data_source`.
   - `ais_static/dt=<date>/`: every static/voyage row for tanker-class MMSIs, no downsampling: `observed_at`, `mmsi`, `imo`, `callsign`, `name`, `ship_type`, `cargo_type`, `length`, `width`, `draught`, `destination`, `eta`, `dims_a..d`.
   - `ais_artifacts/`, `jump_baseline/` (all vessels) and `vessel_day/` (all vessels), see task 7 and ADR-14.
   - A `.done` marker with row counts and elapsed seconds; days with markers are skipped.
   Tanker-class = `ship_type` tanker OR (`ship_type` cargo AND `cargo_type` hazardous) AND (`length` >= 100 m OR length missing). The day's MMSI set also includes the persistent registry of MMSIs seen as tanker-class on earlier days. Also retain any MMSI listed in `config.EXTRA_MMSI_ALLOWLIST` (empty for now; Phase 3 may add hulls that mis-report type). Do not filter on `mobile_type`; keep Class B rows so Phase 4b can test whether shadow tankers ever broadcast as Class B.

2. **Finish the window.** `make ingest-dma` (started in Phase 0) processes all days in `[WINDOW_START, WINDOW_END]`, oldest first, resumable after any crash, with a per-day timing CSV in `reports/logs/dma_ingest.csv` (date, zipped bytes, rows in, rows out, seconds download, seconds filter). Print an ETA every ten days. Corrupt or missing days are logged and skipped; list them in the report. Add `make ingest-dma-check` that lists days without markers so Adam can re-run gaps.

3. **Mid-run gate.** After the first 30 days, compare measured throughput and Parquet bytes per day to the phase0 extrapolation. Machine-hours alone are not a reason to shrink the window. Shrink only if (a) projected Parquet exceeds free disk minus `MIN_FREE_GB`, or (b) the run cannot finish before DMA's rolling deletion overtakes the oldest unfetched day (compare the live earliest-available date with phase0.md to get the deletion cadence). Never move `WINDOW_START` to a date that leaves under 21 months without Adam's sign-off, because shorter windows leave no supervised cutoffs (ADR-11 addendum). Record old and new bounds in `config/window.json` and the report; never shrink silently.

4. **Population table.** `data/parquet/population.parquet`, one row per MMSI: `first_seen`, `last_seen`, `n_days_observed`, `n_dynamic_rows`, modal `ship_type`, modal `length`, modal `width`, modal `name`, share of rows as Class B. Also `reports/phase1_population_by_month.csv` (distinct tanker-class MMSIs per month) and a plot.

5. **Reported-type-change table.** `data/parquet/type_changes.parquet`: for each MMSI, every change in reported `ship_type` or `cargo_type` between consecutive static messages, with `observed_at`, old and new values. This feeds the population cross-check in Phase 3 (a hull that stops reporting as a tanker must not silently drop out of the study). Report the count of MMSIs with at least one change.

6. **Coverage evidence for the gap claim.** For each MMSI compute gaps between consecutive dynamic rows longer than 6 h. For a random sample of 500 gaps, record the last position before the gap and whether it lies within 10 km of the DMA coverage edge (edge = boundary of the concave hull of all positions in the window at 0.1-degree resolution, or a Danish EEZ polygon if available offline; document which). Plot the gap-length histogram and the coverage-edge fraction to `reports/phase1_gaps.png`. Expected: nearly all long gaps begin at the edge. State the conclusion in the report in one sentence: DMA gaps are not evasion features.

7. **Spoof artefacts, normalised (computed at ingest since Phase 0; verify here).** `data/parquet/ais_artifacts/dt=<date>/`: per MMSI per day, `n_jumps` (implied speed > 50 kn and distance > 1 km between consecutive deduplicated full-resolution rows), `n_bad_positions` (0,0 or outside the Baltic/North Sea bounding box in config), and `cell_ids` of the 0.5-degree cells where jumps occurred. Also write `data/parquet/jump_baseline/dt=<date>/`: per (date, 0.5-degree cell) the fraction of all observed MMSIs with at least one jump. The Phase 5a feature will be the vessel's jump rate minus this baseline; store both sides here so GNSS-interference days can be washed out later without re-reading tracks.

8. **STS-readiness check (no detector yet).** Confirm that the downsample preserves what Phase 4b needs: on the day(s) ingested with `--keep-fullres` (Phase 0 benchmark day; add one more recent day with `make ingest-day DATE=... FULLRES=1` if DMA still has it), count pairs of tanker-class MMSIs simultaneously within 500 m at under 2 kn for over 2 h in the Skagen anchorage polygon, from `ais_fullres` and from `ais_dynamic`, and report both. The 30 s slow-speed interval is already the default; if pairs are still lost, record it and propose a change before more days are ingested.

9. **Tests.** Phase 0 already covers filter logic, two header variants, downsampling, idempotent re-run, malformed rows, jumps and baseline. Add tests for population, type changes, and gap-edge logic.

10. **Report.** `reports/phase1.md`: window actually ingested (old and new bounds if gated), days failed and why, total Parquet size and rows per table, throughput and total machine-hours, population and monthly counts, type-change count, gap plot conclusion, STS-readiness count, assumptions to confirm, and what Phase 2 and Phase 3 depend on.

Acceptance criteria:
- `make ingest-dma` completes for the configured window, or the mid-run gate has been applied and documented; failed days under 3 percent.
- `data/raw/dma/` empty except in-flight files; total Parquet size reported.
- `population.parquet`, `type_changes.parquet`, `ais_artifacts/`, `jump_baseline/`, `vessel_day/` exist with counts in the report.
- Gap plot and coverage-edge fraction reported with the one-sentence conclusion.
- STS-readiness count reported, with the downsample decision recorded in config.
- Tests pass.
- Nothing under `data/` is committed.

Next phases depend on: `ais_static` and `type_changes` (Phase 3 identity and population cross-check), `population.parquet` (Phases 2 and 3), `ais_dynamic` at 60 s or finer (Phase 4b self-built detectors), `ais_artifacts` and `jump_baseline` (Phase 5a).
