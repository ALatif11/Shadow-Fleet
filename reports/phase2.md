# Phase 2 report (generated 2026-09-18 by `make labels`)

`PREREG.md` was committed before this table existed; see the git history.

## Sources

| source | rows | distinct IMOs | note |
|---|---:|---:|---|
| OFAC advanced XML | 1862 | 1525 | dated EntryEvents for currently listed vessels; IMO via {'advanced_xml:sdn_csv': 1862} |
| OFAC change archive | 1832 | 1247 | removals, modifications, delisted hulls |
| EU (eu_sanctions_map, EU-MARE) | 729 | 673 | undated: 0 {} |
| UK (gb_fcdo_sanctions) | 663 | 663 | undated: 0 |

- `sanctions_actions.parquet`: 5086 rows. By source and action: [['EU', 'add', 729, 673, '2024-06-25', '2026-07-24'], ['OFAC', 'add', 3062, 1564, '1989-01-05', '2026-08-24'], ['OFAC', 'modify', 543, 337, '2022-04-29', '2026-07-27'], ['OFAC', 'remove', 89, 60, '2022-01-31', '2026-06-24'], ['UK', 'add', 663, 663, '2017-10-03', '2026-08-06']]
- CELEX dates loaded from config: 1
- XML vs archive add dates: 1079 IMOs in both, 1079 agree within 7 days. Worst: [{'imo': 9940629, 'days_apart': 0, 'xml': '2022-12-09', 'archive': '2022-12-09'}, {'imo': 9942392, 'days_apart': 0, 'xml': '2023-04-12', 'archive': '2023-04-12'}, {'imo': 9953509, 'days_apart': 0, 'xml': '2024-08-23', 'archive': '2024-08-23'}, {'imo': 9953523, 'days_apart': 0, 'xml': '2024-08-23', 'archive': '2024-08-23'}, {'imo': 9969821, 'days_apart': 0, 'xml': '2025-04-22', 'archive': '2025-04-22'}]

## Positives per cutoff

| cutoff | observed | in scope | excluded (already listed) | positives (union) | positives (OFAC only) | OFAC adds in horizon | of those, EU/UK-listed at T |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2024-08-31 | 2626 | 2600 | 26 | 136 | 70 | 77 | 7 |
| 2024-09-30 | 2711 | 2680 | 31 | 129 | 66 | 76 | 10 |
| 2024-10-31 | 2716 | 2671 | 45 | 114 | 63 | 76 | 13 |
| 2024-11-30 | 2661 | 2604 | 57 | 192 | 64 | 81 | 17 |
| 2024-12-31 | 2681 | 2599 | 82 | 164 | 48 | 78 | 30 |
| 2025-01-31 | 2641 | 2517 | 124 | 147 | 17 | 17 | 0 |
| 2025-02-28 | 2651 | 2497 | 154 | 123 | 17 | 18 | 1 |
| 2025-03-31 | 2673 | 2522 | 151 | 128 | 18 | 19 | 1 |
| 2025-04-30 | 2748 | 2604 | 144 | 151 | 16 | 17 | 1 |
| 2025-05-31 | 2840 | 2634 | 206 | 67 | 16 | 19 | 3 |
| 2025-06-30 | 2890 | 2694 | 196 | 58 | 19 | 22 | 3 |
| 2025-07-31 | 2898 | 2677 | 221 | 28 | 2 | 3 | 1 |
| 2025-08-31 | 2913 | 2714 | 199 | 30 | 3 | 5 | 2 |
| 2025-09-30 | 2888 | 2688 | 200 | 28 | 3 | 4 | 1 |
| 2025-10-31 | 2894 | 2677 | 217 | 19 | 9 | 11 | 2 |
| 2025-11-30 | 2818 | 2608 | 210 | 18 | 8 | 9 | 1 |
| 2025-12-31 | 2850 | 2619 | 231 | 17 | 7 | 8 | 1 |
| 2026-01-31 | 2809 | 2569 | 240 | 20 | 8 | 9 | 1 |
| 2026-02-28 | 2771 | 2508 | 263 | 18 | 7 | 7 | 0 |

- Full table: `/home/adam1/shadowfleet/reports/phase2_positives.csv`

## Findings

- The union label (OFAC/EU/UK) carries the evaluation. Late-2024 cutoffs are the richest, because the January 2025 OFAC action falls inside their 182-day horizon.
- R12 is real and material: a large share of OFAC designations were already EU or UK-listed at T, which is why the pre-registered headline is the union and OFAC-only is a sensitivity table.
- Population keyed on the modal AIS IMO until Phase 3 lands `hull_id`; hulls without a valid AIS IMO are not yet in the population and Phase 3 measures how many that is.

## Assumptions to confirm

- `EntryEventTypeID = 1` is the original entry in the SDN advanced XML; other ids are recorded as `modify` rather than interpreted.
- EU dates are entry-into-force dates; the CELEX table in `config/celex_dates.json` cites each.
- Ten positives still need a hand spot-check against the official press releases.
