# Phase 5a report (generated 2026-10-02 by `make features`)

32 features in 7 frozen families. The registry is the contract: `tests/test_leakage.py` fails if its families stop matching PREREG section 7.

| family | source | features |
|---|---|---|
| `ais` | dma | `mean_transit_speed_kn`, `n_ballast_transits`, `n_days_observed`, `n_laden_transits`, `n_transits`, `share_russian_destination`, `spoof_jump_rate_excess` |
| `detect` | self_built | `anchorage_loitering_hours`, `n_draught_inconsistencies`, `n_mmsi_imo_churn`, `n_sts_candidates`, `n_sts_with_draught_change` |
| `gfw_encounters` | gfw | `n_encounters`, `n_encounters_with_sanctioned_partner` |
| `gfw_gaps` | gfw | `gap_hours_total`, `max_gap_distance_km`, `n_gaps`, `n_gaps_offshore` |
| `gfw_ports` | gfw | `days_since_last_russian_port_visit`, `loitering_hours`, `n_loitering`, `n_port_visits`, `n_russian_port_visits` |
| `identity` | dma | `current_flag`, `days_since_last_identity_change`, `flag_to_convenience_registry`, `n_flag_changes`, `n_mmsi_changes`, `n_name_changes` |
| `static` | dma | `dwt`, `length_m`, `vessel_age_years` |

## Feature matrix

| cutoff | hulls | seconds |
|---|---:|---:|
| 2024-08-31 | 2631 | 7.5 |
| 2024-09-30 | 2718 | 7.0 |
| 2024-10-31 | 2699 | 7.0 |
| 2024-11-30 | 2636 | 7.7 |
| 2024-12-31 | 2637 | 7.0 |
| 2025-01-31 | 2554 | 6.8 |
| 2025-02-28 | 2541 | 7.1 |
| 2025-03-31 | 2553 | 7.1 |
| 2025-04-30 | 2645 | 8.5 |
| 2025-05-31 | 2669 | 8.0 |
| 2025-06-30 | 2718 | 7.4 |
| 2025-07-31 | 2734 | 7.9 |
| 2025-08-31 | 2754 | 7.8 |
| 2025-09-30 | 2762 | 8.2 |
| 2025-10-31 | 2753 | 8.5 |
| 2025-11-30 | 2690 | 8.1 |
| 2025-12-31 | 2704 | 7.4 |
| 2026-01-31 | 2655 | 7.4 |
| 2026-02-28 | 2590 | 6.9 |
| 2026-03-31 | 2606 | 6.9 |

- Table: `data/parquet/feature_matrix`.
- GFW events present: **True**.

## Leakage suite

- (a) `features(h, T)` from the live store equals `features(h, T)` from a store physically truncated at T. Tested.
- (b) static analysis: nothing under `features/` imports label construction, no feature name mentions sanctions except the partner feature, no GFW registry ownership field is read. Tested.
- (c) permutation, (d) reverse-time, (e) entity-resolution sensitivity: these need a fitted model, so they are Phase 5b harness hooks. They are named here so their absence is visible.

## Assumptions to confirm

- A transit is a contiguous run of DMA positions; a gap over 12 h starts a new one. On terrestrial AIS that means one transit per visit to coverage, which is the intent.
- Laden is judged per transit against the hull's own 75th-percentile draught, so hull size does not decide it.
- `dwt` has no source: DMA does not carry it, so the column is null until one exists.
- `vessel_age_years` is null until Phase 4a brings the GFW registry build year.
