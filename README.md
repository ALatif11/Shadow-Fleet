# Shadow Fleet

Evasion-indicator scoring for tankers from cooperative data (terrestrial AIS and open watchlists), with a point-in-time backtest. The question it measures: how many tankers later listed by OFAC, the EU or the UK would this system have ranked highly *before* they were listed, and how many weeks early?

It is not dark-fleet detection. Every input is cooperative, and the evaluation is pre-registered in `PREREG.md`, committed before any label existed. Every number below is generated from `reports/` by `make readme`; nothing here is typed by hand.

*Generated 2026-09-27.*

## Headline result

**not measured yet** (`make backtest`). The harness runs; no cutoff has produced a scored model yet.

## What was built, and what it measured

| stage | measured | source |
|---|---|---|
| Window | **not measured yet** (`make window-gate`) months, **not measured yet** (`make window-gate`) evaluable cutoffs | `config/window.json` |
| Labels | **not measured yet** (`make labels`) dated sanctions actions | `reports/phase2.md` |
| Identity | **not measured yet** (`make identity`) of MMSI-days resolved to an IMO-based hull id | `reports/phase3.md` |
| Self-built detectors | **not measured yet** (`make detect`) STS candidates, **not measured yet** (`make detect`) loitering events | `reports/phase4b.md` |
| Feature store | **not measured yet** (`make features`) features in **not measured yet** (`make features`) frozen families | `reports/phase5a.md` |
| Briefs | **not measured yet** (`make briefs`) generated, **not measured yet** (`make briefs`) with zero verifier failures | `reports/phase8.md` |
| Judge | entailment **not measured yet** (`make judge`), kappa vs human **not measured yet** (`fill reports/audit_sheet.csv`) | `reports/phase9.md` |

## How the point-in-time claim is enforced

`features(hull_id, T)` may only read records with `observed_at <= T`. Five tests hold that claim up, and `make backtest` runs them before it reports anything:

1. Features at T from the live store equal features at T from a store whose later partitions are physically deleted and whose derived tables are rebuilt from what is left.
2. Static analysis: nothing under `features/` imports label construction, no feature name mentions sanctions except the partner feature, no GFW registry ownership field is read.
3. Permutation: shuffling the training labels drops PR-AUC to the base rate.
4. Reverse time: training on later cutoffs and scoring earlier ones is not dramatically better than forward.
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
