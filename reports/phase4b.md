# Phase 4b report (generated 2026-09-29 by `make detect`)

Detection layers built on DMA tracks only. No GFW field is read here, which is what lets Phase 6 report a self-built-only arm against a GFW-only arm.

## STS candidates

- 25 candidates over 31 months, at 500 m / 2.0 kn / 2.0 h.
- Table: `data/parquet/detect_sts`. `observed_at` is when the transfer ended.

Where they cluster (this replaces the hand-drawn Skagen anchorage box; the clusters come out of the data):

| lat | lon | events | hours | transmitters |
|---:|---:|---:|---:|---:|
| 55.45 | 10.5 | 5 | 117.5 | 4 |
| 57.65 | 11.85 | 2 | 23.8 | 2 |
| 55.35 | 18.7 | 1 | 15.9 | 1 |
| 55.55 | 9.75 | 1 | 12.1 | 1 |
| 57.6 | 10.7 | 1 | 10.0 | 1 |
| 57.4 | 10.55 | 1 | 7.7 | 1 |
| 57.65 | 10.6 | 2 | 5.9 | 2 |
| 57.65 | 10.7 | 1 | 5.5 | 1 |
| 57.6 | 10.6 | 2 | 5.2 | 2 |
| 57.5 | 10.65 | 2 | 5.1 | 2 |

## Anchorage loitering

- 11357 stretches under 1.0 kn for over 12.0 h, excluding hulls reporting Moored.
- Table: `data/parquet/detect_loitering`.

| lat | lon | events | hours | transmitters |
|---:|---:|---:|---:|---:|
| 57.65 | 10.65 | 704 | 61070.0 | 419 |
| 57.65 | 10.6 | 753 | 59388.6 | 374 |
| 57.55 | 11.65 | 1275 | 54632.8 | 562 |
| 57.65 | 10.7 | 407 | 31240.4 | 257 |
| 57.6 | 10.6 | 266 | 26485.2 | 191 |
| 57.6 | 10.65 | 207 | 17270.5 | 160 |
| 57.5 | 11.65 | 286 | 16087.4 | 175 |
| 57.65 | 10.55 | 304 | 15172.5 | 93 |
| 57.55 | 11.55 | 282 | 14656.4 | 203 |
| 54.2 | 11.9 | 195 | 14540.5 | 125 |

## Draught inconsistency

- 62923 declared-draught changes of at least 1.0 m over 5385 transmitters.
- 50633 with neither a berth call nor an STS candidate in between (the lightering signal); 8 coincide with an STS candidate; 12285 follow a Moored report.

## Spoof-jump excess

- 273629 transmitter-days over 6527 transmitters; 16527 had at least one jump and 16519 exceeded their cells' baseline.
- Mean excess 0.036473, max 1.0. A hull that jumps only as much as everything else in its cell that day scores zero, which is the point.

## MMSI-IMO churn

- 2 IMO changes under a stable MMSI. An IMO counts once it has been broadcast on 5 distinct days under that MMSI. MMSI changes under a hull are counted at each cutoff by the feature store (ADR-23), not here.

## Assumptions to confirm

- "Outside port polygons" is implemented as "not reporting Moored". DMA carries the vessel's own nav_status, so this needs no polygon set; hulls *at anchor* are kept on purpose, because the Skagen transfers happen at anchor. Phase 4a's GFW port visits will add a second predicate.
- A candidate needs 2 h elapsed and qualifying minutes covering at least half of it, so a sparse pair does not qualify on two distant minutes.
- Runs tolerate a 10 min gap (STS) and 30 min (loitering) so one missed minute does not split an event in two.
- Records from a hull's resolver warm-up are dropped rather than given an MMSI-based id: a synthetic id would split the hull's history at the boundary and hide its first transition.
- STS rows do not store each hull's draught 48 h either side, as the prompt suggested; `detect_draught` already joins the two, so storing it twice would be duplicate state.
- Hand-check of 10 candidates and the Skagen monthly plot are still outstanding; both need the real tables.
