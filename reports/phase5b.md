# Phase 5b report (generated 2026-10-02 by `make backtest`)

20 cutoffs scored. Macro-averaged over cutoffs, per PREREG section 3: a cutoff with more hulls must not dominate.

## The pre-registered primary endpoint

| model | precision@50 | PR-AUC | recall@50 | cutoffs | of those, with a positive |
|---|---:|---:|---:|---:|---:|
| `ISO_forest` | **0.245** | 0.2163 | 0.2254 | 20 | 20 |
| `B2_weighted` | **0.207** | 0.142 | 0.177 | 20 | 20 |
| `LGBM` | **0.1815** | 0.1798 | 0.2884 | 13 | 13 |
| `B3_logistic` | **0.1662** | 0.1465 | 0.2485 | 13 | 13 |
| `B1_russia_port` | **0.147** | 0.1181 | 0.0823 | 20 | 20 |
| `B0_random` | **0.124** | 0.1328 | 0.0832 | 20 | 20 |

Same endpoint on the 13 cutoffs where every model was scored. The supervised models skip the earliest cutoffs, which have the highest base rates, so the table above compares them on harder cutoffs than the baselines. Added Oct 2 2026, after results were seen (post-hoc presentation, same numbers).

| model | precision@50 | PR-AUC | recall@50 | cutoffs |
|---|---:|---:|---:|---:|
| `ISO_forest` | **0.1862** | 0.1739 | 0.2745 | 13 |
| `LGBM` | **0.1815** | 0.1798 | 0.2884 | 13 |
| `B3_logistic` | **0.1662** | 0.1465 | 0.2485 | 13 |
| `B2_weighted` | **0.16** | 0.094 | 0.2123 | 13 |
| `B1_russia_port` | **0.0985** | 0.0712 | 0.0784 | 13 |
| `B0_random` | **0.0692** | 0.085 | 0.0814 | 13 |

PREREG section 3 fixes this table as the headline: precision@50 in the B1 stratum, union label. LightGBM is the pre-registered primary model; if a baseline matches or beats it, that is the finding, not a failure to report.

## Every arm

| label set | stratum | model | precision@50 | PR-AUC | recall@50 | FPR@50 |
|---|---|---|---:|---:|---:|---:|
| ofac_only | all | `B0_random` | 0.01 | 0.0119 | 0.0241 | 0.0188 |
| ofac_only | all | `B1_russia_port` | 0.036 | 0.0338 | 0.0689 | 0.0183 |
| ofac_only | all | `B2_weighted` | 0.041 | 0.0389 | 0.1048 | 0.0182 |
| ofac_only | all | `B3_logistic` | 0.0215 | 0.0251 | 0.1249 | 0.0183 |
| ofac_only | all | `ISO_forest` | 0.029 | 0.0247 | 0.0447 | 0.0184 |
| ofac_only | all | `LGBM` | 0.0369 | 0.0413 | 0.2124 | 0.018 |
| ofac_only | b1 | `B0_random` | 0.025 | 0.0462 | 0.0544 | 0.0884 |
| ofac_only | b1 | `B1_russia_port` | 0.036 | 0.0346 | 0.0708 | 0.0874 |
| ofac_only | b1 | `B2_weighted` | 0.041 | 0.0403 | 0.1084 | 0.087 |
| ofac_only | b1 | `B3_logistic` | 0.0246 | 0.0326 | 0.1379 | 0.0916 |
| ofac_only | b1 | `ISO_forest` | 0.043 | 0.0604 | 0.0737 | 0.0869 |
| ofac_only | b1 | `LGBM` | 0.0415 | 0.0454 | 0.2319 | 0.0901 |
| ofac_only | first_seen | `B0_random` | 0.0 | 0.0143 | 0.0 | 0.2367 |
| ofac_only | first_seen | `B1_russia_port` | 0.02 | 0.0624 | 0.8664 | 0.2333 |
| ofac_only | first_seen | `B2_weighted` | 0.021 | 0.0854 | 0.7281 | 0.2335 |
| ofac_only | first_seen | `B3_logistic` | 0.0 | n/a (no positives) | n/a (no positives) | 0.2616 |
| ofac_only | first_seen | `ISO_forest` | 0.014 | 0.0738 | 0.6784 | 0.2342 |
| ofac_only | first_seen | `LGBM` | 0.0 | n/a (no positives) | n/a (no positives) | 0.2616 |
| union | all | `B0_random` | 0.045 | 0.0338 | 0.0261 | 0.0185 |
| union | all | `B1_russia_port` | 0.147 | 0.1134 | 0.077 | 0.0164 |
| union | all | `B2_weighted` | 0.207 | 0.1357 | 0.1686 | 0.0153 |
| union | all | `B3_logistic` | 0.1523 | 0.1286 | 0.2119 | 0.0161 |
| union | all | `ISO_forest` | 0.162 | 0.0906 | 0.1223 | 0.0161 |
| union | all | `LGBM` | 0.1815 | 0.1718 | 0.2742 | 0.0155 |
| union | b1 | `B0_random` | 0.124 | 0.1328 | 0.0832 | 0.087 |
| union | b1 | `B1_russia_port` | 0.147 | 0.1181 | 0.0823 | 0.0843 |
| union | b1 | `B2_weighted` | 0.207 | 0.142 | 0.177 | 0.0784 |
| union | b1 | `B3_logistic` | 0.1662 | 0.1465 | 0.2485 | 0.0829 |
| union | b1 | `ISO_forest` | 0.245 | 0.2163 | 0.2254 | 0.0746 |
| union | b1 | `LGBM` | 0.1815 | 0.1798 | 0.2884 | 0.0813 |
| union | first_seen | `B0_random` | 0.009 | 0.026 | 0.2084 | 0.2367 |
| union | first_seen | `B1_russia_port` | 0.049 | 0.082 | 0.9231 | 0.2285 |
| union | first_seen | `B2_weighted` | 0.051 | 0.0999 | 0.8271 | 0.2294 |
| union | first_seen | `B3_logistic` | 0.0108 | 0.1246 | 0.8571 | 0.2596 |
| union | first_seen | `ISO_forest` | 0.028 | 0.0805 | 0.437 | 0.2327 |
| union | first_seen | `LGBM` | 0.0123 | 0.22 | 1.0 | 0.2593 |

- Per-cutoff rows: `reports/metrics_by_cutoff.csv`.

## Lead time (event study)

Weeks between a hull's designation and the earliest cutoff at which it entered the top 50. Event-study rather than per-cutoff: averaging per-cutoff distances mostly measures how far each cutoff sat from the next designation wave (ADR-11).

| model | flagged before designation | of designated | median weeks | max |
|---|---:|---:|---:|---:|
| `B0_random` | 62 | 1189 | 20.3 | 92.0 |
| `B1_russia_port` | 43 | 1189 | 27.1 | 100.7 |
| `B2_weighted` | 82 | 1189 | 20.1 | 71.6 |
| `B3_logistic` | 50 | 1189 | 6.4 | 68.6 |
| `ISO_forest` | 51 | 1189 | 18.9 | 51.3 |
| `LGBM` | 65 | 1189 | 10.9 | 55.6 |

Right-censored by construction: a hull flagged at the first cutoff cannot show a longer lead than the window allows, and one designated after the last horizon does not appear at all.

## Leakage suite

**(c), (d) and (e) pass, (d) only under its post-results amendment: the pre-registered raw rule fails, see the row below.**

(a) truncation equality and (b) static analysis run in `tests/test_leakage.py`, which `make backtest` executes before it reports anything.

| test | result |
|---|---|
| (c) permutation | pass (ratio 0.575, pr_auc_shuffled 0.0351) |
| (d) reverse time | pass on lift; **the pre-registered raw-PR-AUC rule fails** (amended 2026-10-02 after results were seen, PREREG section 12) (ratio 2.522, pr_auc_forward 0.0437, pr_auc_backward 0.1102, base_rate_early 0.0468, base_rate_late 0.0065, lift_forward 6.699, lift_backward 2.358, lift_ratio 0.352) |
| (e) entity-resolution sensitivity | pass (pr_auc_delta 0.0) |

## What is not contributing yet

- B2 term firing counts at the last cutoff: {'b1': 518, 'long_gap': 68, 'sanctioned_partner': 0, 'convenience_flag': 10, 'old_vessel': 0, 'renamed': 156, 'spoof_excess': 990}.
- **Dead B2 terms: `sanctioned_partner`, `old_vessel`.** Their weights are pre-registered and stay as they are; they contribute nothing until the phase that feeds them has run (GFW terms need Phase 4a, `old_vessel` needs the registry build year). Reported rather than silently reweighted.

## Assumptions to confirm

- A `syn:` hull has no IMO, so it can never be a positive. The per-cutoff rows carry `hulls_without_imo` so that recall ceiling is visible.
- `current_flag` is dropped from the B3 design matrix rather than label-encoded: an arbitrary integer ordering of flags is a worse lie than leaving the column out.
- B0 is seeded, so the random baseline is reproducible.
- (a) and (b) of the leakage suite run in `tests/test_leakage.py`, which `make backtest` executes before anything here; (c), (d) and (e) run in the harness and are above.
