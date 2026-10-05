# Phase 4a report (generated 2026-09-29 by `make gfw`)

GFW events for every IMO the Phase 3 resolver voted through, 2023-09-03 to 2026-09-14 (the window plus the 180 days before it). Tables are GFW-derived, so local only (rule 7).

## Coverage

- 4663 of 4665 IMOs have a GFW vessel id (0.9996); 10890 vessel ids in total.
- Events per IMO with any: median 191.0, p90 352.0, max 3572.

| type | events | IMOs with any |
|---|---:|---:|
| gap | 2510 | 1339 |
| encounter | 5 | 2 |
| loitering | 239035 | 4532 |
| port_visit | 768139 | 4628 |

- No vessel id was refused by GFW.

## Encounters (task 4b re-test)

- Share of IMOs with any encounter: 0.0004. Effectively zero, which confirms the Phase 0 finding: the public encounter dataset does not return tankers. The encounter features stay in the registry at zero, and the at-sea transfer signal comes from the Phase 4b STS detector alone.

## Russian port visits (B1)

- 22688 port visits at an anchorage with country RUS; 19490 inside the B1 regions (Baltic, Black Sea, Kola Bay; PREREG amendment 2026-09-28), so 3198 elsewhere.
- 11932 of the RUS visits carry no anchorage name, which is why the rule matches country and position rather than a name list.

## Datasets served

- `public-global-encounters-events:v4.0`
- `public-global-gaps-events:v4.0`
- `public-global-loitering-events:v4.0`
- `public-global-port-visits-events:v4.0`

Client: {'cache_hits': 20864, 'live_calls': 496, 'retries': 0}.

## Assumptions to confirm

- Vessel ids come from searching each IMO; no MMSI-and-date fallback was built. It pays for itself only if the IMO share above is low.
- An event's `observed_at` is its end. GFW's event models were run after the fact, so this is the disclosed limitation in PREREG section 11, not a point-in-time guarantee.
- Encounter parsing follows the documented shape; until the share above is non-zero it has not met a live record.
