# Shadow Fleet

Evasion-indicator scoring for tankers from cooperative data (terrestrial AIS and open watchlists), with a point-in-time backtest. The question it measures: how many tankers later listed by OFAC, the EU or the UK would this system have ranked highly *before* they were listed, and how many weeks early?

It is not dark-fleet detection. Every input is cooperative, and the evaluation is pre-registered in `PREREG.md`, committed before any label existed. Every number below is generated from `reports/` by `make readme`; nothing here is typed by hand.

*Generated 2026-10-05.*

## Headline result

On the pre-registered endpoint (precision@50 within the Russia-port stratum, label = OFAC ∪ EU ∪ UK, macro-averaged over 13 monthly cutoffs), the pre-registered primary model, **`LGBM`, reaches precision@50 0.1815** (PR-AUC 0.1798, recall@50 0.2884): of the 50 tankers it ranks highest each month, that share is designated by the US, EU or UK within the next 182 days.

Every model on the same cutoffs (the supervised models cannot score the earliest ones, which have the highest base rates, so all-cutoff averages are not comparable):

| model | precision@50 | lift over random | PR-AUC | cutoffs |
|---|---:|---:|---:|---:|
| `ISO_forest` | 0.1862 | 2.7x | 0.1739 | 13 |
| `LGBM` | 0.1815 | 2.6x | 0.1798 | 13 |
| `B3_logistic` | 0.1662 | 2.4x | 0.1465 | 13 |
| `B2_weighted` | 0.16 | 2.3x | 0.094 | 13 |
| `B1_russia_port` | 0.0985 | 1.4x | 0.0712 | 13 |
| `B0_random` | 0.0692 | 1.0x | 0.085 | 13 |

The signal is modest and simple: every learned model lands close to the others, and the per-family ablations in `reports/phase6.md` show which data carries it.

Median lead time for hulls it flagged before designation: **10.9 weeks** (65 of 1189 designated hulls flagged in time).

Full tables: `reports/phase5b.md`, `reports/phase6.md`, `reports/metrics_by_cutoff.csv`.

## Forward test

A backtest can be tuned without meaning to. So the primary model's top 50 is committed to this repo, hash-stamped, before any of its outcomes exist, and scored against real designations later (`make forward-eval`). The manifest, `reports/forward/README.md`, is append-only and says which list is primary and from which date hits count.

| scored at | model | population | sha256 |
|---|---|---:|---|
| 2026-10-01 | LGBM | 2916 | `f64527a59ad4...` |
| 2026-10-02 | LGBM | 2909 | `2e1ff25872e7...` |

## Changes made after results were seen

Every one is recorded in `PREREG.md` section 12 and labelled post-hoc in the reports, with the numbers from before the change kept beside the numbers after it. Most were bug fixes that brought the code in line with what was pre-registered. The forward test is the check none of them can influence.

## What was built, and what it measured

| stage | measured | source |
|---|---|---|
| Window | **not measured yet** (`make window-gate`) months, **not measured yet** (`make window-gate`) evaluable cutoffs | `config/window.json` |
| Labels | 5086 dated sanctions actions | `reports/phase2.md` |
| Identity | 0.8059 of MMSI-days resolved to an IMO-based hull id | `reports/phase3.md` |
| Self-built detectors | 25 STS candidates, 11357 loitering events | `reports/phase4b.md` |
| Feature store | 32 features in 7 frozen families | `reports/phase5a.md` |
| Briefs | **not measured yet** (`make briefs`) generated, **not measured yet** (`make briefs`) with zero verifier failures | `reports/phase8.md` |
| Judge | entailment **not measured yet** (`make judge`), kappa vs human **not measured yet** (`fill reports/audit_sheet.csv`) | `reports/phase9.md` |

## How the point-in-time claim is enforced

`features(hull_id, T)` may only read records with `observed_at <= T`. Five tests hold that claim up, and `make backtest` runs them before it reports anything:

1. Features at T from the live store equal features at T from a store whose later partitions are physically deleted and whose derived tables are rebuilt from what is left.
2. Static analysis: nothing under `features/` imports label construction, no feature name mentions sanctions except the partner feature, no GFW registry ownership field is read.
3. Permutation: shuffling the training labels drops PR-AUC to the base rate.
4. Reverse time: training on later cutoffs and scoring earlier ones is not dramatically better than forward, measured as lift over each cutoff's base rate. **Disclosed:** the pre-registered version compared raw PR-AUC, which failed because the earliest cutoff has several times the late one's base rate; the comparison was amended to lift on 2026-10-02, after results had been seen (`PREREG.md` section 12).
5. Entity-resolution sensitivity: no hull id depends on a GFW merge, so the delta is zero by construction and the check fails if that ever stops being true.

## Limitations, stated up front

- Most Baltic tankers later designated were calling at Russian ports, so a one-line rule has large lift. The headline is therefore lift **within** that stratum, not against the whole population (R3).
- Hundreds of tankers were EU or UK-listed before OFAC designated them, so an OFAC-only label would be trivially predictable from the other lists. The population excludes anything already listed by any of the three, and OFAC-only is a sensitivity table (R12).
- Many designations follow ownership or price-cap reasons rather than observable behaviour, so recall has a ceiling behaviour-based scoring cannot pass (R9).
- Ownership through shell companies is invisible in free data. The project quantifies how far behaviour alone gets you and says where paid registries would change the answer (R8).
- Feature design was informed by public reporting through 2026, so it is not blind to the test period. `PREREG.md` limits what could be tuned after results were seen; it cannot make the design blind.
- Lead time is bounded by the 182-day horizon and right-censored at the last cutoff.

## Licences and what is published

Global Fishing Watch and OpenSanctions data are non-commercial only (OpenSanctions: CC BY-NC 4.0). This repo publishes code, aggregate tables and figures. It never contains GFW-derived tables, OpenSanctions bulk files or raw AIS; `data/` and `reports/probes/` are gitignored. Nothing is scraped from Equasis or any site whose terms forbid it.

## Run it

```bash
make setup && make doctor && make test
make all
```

`SETUP.md` covers WSL2, uv and llama.cpp. `shadow-fleet-plan.md` has the architecture, the ranked risks and every ADR. `phase-prompts.md` is the build plan.

## Analyst console

`ui/` is a local React console (ADR-18): a ranked watchlist per cutoff, a map of each hull's DMA track and its events, and a timeline that shows each hull only as it was knowable at the chosen instant. It renders a JSON bundle the Python side writes and never computes a metric itself. Until `make ui-export` is wired to the real outputs (Phase C) it runs on a synthetic bundle, labelled as such. Local only: its dossiers carry GFW-derived events (rule 7). See `SETUP.md` section 9.

## Documents

- `shadow-fleet-plan.md`: architecture, risks, ADRs, evaluation design.
- `CLAUDE.md`: rules for every build session.
- `phase-prompts.md`, `phase1-prompt.md`: one prompt per phase.
- `PREREG.md`: the pre-registered evaluation, committed before any label existed.
