# Phase 8/9 report (generated 2026-10-06 by `make briefs`)

650 briefs in 2785.4 s (475 carried over from an interrupted run). Every one was generated under a pydantic-derived JSON schema and then checked by the deterministic verifier; nothing that failed validation was written to disk.

- Throughput: 48.8 completion tokens/s, end to end per brief.
- GPU memory in use at the end of the run (used, total): 10950 MiB, 12227 MiB. llama.cpp allocates weights and KV cache at start-up, so this is the peak.

## Deterministic faithfulness

- **508 of 650 briefs have zero verifier failures (0.7815).**

| failure | briefs |
|---|---:|
| dangling citations | 0 |
| uncovered drivers | 16 |
| ungrounded dates | 11 |
| ungrounded names | 54 |
| ungrounded numbers | 71 |

The four checks: every cited evidence id exists; each of the top-5 SHAP drivers that has records in the bundle has a cited record from that family; every number and date in the prose appears in the bundle (a number may be rounded to fewer decimals); every capitalised name in the prose appears in the bundle (a flag code grounds its country name).

Top drivers with no per-record evidence (stated from the header, so not checkable against a citation): `length_m` (569), `mean_transit_speed_kn` (84), `n_days_observed` (58), `n_transits` (14), `spoof_jump_rate_excess` (10), `n_port_visits` (3), `anchorage_loitering_hours` (3), `days_since_last_russian_port_visit` (1), `n_laden_transits` (1), `n_draught_inconsistencies` (1).

### Changed after the first run's results were seen

The first full run (650 briefs, Oct 6) had 14 clean briefs (2 percent). Reading the failures showed most were the verifier's, and one was the bundle's:

- `(E4)` written in the summary was read as an ungrounded number 4 (the top ungrounded "numbers" were 1 to 13), and the bundle's own words (Header, Drivers) and flag countries (Panama for PAN) as ungrounded names.
- Coverage mapped every self-built detector feature to STS records, so a draught driver cited with its draught record still failed, and it required citations for vessel size and transit features that have no records at all.
- The bundle kept the six most recent GFW events, which were often Gulf port calls, so briefs cited a Russian-port-visit driver against UAE and Kuwaiti ports. Russian port calls now come first, port name and country are included, and the destinations behind `share_russian_destination` are a new evidence family. Driver values are rounded to 2 decimals and the prompt asks for ISO dates and `[E#]` citations.

Every brief was regenerated after these changes (second run: 450 of 650 clean).

After the second run, one more verifier fix and no regeneration: names are matched word by word and case-blind, because SUEZ SOUTH ANCHORAGE in the bundle failed as Suez South Anchorage in the prose, and field names (DWT, callsign) count as known words. Still counted as failures, on purpose: country names the bundle only has as codes beyond the standard name (Turkey, UAE, Turkish), numbers the model computed rather than copied (hours turned into days), and dates not in the bundle. Those are the model departing from the evidence, which is what this check exists to catch; the judge grades whether they are also wrong.

## Per cutoff

| cutoff | briefs | directory |
|---|---:|---|
| 2024-08-31 | 0 | no flagged list |
| 2024-09-30 | 0 | no flagged list |
| 2024-10-31 | 0 | no flagged list |
| 2024-11-30 | 0 | no flagged list |
| 2024-12-31 | 0 | no flagged list |
| 2025-01-31 | 0 | no flagged list |
| 2025-02-28 | 0 | no flagged list |
| 2025-03-31 | 50 | /home/adam1/shadowfleet/reports/briefs/2025-03-31 |
| 2025-04-30 | 50 | /home/adam1/shadowfleet/reports/briefs/2025-04-30 |
| 2025-05-31 | 50 | /home/adam1/shadowfleet/reports/briefs/2025-05-31 |
| 2025-06-30 | 50 | /home/adam1/shadowfleet/reports/briefs/2025-06-30 |
| 2025-07-31 | 50 | /home/adam1/shadowfleet/reports/briefs/2025-07-31 |
| 2025-08-31 | 50 | /home/adam1/shadowfleet/reports/briefs/2025-08-31 |
| 2025-09-30 | 50 | /home/adam1/shadowfleet/reports/briefs/2025-09-30 |
| 2025-10-31 | 50 | /home/adam1/shadowfleet/reports/briefs/2025-10-31 |
| 2025-11-30 | 50 | /home/adam1/shadowfleet/reports/briefs/2025-11-30 |
| 2025-12-31 | 50 | /home/adam1/shadowfleet/reports/briefs/2025-12-31 |
| 2026-01-31 | 50 | /home/adam1/shadowfleet/reports/briefs/2026-01-31 |
| 2026-02-28 | 50 | /home/adam1/shadowfleet/reports/briefs/2026-02-28 |
| 2026-03-31 | 50 | /home/adam1/shadowfleet/reports/briefs/2026-03-31 |

## Samples to read

Paths only: a brief quotes its evidence, which includes GFW-derived values (rule 7), so briefs stay on the local machine.

- `/home/adam1/shadowfleet/reports/briefs/2025-03-31/9230505.md`
- `/home/adam1/shadowfleet/reports/briefs/2025-07-31/9157650.md`
- `/home/adam1/shadowfleet/reports/briefs/2025-11-30/9157650.md`

## Still outstanding

- The faithfulness figure above is the deterministic half only. The cross-family LLM judge runs in `make judge` (Phase 9), which also writes `reports/audit_sheet.csv`; judge-versus-human agreement on that sheet is the credibility number for this whole layer (ADR-10).
