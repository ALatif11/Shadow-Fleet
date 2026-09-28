# Phase 1 report (generated 2026-09-18 by `make phase1`)

Window 2024-03-01 to 2026-09-14.

## Bulk ingest (task 4)

- Days done 928 of 928; failed 0.
- Failed days (first 20): none

## Population

- MMSIs seen: 83722; ever tanker-class: 6605; with an IMO in AIS: 20750.
- Monthly counts: `reports/phase1_population_by_month.csv` (31 months).

## Reported type changes

- Changes: 466227 across 6509 MMSIs; 4861 stopped reporting as tanker at least once (these must not silently drop out of the study; Phase 3 cross-checks them).

## DMA gaps are coverage, not evasion (R4)

- Gaps over 6 h: 116010; median 57.43069444444444 h, p90 917.2355833333336 h.
- Of 500 sampled gaps, 235 start within 10 km of the coverage edge (47%), 317 within 25 km; median distance to the edge 12.912435086215307 km.
- Median displacement across the gap 51.2162797987631 km; 253 of the sample reappeared over 50 km away.
- **DMA gaps are coverage artefacts, not evasion features: they start at the coverage edge or the hull reappears far away, i.e. it left the footprint**
- Figure: `reports/phase1_gaps.png`

## STS readiness (Phase 4b input)

- Day 2026-09-03, pairs within 500 m under 2.0 kn for 2.0 h in the Skagen box: 0 at full resolution, 0 after downsampling.
- Diagnostics: {'slow_minutes': 4400, 'slow_mmsi': 16, 'rows_in_box': 42046, 'mmsi_in_box': 54, 'closest_pair_m': 71.78299540423338}
- Downsample loses pairs: **False**

## Assumptions to confirm

- The coverage edge is derived from the data (0.1-degree cells with fewer than 8 occupied neighbours), not from an official Danish polygon.
- STS readiness counts qualifying minute buckets, not contiguous runs; Phase 4b does run-length detection.
- The Skagen box in config is still a placeholder.
