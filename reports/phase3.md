# Phase 3 report (generated 2026-10-02 by `make identity`)

## Hull ids

- `data/parquet/hull_map.parquet`: 38839 (MMSI, 30-day window) rows for 6605 MMSIs, 33403 of them resolved by IMO majority vote.
- 8810 distinct hull ids, 4145 of them synthetic.
- Coverage: 221350 of 274650 kept MMSI-days carry an IMO-based hull id (0.8059). **PASS**
- Of the MMSI-days that have any hull id, 0.9182 are IMO-based; 33576 MMSI-days are the pre-first-window warm-up, which has no hull id by design.
- The vote is cumulative over everything observed up to the end of a window, and takes effect the day after that window closes, so no cutoff reads a static message from after itself. A vessel has no hull id until its first window closes; that is the warm-up above.
- No GFW identity merge was built: IMO majority meets the bar on its own, so no hull id depends on a GFW model and the Phase 5a entity-resolution sensitivity arm has nothing to disable.

### What the thresholds cost

| min IMO-days | min support | share of MMSI-days by IMO | |
|---:|---:|---:|---|
| 1 | 0.6 | 0.8649 |  |
| 2 | 0.6 | 0.8509 |  |
| 3 | 0.6 | 0.8375 |  |
| 5 | 0.6 | 0.8059 | **chosen** |
| 5 | 0.8 | 0.8059 |  |
| 10 | 0.6 | 0.7261 |  |

## Identity intervals

- `data/parquet/identity_intervals.parquet`: 15290 intervals over 6605 transmitters; 6164 changed name, callsign or flag at least once. Intervals are keyed by transmitter; which hull a transmitter belongs to is decided at each cutoff (ADR-23).
- Flags resolved from 292 ITU MID rows; 15259 intervals carry a flag.

## Checks

- Fragmentation: at cutoff 2026-09-17, 20 of 20 designated IMOs have every transmitter that carried them mapped to one hull id. Fragmented: []
- Silver set (OpenSanctions IMO-MMSI pairs): data/cache/opensanctions/maritime.csv missing; run `make probe-opensanctions` first
- Population cross-check: 4096 MMSIs are tanker-sized with an unknown reported type and were never tanker-class; 47 of them were later designated.

## Assumptions to confirm

- Deviation from the Phase 3 prompt, both recorded here rather than assumed away: the vote is cumulative rather than per-window, because a per-window vote with a warm-up leaks at any cutoff landing inside a vessel's first window, which month-end cutoffs do; and a vote is one per day an IMO was broadcast, not one per message, because `ais_static` is change-point compressed at ingest (ADR-14) so message counts are not comparable between hulls.
- 30-day windows, 60 percent support, 5 IMO-days: the prompt's numbers, on day votes.
- `vessel_age_years` stays null until Phase 4a brings the GFW registry build year.
- Null static fields are carried forward, so a message that omits a field is not a change.
