# Phase 1 report (generated 2026-09-24 by `make phase1`)

No window file.

## Population

- MMSIs seen: None; ever tanker-class: None; with an IMO in AIS: None.
- Monthly counts: `None` (None months).

## Reported type changes

- Changes: None across None MMSIs; None stopped reporting as tanker at least once (these must not silently drop out of the study; Phase 3 cross-checks them).

## DMA gaps are coverage, not evasion (R4)

- Gaps over 6 h: None; median None h, p90 None h.
- Of None sampled gaps, None start within 10 km of the coverage edge (0%), None within 25 km; median distance to the edge None km.
- Median displacement across the gap None km; None of the sample reappeared over 50 km away.
- **None**
- Figure: `None`

## STS readiness (Phase 4b input)

- Day None, pairs within None m under None kn for None h in the Skagen box: None at full resolution, None after downsampling.
- Diagnostics: None
- Downsample loses pairs: **None**

## Assumptions to confirm

- The coverage edge is derived from the data (0.1-degree cells with fewer than 8 occupied neighbours), not from an official Danish polygon.
- STS readiness counts qualifying minute buckets, not contiguous runs; Phase 4b does run-length detection.
- The Skagen box in config is still a placeholder.
