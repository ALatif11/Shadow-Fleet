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

- **2026-09-28, before any Phase 5b result existed (no metric had been computed on real data).** Section 5,
  B1: "a Russian Baltic or Black Sea port" becomes **"a Russian Baltic or Black Sea port, or Murmansk"**
  (decided by Adam). The code's port list had also carried Kozmino, a Pacific terminal that serves Asia rather
  than the fleet that transits Danish waters; it is removed. Kaliningrad and Baltiysk are added, since they
  are Russian Baltic ports and were missing. For GFW port visits the rule is applied by the anchorage's
  country (RUS) and position rather than its name, because two of the eight port visits in the Phase 0
  sample carried no anchorage name at all and a name list would have missed them silently.
- **2026-09-29, before any Phase 5b result existed.** Section 5, B1, DMA half: no change to which ports count,
  only to how a destination is recognised. On the real store, 1,215 of the top 1,462 Russian-looking
  ship-destination pairs were written as UN/LOCODEs (`RUULU`, `RU ULU`, `RUULU>EGPSD`) or spelling variants
  (`UST_LUGA`, `ST.PETERSBURG`) that the name list missed. B1 destinations now match the ports' UN/LOCODEs
  (RU + PRI, ULU, VYS, LED, KGD, BLT, NVS, TUA, TAM, MMK) or their names with punctuation and spaces removed.
  Arkhangelsk (`RUARH`), which appears in the data, stays outside B1 under amendment 1.
- **2026-10-02, disclosure, not an amendment.** The first real `make backtest` failed test 4 (reverse time:
  backward PR-AUC 0.110 vs forward 0.044, ratio 2.5 against a tolerance of 1.5), and a harness bug printed
  every metric anyway instead of stopping. Adam and the developer have therefore seen Phase 5b results.
  The harness now stops and writes only the leakage section. From this point, any change to a leakage test,
  a feature or a model is post-results and is labelled post-hoc wherever it is reported.
- **2026-10-02, AFTER Phase 5b results had been seen (see the disclosure above). Post-hoc.** Section 10,
  test 4: "not dramatically better" is now measured as lift over each cutoff's own base rate (backward
  lift may not exceed 1.5 x forward lift), not raw PR-AUC. Reason: the two directions are scored on
  different cutoffs; the earliest (2024-08-31, base rate 4.68%, inside the horizon of OFAC's Jan 10 2025
  wave) has 7 times the base rate of the latest (2026-03-31, 0.65%), and PR-AUC rises with base rate. On
  lift, backward is worse than forward (2.36 vs 6.70), which is the opposite of the leakage signature. The
  raw rule is still computed and every report shows that it fails. Decided by Adam.
- **2026-10-02, AFTER results had been seen. Two implementation fixes to the primary model, not design
  changes; both are reported.** (1) Section 4 says early stopping holds out the most recent training cutoff.
  The code held out one row (`max(int(n*0.8), n-1)`), so early stopping never ran and every model trained
  400 rounds. It now holds out the most recent training cutoff, as pre-registered. (2) LightGBM was not
  reproducible: identical data gave B1 precision@50 0.2077 in one run and 0.1954 in the next (multi-threaded
  histogram sums). It now runs single-threaded and deterministic. The Phase 6 numbers from before these
  fixes are kept in `reports/status-2026-09-28.md` beside the numbers after them, so the effect is visible.
- **2026-10-02, AFTER results had been seen. Post-hoc.** LightGBM early stopping now watches average
  precision instead of the default logloss. Logloss rewards calibration, which `scale_pos_weight`
  deliberately breaks, so on a low-base-rate validation cutoff training stopped at the first tree; the
  forward list scored 2026-10-01 came out as 50 identical scores. That list is kept, hash intact, and
  reported; the primary forward test is a second list scored 2026-10-02 (declared in
  `reports/forward/README.md` before it was scored). Phase 6 is re-run with the fix and both runs are kept.
- **2026-10-06, AFTER results had been seen. Reproducibility fix, not a design change; reported.** A Phase 6
  re-run reproduced every model to four decimals except LightGBM (B1 precision@50 0.1815 committed Oct 4,
  0.1908 on Oct 6, same code and data). Cause, measured: DuckDB sums floats across threads in no fixed order,
  so two feature builds of the same cutoff differed in 2,096 of its values at the 14th significant digit, and
  LightGBM, which bins on exact values, trained a different model each time. Feature values are now rounded
  to 6 decimals. The LightGBM numbers reported from here on are the first run after this fix; both earlier
  values are kept in `reports/status-2026-09-28.md`. The forward list scored 2026-10-02 came from a model
  built on unrounded features; it stays as committed (its hash is the record of what was scored), and a
  re-score today would not reproduce it bit for bit.
