# Phase 0 report (generated 2026-09-28 by `make report-phase0`)

Every number below is read from `reports/probes/*.json` or `config/window.json`. Edit the go/no-go and assumptions sections by hand after reading.

## What was built

- Repo scaffold, config, WSL2 doctor, structured logging, disk guard.
- Frozen DMA ingest (ADR-14): zip stream to temp Parquet, then `ais_dynamic`, `ais_static`, `ais_artifacts`, `jump_baseline`, `vessel_day` (+ `ais_fullres` on request); markers, registry, timing log; bulk runner with 2 prefetch workers and sequential processing.
- GFW v3 client with SHA-256 disk cache and rate-limit log; OFAC SDN and change-archive parsers; OpenSanctions maritime and label-source probe; ITU MID table; window gate; llama.cpp smoke test.

## DMA (Phase 0 task 2)

- Index: http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/; 928 daily and 210 monthly archives.
- Earliest available day: **2006-03-01** (daily from 2024-03-01, monthly from 2006-03); latest: 2026-09-14.
- Unrecognised archive names (first 50): ['all_sources_2017-02.zip', 'all_sources_2017-03.zip', 'all_sources_2017-04.zip', 'all_sources_2017-05.zip', 'all_sources_2017-06.zip']
- Benchmark day 2026-09-03 from `aisdk-2026-09-03.zip` (daily): zipped 607.6 MB, download 25.2 s.
- Stage 1 (zip stream to temp Parquet): 17,677,807 rows, 0 malformed, 13.97 s, temp 413.3 MB, encoding utf8.
- Header: `['# Timestamp', 'Type of mobile', 'MMSI', 'Latitude', 'Longitude', 'Navigational status', 'ROT', 'SOG', 'COG', 'Heading', 'IMO', 'Callsign', 'Name', 'Ship type', 'Cargo type', 'Width', 'Length', 'Type of position fixing device', 'Draught', 'Destination', 'ETA', 'Data source type', 'A', 'B', 'C', 'D']`
- Unknown columns: none; missing canonical columns: none.
- Stage 2: 9.9 s. Rows that day 17,677,807; after dedupe 10,993,614; bad timestamp or MMSI 0.
- MMSIs: 4,374 vessels; 199 tanker-class; kept 199 (0 via registry); Class A >= 100 m with unknown type: 30.

| table | rows | bytes |
|---|---:|---:|
| ais_dynamic | 155,164 | 2.3 MB |
| ais_static | 546 | 17.1 KB |
| ais_artifacts | 199 | 11.1 KB |
| jump_baseline | 214 | 2.4 KB |
| vessel_day | 4,374 | 71.9 KB |
| ais_fullres | 867,699 | 12.8 MB |

- static rows per dynamic row: 0.0035; jumps (all vessels): 1451; kept MMSIs in Skagen box: 54.
- Ship type values: Fishing 2,496,778; Cargo 1,981,215; Passenger 1,267,919; Undefined 786,183; Tanker 771,247; Sailing 618,638; Other 531,870; Pleasure 515,453; Pilot 437,970; Tug 384,005; SAR 335,309; HSC 286,000; Dredging 260,589; Military 76,957; Reserved 67,115; Law enforcement 50,827; Port tender 37,293; Towing 25,317; Not party to conflict 18,148; Diving 15,209; Medical 8,923; Towing long/wide 8,184; Anti-pollution 7,066; WIG 3,232; Spare 1 1,390; Spare 2 777.
- Cargo type values: not reported 9,315,487; No additional information 1,088,773; Category X 232,033; Reserved for future use 161,440; Category Y 96,092; Category OS 72,598; Category Z 27,191.
- Mobile type values: Class A 9,339,938; Class B 1,143,898; Base Station 349,038; AtoN 155,812; SAR Airborne 4,889; Search and Rescue Transponder 33; Man Overboard Device 6.

## Window gate (task 3)

- Window: **2024-03-01 to 2026-09-14** (30.5 months). Reason: first daily DMA file (or --start) fits the disk budget.
- Parquet per day 2.4 MB; projected 2.5 GB; free at gate 85.1 GB; budget 57.1 GB.
- 49.1 s per day; projected 12.6 machine-hours; 1760.7 days ingested per wall-clock day.
- Evaluable monthly cutoffs today: 19 (2024-08-31, 2026-02-28); supervised: 12.

## Bulk ingest (task 4)

- Days done 928 of 928; failed 0.
- Failed days (first 20): none

## GFW (task 5)

- Go: **True**. IMOs with events by type: {'GAP': 1, 'ENCOUNTER': 0, 'LOITERING': 3, 'PORT_VISIT': 3}.
- Client stats: {'cache_hits': 0, 'live_calls': 25, 'retries': 0}

| IMO | vessel ids | dated identity | shiptypes | GAP | ENCOUNTER | LOITERING | PORT_VISIT |
|---|---:|---|---|---:|---:|---:|---:|
| 9281011 | 3 | 3/3 | OTHER | 0 | 0 | 19 | 3 |
| 9274434 | 2 | 2/2 | OTHER | 0 | 0 | 0 | 0 |
| 9224465 | 2 | 2/2 | OTHER | 0 | 0 | 33 | 2 |
| 9378632 | 2 | 2/2 | OTHER | 1 | 0 | 3 | 3 |
| 9257993 | 3 | 3/3 | OTHER | 0 | 0 | 0 | 0 |

Raw vessel JSON: `reports/phase0_gfw_vessel.json` (local only, gitignored).

## OFAC (task 6)

- Go (2025 archive >= 100 vessel actions with IMO): **True**.
- Current SDN CSV: {'source': 'cache', 'entries': 19385, 'vessels': 1540, 'vessels_with_imo': 1525}
- SDN advanced XML: {"source": "cache", "bytes": 127004763, "element_counts": {"DistinctParty": 19385, "SanctionsEntry": 19571, "EntryEvent": 21749, "Date": 21749, "Feature": 79937, "VersionDetail": 56984}, "example_entry_event": "<ns0:EntryEvent xmlns:ns0=\"https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML\" ID=\"36\" EntryEventTypeID=\"1\" LegalBasisID=\"1\">\n        <ns0:Comment />\n        <ns0:Date CalendarTypeID=\"1\">\n          <ns0:Year>1986</ns0:Year>\n          <ns0:Month>12</ns0:Month>\n          <ns0:Day>10</ns0:Day>\n        </ns0:Date>\n      </ns0:EntryEvent>
- 2023 change archive: {"dates": 0, "entries": 7093, "vessel_entries": 301, "vessel_entries_with_imo": 300, "entries_without_action": 2, "entries_without_date": 7093, "actions": {"add": 102, "remove": 39, "modify": 160}} (source cache)
- 2025 change archive: {"dates": 0, "entries": 15978, "vessel_entries": 545, "vessel_entries_with_imo": 540, "entries_without_action": 1, "entries_without_date": 15978, "actions": {"add": 444, "modify": 88, "remove": 13}} (source cache)
- Sample text: `reports/phase0_ofac_sample.txt`.

## OpenSanctions (task 7)

- Licence: CC BY-NC 4.0 (non-commercial). Maritime version 20260916150302-fcf.
- Maritime CSV: 23398 rows, 20057 with valid IMO; header `['type', 'caption', 'imo', 'risk', 'countries', 'flag', 'mmsi', 'id', 'url', 'datasets', 'aliases']`.
- Risk values: {'mare.detained': 7422, 'sanction': 6120, 'poi': 2943, 'mare.shadow': 857, 'reg.warn': 779}
- Source datasets: {'tokyo_mou_detention': 5901, 'ext_tokyo_mou_psc': 4668, 'ua_war_sanctions': 2943, 'us_ofac_sdn': 2113, 'us_trade_csl': 2113, 'black_sea_mou_detention': 1311, 'ext_abuja_mou_psc': 1038, 'kp_rusi_reports': 954, 'eu_sanctions_map': 866, 'eu_journal_sanctions': 809, 'ca_dfatd_sema_sanctions': 731, 'ch_seco_sanctions': 668, 'gb_fcdo_sanctions': 665, 'paris_mou_banned': 253, 'abuja_mou_detention': 210, 'fr_tresor_gels_avoir': 121, 'un_1718_vessels': 59, 'eu_fsf': 41, 'mc_fund_freezes': 40, 'us_cbp_forced_labor': 4, 'be_fod_sanctions': 2, 'ae_local_terrorists': 1}
- OFAC-sanctioned IMO candidates for GFW probe: 1578.
- EU vessel source candidates (EU-MARE with dates): **['eu_sanctions_map']**
- PSC-like datasets: ['abuja_mou_detention', 'black_sea_mou_detention', 'ext_abuja_mou_psc', 'ext_black_sea_mou_psc', 'ext_gb_coh_psc', 'ext_tokyo_mou_psc', 'paris_mou_banned', 'tokyo_mou_detention']

- `eu_sanctions_map`: {"version": "20260917004802-hwl", "title": "EU Sanctions Map", "ftm_size_mb": 2.0, "vessels": 716, "vessels_with_imo": 716, "vessel_sanctions_EU-MARE": 729, "vessel_sanctions_with_date": 674, "example_dated_sanction": {"programId": ["EU-MARE"], "program": null, "listingDate": null, "startDate": ["2024-06-25"]}, "top_programs": {"EU-MARE": 729, "EU-PRK": 40, "PRK": 40, "EU-IRN": 7, "IRN": 7, "EU-BLR": 6, "BLR": 6, "EU-LBY": 5, "LBY": 5, "EU-MMR": 4, "MMR": 4, "EU-GNB": 4, "GNB": 4, "EU-MLI": 3, "MLI": 3}, "dated_exports": {"20240105": 403, "20250103": 403, "20260102": 403}}
- `eu_journal_sanctions`: {"version": "20260917042501-kev", "title": "EU Council Official Journal Sanctioned Entities", "ftm_size_mb": 20.9, "vessels": 734, "vessels_with_imo": 733, "vessel_sanctions_EU-MARE": 0, "vessel_sanctions_with_date": 0, "example_dated_sanction": null, "top_programs": {"EU-UKR": 3026, "EU-RUS": 2002, "EU-IRN": 709, "EU-BLR": 410, "EU-TAQA-EUAQ": 371, "EU-SYR": 368, "EU-PRK": 326, "EU-HR": 196, "EU-AFG": 140, "EU-MMR": 127, "EU-RUSDA": 100, "EU-COD": 89, "EU-IRQ": 75, "EU-VEN": 69, "EU-LBY": 52}, "dated_exports": {"20240105": 403, "20250103": 403, "20260102": 403}}
- `gb_fcdo_sanctions`: {"version": "20260917050204-jhi", "title": "UK FCDO Sanctions List", "ftm_size_mb": 21.9, "vessels": 664, "vessels_with_imo": 663, "vessel_sanctions": 664, "vessel_sanctions_with_date": 664, "example_dated_sanction": {"programId": ["GB-RUS"], "program": ["The Russia (Sanctions) (EU Exit) Regulations 2019"], "listingDate": null, "startDate": ["2025-09-12"]}, "top_programs": {"GB-RUS": 3469, "The Russia (Sanctions) (EU Exit) Regulations 2019": 3469, "GB-SYR": 353, "The Syria (Sanctions) (EU Exit) Regulations 2019": 353, "GB-ISIL": 336, "Isil (Da'esh) and Al-Qaeda (United Nations Sanctions) (EU E
- `eu_fsf`: {"version": "20260917050204-jhh", "title": "EU Financial Sanctions Files (FSF)", "ftm_size_mb": 16.3, "vessels": 2, "vessels_with_imo": 2, "vessel_sanctions_EU-MARE": 0, "vessel_sanctions_with_date": 0, "example_dated_sanction": null, "top_programs": {"EU-UKR": 2960, "UKR": 2960, "EU-IRN": 707, "IRN": 707, "EU-SYR": 380, "SYR": 380, "EU-BLR": 374, "BLR": 374, "EU-TAQA-EUAQ": 360, "TAQA": 337, "EU-PRK": 255, "PRK": 255, "EU-HR": 170, "HR": 170, "EU-AFG": 140}, "dated_exports": {"20240105": 403, "20250103": 403, "20260102": 403}}

## MID table (task 8)

- 0 MIDs written to `shadowfleet/ingest/mid.csv`; unmatched names: none.

## llama.cpp smoke test (task 9)
**NOT RUN**

## Go / no-go

| source | decision | note |
|---|---|---|
| DMA | TODO | |
| GFW | TODO | |
| OFAC | TODO | |
| OpenSanctions EU/UK | TODO | |
| llama.cpp | TODO | |

## Assumptions to confirm

- DMA CSV header matches `config.DMA_COLUMN_ALIASES` (check `unknown_columns` / `missing_columns` below).
- DMA timestamps are `dd/mm/yyyy HH:MM:SS` UTC (check `rows_bad_timestamp` is near zero).
- Hazardous-cargo substrings in config match DMA `Cargo type` values (compare with the value counts below).
- DMA rows carry static fields on position rows, so change-point compression of `ais_static` is lossless (check `static_rows_per_dynamic_row`).
- The tanker registry grows in date order; a hull that never reports tanker-class before a day is missed on that day (Phase 3 measures this from `vessel_day`).
- Downsample 60 s, 30 s below 3 kn, is fine enough for STS detection (Phase 1 task 8 verifies on `ais_fullres`).
- Jump rule: > 1 km and > 50 kn implied between consecutive deduplicated rows; cells are floor(deg / 0.5).
- The Skagen anchorage box in config is a placeholder until Phase 4b.
- DMA timestamps are treated as UTC. If older data comes as monthly archives, stage 2 rescans the whole month once per day; add a day column in stage 1 before running a monthly backfill.
- The convenience-flag list in config is a placeholder until Phase 3 cites a source.
- GFW `:latest` dataset aliases resolve to versions that include tankers in GAP and ENCOUNTER.
- The OFAC change-archive parser was written without the live PDF; its counts below are the test of it.
- EU vessel listings come from the OpenSanctions dataset carrying program EU-MARE (identified below).

## Next phase depends on

- The running ingest and `config/window.json` (Phases 1 to 4b).
- GFW client and cache (4a), OFAC parsers and OpenSanctions slugs (2), MID table (3).
