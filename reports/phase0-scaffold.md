# Phase 0, session 1: architecture reconciliation and scaffold (Sep 17 2026)

This session ran in a Cowork cloud sandbox that could not reach DMA, OFAC, OpenSanctions or GFW. So it produced code and tests only. `reports/phase0.md` is generated later by `make report-phase0`, from the live probes run on Adam's machine. No project metric exists yet.

## Decisions taken with Adam
- Shadow Fleet is back on as the main project (it was shelved Sep 12 in favour of an entity-matching project).
- Scope of this session: reconcile the plan documents, then scaffold and build Phase 0.
- Runtime: WSL2 on Adam's PC (`SETUP.md`).
- Disk: Adam frees space before the bulk ingest; the code enforces a guard regardless.

## What changed in the plan (details in shadow-fleet-plan.md, revision items 12 to 19, ADR-13 to ADR-16)
- The 15-month window fallback was removed. It leaves 3 evaluable cutoffs and 0 supervised ones. The gate now keeps the full history unless disk forces a shorter window, and never goes below 21 months without Adam's sign-off.
- The ingest schema is frozen in Phase 0, because raw DMA is deleted as it is read. `jump_baseline` and `vessel_day` are computed over all vessels at ingest time. The old plan computed the jump baseline after the raw files, which carry every vessel, had already been deleted.
- Phase 0 no longer downloads everything before filtering, which would have needed 300 to 700 GB. Each file is filtered and deleted as it arrives.
- Label sources corrected:
  - OFAC's 2024 to 2026 change archives exist only as PDF.
  - EU vessels come from Annex XLII (program EU-MARE), not the EU FSF file.
  - UK vessels come from `gb_fcdo_sanctions`.
- The GFW "intentional gap" feature was dropped (all public gaps have been intentional since Aug 2025). Registry ownership fields are banned from features.
- `phase-prompts.md` was reconciled:
  - quarterly cutoffs, the OFAC-only headline gate and "headline-label-only" population exclusion were all removed;
  - Phases 4a/4b and 5a/5b were split out;
  - the Phase 9 judge is now a different model family from the generator.
- CLAUDE.md rule 9 was amended and rule 10 ("do not break earlier phases") was added.

## What was built
| module | purpose |
|---|---|
| `config.py` | paths, window/cutoff rule, DMA schema aliases, filters, GFW/OFAC/OpenSanctions endpoints |
| `ingest/dma.py` | index parser (daily and monthly archives), resumable download, zip stream to temp Parquet, per-day ADR-14 tables, tanker registry, markers, bulk runner, probe |
| `ingest/window.py` | window gate |
| `ingest/gfw.py` | v3 client, SHA-256 cache, retry/backoff, pagination, rate-limit log, probe |
| `ingest/ofac.py` | SDN CSV parser, advanced XML summary, change-archive parser (text and PDF), probe |
| `ingest/opensanctions.py` | maritime CSV summary, FtM vessel-sanction summary, dated-export check, probe |
| `ingest/mid.py` | ITU MID table to ISO3 |
| `briefs/llm_smoke.py` | JSON-schema smoke test against llama-server |
| `util/` | disk guard, HTTP download with Range resume, JSON logging, IMO check digit, probe files, doctor, phase 0 report renderer |
| `cli.py`, `Makefile` | every command above |

## Commands
See `SETUP.md` section 7.

## Measured in the sandbox (not project numbers)
- `make test`: 43 tests pass; `ruff` clean.
- Synthetic load test: 5,000,000 rows (2,000 MMSIs), 86 MB zip, on 2 CPUs with a 4 GB DuckDB limit. Results:
  - stage 1 took 9.3 s and stage 2 took 11.8 s;
  - peak RSS was 3.5 GB;
  - `ais_dynamic` came to 7.8 MB for 400 kept MMSIs.

  Real DMA days differ in size and content; `make probe-dma` gives the real figure.

## Assumptions to confirm (Phase 0 session 2 checks each against live data)
- DMA header, timestamp format and `Cargo type` values match config.
- DMA rows carry static fields, so change-point compression of `ais_static` is lossless.
- The OFAC change-archive parser handles the real 2025 PDF text. It was built against a synthetic fixture (`tests/fixtures/ofac_changes_synthetic.txt`) and must be re-tested on `reports/phase0_ofac_sample.txt`.
- GFW v3 parameter shapes (`vessels[0]`, `datasets[0]`, `start-date`, `nextOffset`) and the `selfReportedInfo` fields match the live API.
- The OpenSanctions maritime CSV has `imo`, `risk` and `datasets` columns, and `index.json` lists resources with `name`/`url`/`size`.
- The ITU MID page is a parseable HTML table.
- The Skagen box and the convenience-flag list are placeholders.

## Next
Phase 0 session 2, on Adam's machine: follow the Phase 0 prompt in `phase-prompts.md`, starting with `make doctor` and `make probe-dma`, and start the bulk ingest the same day.
