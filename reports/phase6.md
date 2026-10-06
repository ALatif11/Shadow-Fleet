# Phase 6 report (generated 2026-10-06 by `make phase6`)

LightGBM and the isolation forest join the harness here; everything else in this report is reporting only and never drives a change to the feature set (PREREG section 7).

## Does the model beat the rules

Compared on the cutoffs where every model was scored (the supervised models skip the earliest, highest-base-rate cutoffs, so an all-cutoff average flatters the baselines; this matched view was added Oct 2 2026, after results were seen).

- **LightGBM beats the best rules baseline** (`B3_logistic`) on the pre-registered endpoint: precision@50 0.1846 vs 0.1662, a difference of +0.0184, macro-averaged over 13 cutoffs in the B1 stratum under the union label.
- PR-AUC: 0.1783 vs 0.1465.
- If that difference is small, the finding is that a hand-weighted rule captures most of what is learnable from these features, which is a result about the data, not a failure of the model (CLAUDE.md rule 8).
- The isolation forest, which never sees a label, reaches precision@50 0.1862 on the same cutoffs.

- Trees kept by early stopping, per cutoff and seed: min 5, median 278, max 400 (a 1 is a constant model).

## Ablations

Each arm retrains LightGBM on a restricted column set. Means over the 13 cutoffs with a closed training horizon.

| arm | PR-AUC | precision@50 |
|---|---:|---:|
| `drop_ais` | 0.1619 | 0.1831 |
| `drop_detect` | 0.1831 | 0.18 |
| `drop_gfw_encounters` | 0.1708 | 0.1831 |
| `drop_gfw_gaps` | 0.1701 | 0.1908 |
| `drop_gfw_ports` | 0.1295 | 0.1615 |
| `drop_identity` | 0.17 | 0.1954 |
| `drop_static` | 0.1551 | 0.1738 |
| `full` | 0.1708 | 0.1831 |
| `gfw_only` | 0.1559 | 0.1692 |
| `self_built_only` | 0.1235 | 0.1723 |

`gfw_only` against `self_built_only` is the arm this project exists to report (R14): it separates what Global Fishing Watch detected from what this project detected.

## Drift (PSI by family), split at the pre-registered Hormuz break

| side | cutoffs | PSI by family |
|---|---:|---|
| pre_break | 11 | `ais` 0.0148, `detect` 0.0079, `gfw_ports` 0.0289, `identity` 0.7982, `static` 0.0107 |
| post_break | 2 | `ais` 0.0162, `detect` 0.0036, `gfw_ports` 0.0236, `identity` 0.623, `static` 0.009 |

Under 0.1 is stable, 0.1 to 0.25 moderate, over 0.25 large. The break is 2026-02-28 and is never a feature and never a window bound (ADR-17).

## Per-hull explanations

- SHAP tables: `data/parquet/shap/cutoff=*/` for 13 cutoffs. Contributions come from LightGBM's own `pred_contrib` (TreeSHAP), so the `shap` package is not a dependency: it would be a second implementation of the same algorithm.
- Ranked lists: `reports/flagged_<cutoff>.csv`, top 100 per cutoff, with the five largest drivers per hull.
- False-positive review sheets for Adam: ['reports/fp_review_2025Q1.csv', 'reports/fp_review_2025Q2.csv', 'reports/fp_review_2025Q3.csv', 'reports/fp_review_2025Q4.csv', 'reports/fp_review_2026Q1.csv']. The `reason` column is deliberately blank; a pre-filled guess would be a fabricated review.

## Assumptions to confirm

- LightGBM's early-stopping fold is the most recent training cutoff, as PREREG section 4 asks (until Oct 2 2026 it was a single row, so early stopping never ran; PREREG section 12).
- The isolation forest is fitted on each cutoff's own feature matrix, so it needs no history and is available at cutoffs where the supervised models are not.
- Calibration is reported as the Brier score inside the per-cutoff metrics for the probability models. There is no reliability-diagram figure yet.
- The top-20 false-positive review per quarter is generated but not yet filled in.
