# PREREG: Shadow Fleet evaluation, pre-registered

Committed 2026-09-17, before any label table, positives count or model score exists in this repo. The git
history is the evidence: if a commit containing a positives count or a metric precedes this file's first
commit, this pre-registration is void and every report must say so.

Nothing here may be changed once Phase 5b results are seen. Anything added or altered after that point is
labelled **post-hoc** in every report and in the README, next to the pre-registered result, not instead of it.

## 1. Data window and cutoff rule

- Window: `config/window.json`, written by `make window-gate` in Phase 0. At writing: **2024-03-01 to
  2026-09-14** (first daily DMA file to the latest available), 30.5 months.
- Cutoff rule: **the last calendar day of every month T** with `window_start + 180 d <= T <= min(window_end,
  today - 182 d)`. Cutoffs are computed by this rule, never chosen. At writing that is 19 cutoffs,
  2024-08-31 to 2026-02-28, of which 12 are supervised (2025-03-31 onward). The set grows as the horizon
  closes on later months; growth by rule is not an amendment.
- Feature window: **180 days** ending at T. Horizon: **182 days** after T.
- Quarterly aggregates are derived from monthly cutoffs. Scoring is always monthly.

## 2. Population at T

Hulls with at least one DMA observation in `[T-180d, T]` **and not listed** by any of OFAC, the EU or the UK
on or before T (unrevoked add). Being on another Western list is baseline B1b, reported, never a feature.

## 3. Primary endpoint (one)

**Precision@50 within the B1 stratum** (hulls the Russia-port rule flags), label = **OFAC ∪ EU ∪ UK**,
macro-averaged over all scored cutoffs, primary model **LightGBM**.

One primary endpoint, fixed now, so the headline cannot be chosen after the fact.

## 4. Primary model, fixed before labels

LightGBM, `num_leaves 15`, `min_child_samples 20`, `feature_fraction 0.8`, `scale_pos_weight` from the
training base rate, early stopping on the most recent training cutoff held out as validation, **three seeds
averaged**, no hyperparameter search beyond those seeds. Training uses only cutoffs whose horizon closed on
or before T (expanding window).

## 5. Secondary endpoints (reported, never promoted to headline)

Precision@k and recall@k for k in {25, 50, 100}; PR-AUC; alert volume needed for 50 percent recall; FPR at
the k=50 operating point; event-study lead time in weeks; calibration (reliability plot, Brier score);
first-appearance evaluation (each hull scored only at its first eligible cutoff); the same metrics on the
full population rather than the B1 stratum; OFAC-only as a label sensitivity table.

## 6. Baselines and their weights, fixed now

- **B0** random.
- **B1** called at a Russian Baltic or Black Sea port in window (GFW port visit or DMA destination text).
- **B1b** listed by a Western authority other than the one defining the label (OFAC-only table; exposes the
  trivial channel, R12).
- **B2** weighted sum, weights fixed here before any label is loaded:
  `3·B1 + 2·[gap_hours_total > 24] + 2·[n_encounters_with_sanctioned_partner > 0] +
  1·[flag_to_convenience_registry] + 1·[vessel_age_years > 15] + 1·[n_name_changes >= 1] +
  1·[spoof_jump_rate_excess > 0]`.
- **B3** logistic regression on the full feature set, standardised, `class_weight="balanced"`.
- Unsupervised isolation forest reported alongside, not as a baseline to beat.

The Phase 0 GFW probe found zero encounter events for five designated tankers, so the encounter term in B2
may contribute nothing. It stays in at its pre-registered weight and the result is reported as a finding.

## 7. Frozen feature families

`identity`, `ais` (DMA transits, draught, destination, spoof-jump excess), `gfw_gaps`, `gfw_encounters`,
`gfw_ports`, `detect` (self-built STS, anchorage loitering, draught inconsistency, MMSI-IMO churn),
`static` (age, length, dwt). The exact feature list is frozen in the `FEATURES` registry in Phase 5a and a
test asserts the registry equals this list.

**Feature families are never removed after results are seen.** Ablations, including GFW-only and
self-built-only arms, are reporting only and never drive removal.

## 8. Regime break and drift

`hormuz_closure = 2026-02-28` (Strait of Hormuz effectively closed after the Feb 28 2026 strikes). Metrics
and PSI drift are reported for cutoffs before and on-or-after that date, with the count of post-break
cutoffs whose horizon has closed. The break is never a window bound and never a feature.

## 9. Forward test (Phase F)

On or after **2026-10-01**, score the then-current population with the frozen primary model and commit the
ranked top 50 (hull id, IMO, score, top-5 drivers; no GFW-derived values) to git before any evaluation. The
commit timestamp is the evidence. Evaluate monthly against OFAC, EU and UK designations through T+182 days.
Reported as prospective, separately from the backtest.

## 10. Leakage tests that must pass before any metric is reported

1. `features(h, T)` from the live store equals `features(h, T)` from a store truncated at T, for 200 sampled
   hulls at each cutoff.
2. No feature reads an OpenSanctions or OFAC column except `listed_as_of_T` computed from dated actions, and
   no feature reads a GFW registry ownership field.
3. Permutation: shuffled labels drop PR-AUC to the base rate.
4. Time-direction: training on later cutoffs and scoring earlier ones is not dramatically better than
   forward.
5. Entity-resolution sensitivity: primary metrics recomputed with GFW-based hull merges disabled; the delta
   is reported.

`make backtest` runs these first and fails without reporting metrics if any fails.

## 11. Disclosures that limit what this pre-registration can claim

- Feature design was informed by public reporting through 2026, so it is **not blind** to the test period.
  This document limits what can be tuned after results are seen; it cannot make the design blind.
- GFW event and identity models postdate the cutoffs. The identity-merge share is quantified (test 5); the
  event models cannot be re-run as of T.
- Lead time is bounded above by the 182-day horizon and right-censored at the last cutoff.
- Many designations follow ownership or price-cap reasons rather than observable behaviour, so recall has a
  ceiling that behaviour-based scoring cannot pass. Reported, not tuned around.

## 12. Amendments

Append-only, below, each with a date and whether Phase 5b results had been seen.

- (none)
