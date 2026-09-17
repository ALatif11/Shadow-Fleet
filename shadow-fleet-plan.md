# Shadow Fleet: Architecture and Build Plan

Planner: Claude (Sep 10, 2026; architecture reconciliation Sep 17, 2026). Implementer: Claude Opus, one phase per session.
Companion files: `CLAUDE.md` (session brief), `phase-prompts.md` (phase prompts, reconciled Sep 17; Phase 1 detail in `phase1-prompt.md`), `SETUP.md` (WSL2 runtime).

## Revision log (architecture reconciliation, Sep 17 2026)
Changes after checking the plan against the target machine and the live data sources. ADR-13 to ADR-16 below hold the detail.
12. Runtime is WSL2 Ubuntu on Adam's PC (ADR-13). The Cowork session shell is a 2-CPU / 3 GB VM with no GPU; it writes and tests code only. Long jobs run in WSL2.
13. Disk: the host drive had 23 GB free (98 percent used) on Sep 17. Raw DMA is never stored beyond the file in flight; ingest streams from the zip and a disk guard pauses below `MIN_FREE_GB`. Adam frees 100+ GB before the bulk run.
14. Because raw DMA is deleted as it is read, the ingest schema is irreversible. Phase 0 therefore freezes the canonical `ais_dynamic` / `ais_static` schema and computes, at ingest time, everything that needs all-vessel data: `jump_baseline` (all vessels, not just tankers) and a compact `vessel_day` summary for every MMSI (so Phase 3 can measure how many tankers the type filter missed). Old Phase 1 task 1 moves into Phase 0 (ADR-14).
15. Window gate rewritten. The old fallback ("15 months if over 60 machine-hours / 400 GB") leaves zero supervised cutoffs: with a 180 d feature window and a 182 d horizon, a 15-month window starting Jun 2025 gives three evaluable cutoffs (Dec 2025 to Feb 2026) and none with a closed training horizon. New rule: ingest everything available; machine-hours are not a reason to shrink; shrink only for disk or deletion-race reasons, never below 21 months without Adam's sign-off (ADR-11 addendum).
16. Label sources corrected (ADR-15). OFAC publishes the SDN change archive as text only through 2023; 2024 to 2026 exist only as PDF (`sdnnew24.pdf`, `sdnnew25.pdf`, `sdnnew26.pdf`), so `pdfplumber` is mandatory, not a fallback, and the SDN advanced XML entry-event dates are the cross-check. EU shadow-fleet vessels are listed in Annex XLII to Council Regulation 833/2014 (OpenSanctions program `EU-MARE`, 673 vessels on Sep 17), not in the EU FSF asset-freeze file (2 vessels). UK vessels come from `gb_fcdo_sanctions`, which carries a listing date. OpenSanctions keeps dated exports at `data.opensanctions.org/datasets/YYYYMMDD/<dataset>/` back to about July 2021, which gives a snapshot-diff cross-check for EU/UK dates.
17. GFW (ADR-16): since Aug 2025 every public AIS-off event is an intentional gap and the `gap-intentional-disabling` parameter was removed, so "intentional" is not a separate feature. Since Jun 2026 GFW registry data includes S&P ownership fields; those are as-of-now and never features. Port visits use dataset `public-global-port-visits-events` v3.1 or later.
18. `phase-prompts.md` reconciled with this plan: Phase 0 no longer uses quarterly cutoffs or download-then-filter; Phase 2 headline label is OFAC∪EU∪UK per PREREG (the OFAC-only gate is gone); Phase 4 is split into 4a/4b and Phase 5 into 5a/5b; population exclusion is "any of the three lists" everywhere; the Phase 9 judge is a different model family.
20. DMA source moved (found Sep 17 during setup): `web.ais.dk` times out; the daily files are served from the S3 bucket `http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/aisdk-YYYY-MM-DD.zip` (third-party report: about 590 to 750 MB zipped and about 17M rows per day). The ingest reads the S3 XML listing first and falls back to the old HTML index.
21. WSL disk: `df` inside WSL reports the sparse virtual disk (about 1 TB), not the Windows drive behind it. The disk guard caps free space by the Windows drive (`SHADOWFLEET_HOST_DISK`, default `/mnt/c`).
22. Window decided with Adam (Sep 17): start at the first daily DMA file, **2024-03-01**, end at the latest (2026-09-14 at the time of the probe). That gives 19 evaluable monthly cutoffs (2024-08-31 to 2026-02-28), 12 of them supervised (from 2025-03-31). The DMA bucket also holds monthly archives back to 2006-03, so older data appears to be consolidated rather than deleted, and the "rolling deletion" race in R5 is probably not real. A backfill into the monthly archives (2023 or 2022) is optional later, after the stage-2 day-column optimisation, if Phase 2 label counts justify it. Benchmark (2026-09-03): 17.7M rows, 11.0M after dedupe, 199 tanker-class MMSIs, about 2.5 MB of Parquet per day, 25 s download, 10 s stage 2.
23. Strait of Hormuz closure (Feb 28 2026) handled as a pre-registered regime break and a prospective forward test, not as a window bound (ADR-17, decided with Adam).
24. Label sources confirmed live (Sep 17, Phase 0 probes). UK: `gb_fcdo_sanctions`, 664 vessels, 663 with IMO, all with a date in `startDate` (not `listingDate`). EU: `eu_sanctions_map`, 716 vessels, 729 Sanction records with `programId = EU-MARE`, 674 dated, the rest carrying the amending regulation's CELEX id in `sourceUrl` (Phase 2 maps CELEX to the Official Journal date). `eu_fsf` has 2 vessels and is not a label source. OpenSanctions dated exports now return 403 (paid delivery token), so the ADR-15 snapshot-diff cross-check is replaced by CELEX/OJ dates plus manual spot checks.
25. GFW probe on 5 OFAC-designated tankers (Sep 17): loitering events exist for tankers (3 of 5) and port visits (3 of 5), one gap; **zero encounters**. The public encounter dataset appears to cover fishing-vessel and carrier pairs, so tanker-to-tanker STS is probably absent from it. Phase 4a re-tests on the full population; if it holds, the GFW encounter features are dropped as empty (reported, not tuned away) and the self-built STS detector in Phase 4b becomes the only source of at-sea transfer signals, which raises its priority.
26. OFAC probe (Sep 17): SDN advanced XML (127 MB) carries 21,749 `EntryEvent` elements, each with a full `Date` (year, month, day) and an `EntryEventTypeID`, for 19,571 sanctions entries; the current SDN CSV has 1,540 vessels, 1,525 with a parseable IMO. So exact add dates for currently listed vessels come from the structured XML, not from PDF parsing. The XML describes only entries still on the list, so the yearly change archive stays the source for removals, modifications and vessels later delisted (ADR-4/ADR-15 amended).
19. CLAUDE.md rule 9 amended: when Adam is present at the start of a session, ask the questions that are his to decide; otherwise assume and record.

## Revision log (post-review, Sep 10 2026)
Changes after a skeptical-reviewer pass; each is reflected in the sections below and in CLAUDE.md.
1. Population at T excludes hulls listed by any of OFAC/EU/UK; "on another Western list" becomes baseline B1b, never a feature (closes the EU-to-OFAC leakage channel).
2. Pre-registration: `PREREG.md` committed in Phase 2 fixes the cutoff rule, primary endpoint, primary model, and frozen feature list; later changes are labelled post-hoc; ablations are reporting only.
3. Monthly cutoffs (rule-based, not hand-picked) with quarterly aggregates derived; event-study lead time.
4. New metrics: alert volume at 50 percent recall, FPR at operating point, first-appearance evaluation, mandatory qualitative review of top-20 non-listed flags.
5. Self-built detectors on DMA tracks (Phase 4b: STS candidates at the Skagen anchorage and elsewhere in Danish waters, anchorage loitering, draught inconsistency, MMSI-IMO churn) with GFW-only vs self-built-only ablations. Graph layer (old Phase 7) demoted to stretch-only to fund it.
6. Spoof jumps normalised per day and 0.5-degree cell to remove GNSS-interference confounds; population cross-checked against GFW classification and dimensions; reported-type changes tracked.
7. Drift: per-cutoff PSI and performance by training-window age.
8. Entity resolution gets a silver test set and a sensitivity arm without GFW-based merges (GFW linking is computed from full history).
9. Faithfulness judge is a different model family from the generator; judge-vs-human kappa is the credibility number.
10. Schedule: Phase 5 split into 5a/5b; llama.cpp smoke test moved to Phase 0; Phase 0 window gate (60 machine-hours / 400 GB) and a Phase 1 mid-run gate.
11. Headline reframed as "evasion-indicator scoring from cooperative data with a point-in-time backtest." No GFW-derived tables committed to the repo.

## 0. What I verified about data (Sep 2026)

| Source | Verified | Consequence for design |
|---|---|---|
| Danish Maritime Authority (DMA) AIS, now served from the S3 bucket `aisdata.ais.dk.s3.eu-central-1.amazonaws.com` (the old `web.ais.dk/aisdata/` index timed out on Sep 17; Phase 0 records the live listing) | Free daily CSV zips of all AIS received by Danish shore stations. Rolling window of roughly the last two years; older files are deleted. | This is the raw-track backbone. Every laden tanker leaving Primorsk / Ust-Luga transits Danish waters. **Download the oldest months in week 1 or they disappear mid-project.** |
| Global Fishing Watch (GFW) API v3 | Free key, non-commercial only. Events API gives ENCOUNTER, LOITERING, PORT_VISIT, and GAP (AIS-off) events per vessel; `vessel_types` includes BUNKER_OR_TANKER; gap events carry an "intentional disabling" flag and implied speed. Vessels API gives identity records combined with registry data; Insights API has flag-change history. | Global open-ocean behaviour (gaps, STS transfers) comes from GFW events, not from raw tracks. Loitering is documented as carrier-vessel-only in the public dataset; treat tanker loitering as unverified until Phase 0 tests it. |
| OFAC SDN | No historical snapshots exist. OFAC publishes an archive of every add/modify/remove action with dates back to 1994 (yearly "SDN Changes" files: text for 1994 to 2023, PDF only for 2001 to present, so 2024 to 2026 are PDF-only) and current XML/CSV with vessel entries carrying IMO numbers. | Point-in-time sanctions state is reconstructable by replaying the change archive. Designation date per IMO is exact. |
| OpenSanctions | Free for non-commercial use. Vessels are first-class entities with IMO, MMSI, flag, `pastFlags`, owner/operator company links, a "maritime" collection CSV, and Port State Control (Tokyo/Paris/Black Sea MoU) inspection datasets that carry ISM manager company IMO numbers. | Secondary label source (EU/UK designations) and the only free source of vessel-to-manager-company edges for non-sanctioned vessels. Everything here is as-of-now: use for labels and post-hoc audit, never as a feature without a date filter. |
| NOAA MarineCadastre | US waters only. | Useless for this theater. Dropped. |
| Local LLM landscape | On 12 GB: Gemma 4 12B at Q4_K_M is roughly 7 GB, Qwen3-14B Q4_K_M roughly 8 GB. 27B-class models need 16 GB+ at Q4. | Pick a 12 to 14B dense model, Q4_K_M, 8k context, served by llama.cpp. Verify exact model names on Hugging Face at build time; the names churn. |

Unverified assumptions (Phase 0 tests them): DMA daily file is roughly 0.5 to 1 GB zipped, 10 to 20M rows; GFW rate limits are tolerable for a few thousand vessels; GFW vessel identity records carry date ranges per name/MMSI/flag; OpenSanctions keeps dated dataset archives (fallback is the OFAC change archive, which is certain).

## 1. Feasibility and risk, ranked

Each item: the risk, why it is fatal or quietly invalidating, the earliest cheap test, and the design response.

### R1. Label leakage through "as-of-now" data (quietly invalidating, most likely)
GFW vessel identity, OpenSanctions vessel records (pastFlags, owner links, the "shadow fleet" topic tag), and the OFAC remarks field all reflect what is known today. A feature like "number of flag changes" computed from current GFW identity records includes changes that happened after the cutoff, often because the vessel was sanctioned. A model built this way will report AUC 0.95 and be worthless.
- Cheap test (Phase 0): pull one sanctioned tanker's GFW identity record and one OpenSanctions record; check whether every name/flag/MMSI entry carries a date range. If not, the field cannot be used as a feature.
- Design response: a single `features(hull_id, T)` function that reads only records with `observed_at <= T`. Every raw record carries `observed_at` (AIS timestamp, GFW event end time, PSC inspection date, sanctions action date). A leakage unit test computes features from the full store and from a store physically truncated at T and asserts equality. Identity changes are derived from DMA static messages (timestamped) plus GFW identity intervals, never from OpenSanctions.

### R2. Positive class too small once you restrict to OFAC, tankers, this theater, and a horizon
OFAC designations of Russia-trade tankers cluster in waves (Feb 2024 Sovcomflot vessels, the January 10 2025 action naming well over a hundred tankers, further actions through 2025 and 2026). Within the DMA window (roughly Sep 2024 to now), a Baltic-transiting tanker population of maybe 1,500 to 3,000 hulls should contain on the order of 100 to 300 later-designated by OFAC, but it could be far fewer after horizon filtering.
- Cheap test (Phase 2, and cheaply approximated in Phase 0): count IMO numbers in OFAC vessel entries with designation date inside the window, intersect with tanker MMSI/IMO seen in one week of DMA data.
- Design response: OFAC is the headline label. EU and UK designations (via OpenSanctions, with their own listing dates) are a secondary label; report both. Gate in Phase 2: if OFAC positives per cutoff < 30, the headline becomes "any of OFAC/EU/UK" with OFAC-only as a sensitivity table. Multiple cutoffs pool evaluations.

### R3. The model learns "trades with Russia," not "evades"
Almost every Baltic tanker later sanctioned was calling at Russian ports. A trivial rule ("visited Primorsk/Ust-Luga/Novorossiysk") will have enormous lift, and any ML model will mostly rediscover it. This is the week-7 finding that embarrasses an unprepared project.
- Cheap test (Phase 5): compute precision@50 for the Russia-port rule alone.
- Design response: make it baseline B1 and evaluate every model both on the full population and stratified within the Russia-calling subpopulation. The honest headline is lift over B1 within that stratum, plus lead time. State this framing in the README from day one.

### R4. AIS gaps in DMA data are meaningless
DMA is terrestrial. A ship leaving Danish waters "disappears," which is coverage, not evasion. Naive gap features from DMA will be pure noise correlated with route.
- Cheap test (Phase 1): plot gap-length distribution for tankers in DMA; nearly all gaps will be exits from coverage.
- Design response: dark-gap features come only from GFW GAP events (satellite-based, with GFW's reception-quality modelling and the intentional-disabling flag). DMA is used for population, identity, draught, destination, transit patterns, and spoofing artefacts (impossible speeds, position jumps) inside coverage.

### R5. Data volume and the rolling deletion window
Two years of DMA is on the order of 700 daily files, plausibly 300 to 700 GB zipped. Cannot be stored raw; and the oldest days are being deleted continuously.
- Cheap test (Phase 0): time one day end to end: download, stream-filter to tankers, write Parquet, delete raw. Extrapolate to the full window.
- Design response (revised Sep 17, ADR-13/14): streaming ingest from the zip, filter at read time (ship type tanker or cargo-type hazardous, plus length >= 100 m), downsample dynamic messages to one per vessel per 60 s (30 s below 3 kn), keep every distinct static state, compute all-vessel tables at ingest, delete the zip. Expected filtered output is a few percent of input. The bulk job starts in Phase 0, oldest day first. Machine-hours are not a reason to shrink the window; only disk or the deletion race are (ADR-11 addendum).

### R6. Entity resolution: MMSI is not a hull
Sanctioned tankers change MMSI, name, and flag; the AIS IMO field is frequently zero, wrong, or shared. If MMSI is the key, identity changes become "new vessels" and the positives fragment.
- Cheap test (Phase 3): for 20 known-sanctioned IMOs, count distinct MMSIs seen in DMA within the window.
- Design response: hull_id = IMO where a stable majority vote over static messages gives a valid check-digit IMO; else GFW vessel id; else a synthetic id from (MMSI, name, dimensions). Build `identity_intervals(hull_id, mmsi, name, callsign, flag_from_mid, start, end)`. Identity-change features are counts of interval boundaries before T.

### R7. GFW API friction
Vessel-id matching (MMSI + date range vs IMO), rate limits, event datasets that exclude tankers, pagination bugs.
- Cheap test (Phase 0): for 5 known-sanctioned IMOs, fetch vessel ids, then GAP and ENCOUNTER events for 2025. Confirm non-empty.
- Design response: cache every response to disk keyed by request hash; population-limited pulls (few thousand vessels) rather than regional pulls; a feature-flag so the pipeline runs with GFW absent (DMA-only feature set) if the API turns out unusable.

### R8. Ownership data does not exist for free at scale
There is no free bulk beneficial-ownership registry. Equasis forbids scraping. What exists: OpenSanctions owner/operator links (mostly for already-sanctioned vessels, so leak-prone), PSC inspection records with ISM manager company IMO and inspection date, GFW registry info for some vessels.
- Cheap test (Phase 7, optional): count population vessels that have at least one dated manager-company edge from PSC data.
- Design response: the ownership/shell-company component is demoted to a "time permits" graph layer built only from dated edges. The behavioural graph (who met whom at sea, from GFW encounters) is the primary graph and is fully point-in-time. Disclose the ownership limitation plainly; it is itself a credible compliance finding ("open data cannot see the corporate layer; here is what behaviour alone recovers").

### R9. Sanctioned vessels that never behaved suspiciously
Designated because of ownership (Sovcomflot), price-cap violations, or being named in a batch. They cap recall for any behaviour-based detector.
- Design response: report recall overall and among vessels with at least one behavioural event in window; tag positives by OFAC program string; treat the residual as a documented ceiling, not a failure.

### R10. VRAM and LLM faithfulness
Brief generator plus judge plus anything else on the GPU concurrently will OOM. Free-form briefs hallucinate dates and coordinates.
- Design response: one llama.cpp server at a time; classifier and graph work are CPU. Briefs are generated schema-first (JSON with evidence-id citations) and rendered to prose; a deterministic checker verifies every cited id exists and every date/number in the prose appears in the evidence bundle.

### R11. Reproducibility and right-censoring
Vessels designated after the horizon are negatives for that cutoff. Later cutoffs turn them positive. Pooled metrics must not double count.
- Design response: evaluation is per (cutoff, hull); pooled metrics are macro-averaged over cutoffs and also reported per cutoff. Store a `labels(cutoff)` table, never a global label.

### R12. EU/UK listings leak into the OFAC label (added post-review)
Hundreds of tankers were EU/UK-listed before OFAC designated them. Under an OFAC-only label, "already on the EU list at T" is knowable and near-perfectly predictive, which proves nothing.
- Cheap test (Phase 2): count OFAC positives per cutoff that were already EU/UK-listed at T.
- Design response: population excludes hulls on any of the three lists as of T; B1b reports the trivial channel; headline label is the union.

### R13. Cutoff selection and multiple comparisons (added post-review)
Hand-picked cutoffs relative to known designation waves, plus many models x labels x k values, invite cherry-picking; per-cutoff lead time mostly measures distance to the next wave.
- Design response: rule-based monthly cutoffs, `PREREG.md` with a single primary endpoint and model, event-study lead time, first-appearance evaluation.

### R14. Not a detector (added post-review)
If every behavioural signal comes from GFW's models, the project is a classifier over someone else's detections. A defense reviewer asks this first.
- Cheap test (Phase 1): count tanker pairs within 500 m at under 2 kn for over 2 h on one Skagen-anchorage day in downsampled DMA data.
- Design response: Phase 4b self-built detectors on raw DMA tracks, with an ablation that isolates their contribution.

### R15. Feature drift under adversary adaptation (added post-review)
Shadow-fleet behaviour shifted through 2025 (fewer gaps under inspection pressure, fraudulent registries). A model trained on early cutoffs may not transfer.
- Design response: PSI per feature family per cutoff and a performance-by-training-age table (Phase 6), shown next to pooled numbers.

## 2. Scope decision

### Theater: Baltic exit, defined by the DMA footprint
Population = every tanker-class hull observed by DMA in the window. Behaviour is assembled from DMA (inside Danish waters) and GFW events (globally, for those hulls). Why this and not the Gulf of Oman, Malaysia STS zones, or Laconia Bay: those have no free raw AIS; they only have GFW events, which cannot define a population honestly or expose identity/draught details. The Danish straits are a physical chokepoint for one of the two largest shadow-fleet trades, and the OFAC/EU/UK action against Russia-trade tankers is the densest label set on Earth for this period. Iran-trade tankers are out of scope and the README says so.

### Minimum viable project (must ship)
1. Point-in-time backtest with monthly rule-based cutoffs, quarterly aggregates, and a six-month horizon, governed by `PREREG.md`.
2. Population and identity from DMA; global events from GFW; labels from OFAC change archive with EU/UK secondary.
3. Feature set (as-of-T): identity churn, flag-of-convenience transitions, draught-based laden transits to/from Russian Baltic ports, GFW dark gaps (count, total hours, intentional flag, max implied distance), encounters at sea (count, with-listed-as-of-T partner count), loitering if available, port visit pattern, vessel age and size, baseline-normalised spoofing artefacts, and self-built DMA detections (STS candidates, anchorage loitering, draught inconsistency, MMSI-IMO churn).
4. Models: rules B0/B1/B1b/B2, logistic regression, LightGBM, unsupervised isolation-forest score. Metrics: precision@k, recall@k, PR-AUC, alert volume at 50 percent recall, FPR, event-study lead time in weeks, drift tables.
5. LLM analyst brief for each flagged vessel with evidence-id citations and an automated faithfulness score.
6. README with the honest findings, including R3, R12, and R14, plus the top-20 false-positive review per cutoff.

### Add if time allows, in this order
1. Two-prompt faithfulness experiment for briefs (schema-first vs free-form).
2. Behavioural graph (stretch only): encounter co-occurrence graph, personalised PageRank from listed-as-of-T seeds, plus the "partner listed later" analysis.
3. Ownership edges from PSC manager-company data with inspection-date filtering.
4. Streamlit map of a flagged vessel's timeline.

### Cut
GNNs (GraphSAGE and friends): the graph has a few thousand nodes and sparse labels; PPR features will capture most of the signal and a GNN would consume two phases for a result the evaluation cannot distinguish from noise. RAG over press releases: not needed; evidence is structured. Real-time streaming, any web UI beyond Streamlit, global AIS, Iran theater, SAR imagery, vector databases.

## 3. Architecture

### Data flow
```
DMA daily zips ──stream filter──> parquet/ais/dt=YYYY-MM-DD/  (tanker-class rows only)
                                       │
                                       ├──> population.parquet (hulls seen in window)
                                       ├──> identity_intervals.parquet  (hull, mmsi, name, callsign, flag, start, end)
                                       ├──> transits.parquet (per hull: Danish-water passages, draught, destination)
                                       ├──> type_changes.parquet, ais_artifacts/, jump_baseline/ (per day and 0.5-degree cell)
                                       └──> detect/: sts_candidates, anchorage_loitering, draught_inconsistency (self-built, Phase 4b)
GFW API ──cached JSON──> parquet/gfw_events/ (gap, encounter, loitering, port_visit)  keyed by hull
OFAC change archive ──parser──> sanctions_actions.parquet (imo, name, program, action, date, source)
OpenSanctions maritime + PSC ──> secondary_labels.parquet, manager_edges.parquet (with observed_at)
ITU MID table ──> mid_to_flag.csv (static)

features(hull, T)  ──>  feature_matrix(cutoff=T).parquet     (only records with observed_at <= T)
labels(T, horizon) ──>  labels(cutoff=T).parquet
backtest.py: for T in cutoffs: fit on train cutoffs, score at T, metrics, lead time
briefs.py: top-k flagged at T ──> evidence bundle JSON ──> llama.cpp ──> brief.md + faithfulness.json
```

### Repo layout
```
shadowfleet/
  data/raw/          # gitignored, transient
  data/parquet/      # gitignored, DuckDB reads these
  data/cache/gfw/    # gitignored, response cache
  shadowfleet/
    ingest/{dma.py, gfw.py, ofac.py, opensanctions.py, mid.py}
    resolve/identity.py
    detect/{sts.py, loitering.py, draught.py, spoof.py}
    features/{asof.py, ais.py, gfw.py, identity.py, detect.py, graph.py}
    labels/labels.py
    models/{rules.py, tabular.py, anomaly.py}
    backtest/{harness.py, metrics.py, drift.py}
    briefs/{bundle.py, generate.py, verify.py}
    cli.py
  tests/
  notebooks/         # exploration only; nothing load-bearing
  reports/           # figures, metrics tables, final README sections
  CLAUDE.md  PREREG.md  README.md  pyproject.toml  Makefile
```

### Decision records

**ADR-1 Theater and population.** Options: global via GFW events only; Gulf of Oman / Malacca; Baltic exit via DMA. Choice: Baltic exit. Why: only free raw AIS with multi-year history; physical chokepoint; densest labels. Revisit if: DMA stops publishing, or Phase 2 shows fewer than 30 OFAC-or-EU/UK positives per cutoff.

**ADR-2 Raw AIS source.** Options: DMA, NOAA MarineCadastre, GFW 4Wings rasters, AISHub (requires feeding data). Choice: DMA. Why: per-vessel messages with static fields (name, IMO, callsign, draught, destination), free, historical. Revisit if: file sizes make the window intractable (then shrink window, keep source).

**ADR-3 Global behaviour source.** Options: none (DMA-only), GFW events, paid satellite AIS. Choice: GFW events, population-limited. Why: only free source of satellite-informed gap and encounter detection. Revisit if: Phase 0 probe finds tankers excluded from GAP/ENCOUNTER datasets; then DMA-only features and the README states the limitation.

**ADR-4 amended (Sep 17).** Primary source for an add date is now the SDN advanced XML `SanctionsEntry/EntryEvent/Date` (present for every current entry, with `EntryEventTypeID` distinguishing the event kind), cross-checked against the change archive. The change archive remains the only source for removals, modifications, and entries no longer on the list, so a vessel delisted before T is still handled correctly.

**ADR-4 Labels and point-in-time reconstruction.** Options: current SDN snapshot with `List_date`; OpenSanctions `first_seen`; OFAC change archive replay. Choice: replay the change archive as the primary source of (IMO, action, date), cross-checked against OpenSanctions first_seen; EU/UK dates from OpenSanctions. Why: OFAC keeps no snapshots, the change archive is the only authoritative dated record. Disclosure: removals and modifications are replayed too, so a vessel de-listed before T is not a positive at T. Revisit if: archive parsing proves unreliable for vessel entries (fall back to OpenSanctions first_seen and disclose).

**ADR-5 Hull identity key.** Options: MMSI; GFW vessel id; IMO with fallbacks. Choice: IMO from majority vote of check-digit-valid values in static messages per (MMSI, 30-day window), fallback GFW id, fallback synthetic. Why: IMO survives renames and reflags. Revisit if: more than 20 percent of population hulls lack a valid IMO (then evaluate the synthetic-id fragmentation explicitly). Post-review: GFW identity linking is computed from full history, so GFW-based merges are a mild leak; a silver test set (OpenSanctions and GFW IMO-MMSI pairs) measures resolution quality and a sensitivity arm disables GFW merges.

**ADR-6 Storage.** Options: PostgreSQL/PostGIS; DuckDB over Parquet; SQLite. Choice: DuckDB + Hive-partitioned Parquet, GeoPandas/shapely only for polygon tests (Danish waters, Russian port zones). Why: single machine, columnar scans over hundreds of millions of rows, zero ops. Revisit if: a map UI needs concurrent writes (it will not).

**ADR-7 Detection models.** Options: rules; logistic regression; gradient boosting; isolation forest; sequence models over tracks. Choice: rules (B1 Russia-port, B1b already-on-another-Western-list, B2 hand-weighted evasion score) as baselines; logistic regression and LightGBM as supervised models; isolation forest as the unsupervised anomaly score reported alongside. No sequence models. Why: few hundred positives, tabular as-of features, need for explainability into briefs (SHAP values become evidence pointers). Revisit if: LightGBM cannot beat B2 by a meaningful margin at k=50 (that result is itself the finding; report it).

**ADR-8 Graph (stretch only after review).** Options: none; networkx/igraph with PPR; PyTorch Geometric GNN. Choice: igraph PPR over the encounter graph built from GFW encounters with end time <= T, seeds = hulls sanctioned as of T; optional dated manager-company edges. Why: cheap, point-in-time by construction, interpretable ("met 3 vessels that were already sanctioned"). GNN cut (see Scope). Revisit if: PPR feature yields no lift and time remains; still prefer more label work over a GNN.

**ADR-9 Local LLM and serving.** Options: Qwen3-14B Q4_K_M, Gemma 4 12B Q4_K_M, an 8B model at Q6; Ollama vs llama.cpp server vs vLLM. Choice: llama.cpp server (CUDA 12.8 build for Blackwell) with an OpenAI-compatible endpoint; primary model Gemma 4 12B Q4_K_M, fallback Qwen3-14B Q4_K_M, both at 8k context, one instance at a time. Why: roughly 7 to 8 GB weights plus KV cache leaves headroom; llama.cpp supports grammar/JSON-schema constrained decoding, which is what makes schema-first briefs reliable. Revisit if: throughput is under 15 tok/s (drop to an 8B model at Q5) or JSON-schema decoding is unstable with the chosen model.

**ADR-10 Brief generation and faithfulness.** Options: free-form prose from a feature dump; schema-first JSON citing evidence ids, then templated prose; RAG over press releases. Choice: schema-first. Pipeline: evidence bundle (top SHAP features mapped to concrete events with ids, dates, coordinates, partner ids) -> constrained JSON (`findings[]` each with `claim`, `evidence_ids[]`, `severity`) -> renderer -> prose -> deterministic verifier (ids exist, dates and numbers in prose appear in bundle, no vessel names not in bundle) -> LLM judge from a different model family than the generator (Qwen judges Gemma or the reverse) for semantic entailment of each claim, with judge-vs-human kappa on a 30-finding audit as the credibility number. Why: makes faithfulness measurable and mostly deterministic, and avoids a model grading its own blind spots. Revisit if: constrained decoding kills prose quality; then free-form with the same verifier as a second arm.

**ADR-11 Backtest protocol (revised after review).** Options: single cutoff; hand-picked quarterly cutoffs; rule-based monthly cutoffs. Choice: monthly cutoffs = last day of every calendar month inside the window (fixed in `PREREG.md`), quarterly aggregates derived from them, feature window 180 days before T, horizon 182 days after T. Population at T excludes hulls listed by any of OFAC/EU/UK on or before T. Training for supervised models uses only cutoffs whose horizon closed before T (expanding window); early cutoffs are scored by rules and unsupervised models only. Lead time is event-study (designation date minus the earliest cutoff at which the hull entered the top k). Why: hand-picked quarterly cutoffs looked tuned to designation waves and gave only two supervised scoring points; monthly gives roughly ten and removes the choice. Revisit if: positives per month are too thin for stable per-cutoff tables (keep monthly scoring, report quarterly aggregates only).

**ADR-12 Self-built detectors (added after review).** Options: rely entirely on GFW events; build detectors on DMA tracks. Choice: both, with ablations. Self-built on DMA: STS candidates (two tanker hulls within 500 m, both under 2 kn, over 2 h, outside port polygons, draught change before/after), anchorage loitering, declared-vs-observed draught inconsistency, MMSI-IMO churn, baseline-normalised spoof jumps. Why: a defense reviewer will not credit a project that only consumes GFW's detections; the Skagen anchorage is a documented STS site inside the one area with raw tracks. Revisit if: Phase 1's STS-readiness count is zero (then the detector is dropped and the finding reported).

**ADR-13 Runtime and storage (added Sep 17).** Options: native Windows; WSL2 Ubuntu; the Cowork VM. Choice: WSL2 Ubuntu 24.04, repo and `data/` on the WSL ext4 filesystem (`~/shadowfleet`), never under `/mnt/c` (NTFS through 9P is several times slower for Parquet). Python 3.11+ via `uv`. llama.cpp built inside WSL2 against CUDA 12.8+ (the Windows NVIDIA driver provides the GPU; only the WSL CUDA toolkit is installed in Ubuntu). Why: Makefile and POSIX tooling as written; GPU passthrough works; the Cowork VM has 2 CPUs, 3 GB RAM, no GPU and short command timeouts. Storage rule: at most `DMA_MAX_WORKERS` (2) raw files on disk at once; zips are streamed, never unzipped to disk; a disk guard pauses ingest when free space drops below `MIN_FREE_GB` (default 25) and resumes when it recovers. The WSL virtual disk grows but does not shrink by itself; SETUP.md documents compaction. Revisit if: Phase 0 measures filtered output above 1 GB per day (then projected Parquet exceeds a reasonable budget and the downsample is revisited before the bulk run).

**ADR-14 Ingest schema frozen in Phase 0 (added Sep 17).** Because raw files are deleted, anything not written at ingest is lost for good. Choice: per file, stream the CSV into a temporary all-vessel Parquet (compressed, deleted after the day is processed), then with DuckDB write: `ais_dynamic` (tanker-class MMSIs, deduplicated on (mmsi, timestamp, lat, lon), downsampled to the first row per 60 s, or per 30 s when SOG < 3 kn so STS and loitering detection keep resolution), `ais_static` (every static/voyage row for tanker-class MMSIs, all columns), `ais_artifacts` (per tanker MMSI per day: jumps, bad positions, cells), `jump_baseline` (per day and 0.5-degree cell, over all vessels), and `vessel_day` (one row per MMSI per day for every vessel: rows, class, modal ship type, cargo type, length, width, modal IMO, modal name). Tanker-class MMSI set for a day = MMSIs with any tanker-class row that day, union the persistent registry of MMSIs seen as tanker-class on earlier days, union `EXTRA_MMSI_ALLOWLIST`. The benchmark day also keeps full-resolution tanker rows in `ais_fullres/` for the Phase 1 STS-readiness check. Why: jump baselines need all vessels; the population cross-check needs to know what the filter dropped; downsampling is irreversible, so the finer interval is the default. Revisit if: `vessel_day` or full-resolution rows are too large (measured in Phase 0).

**ADR-15 Label sources (corrected Sep 17, confirmed against live data the same day).** OFAC: replay yearly SDN change archives (text parser for 2022 to 2023, PDF parser for 2024 onward), cross-checked against the entry-event dates in the current SDN advanced XML and OpenSanctions `us_ofac_sdn` listing dates. EU: vessels under Annex XLII to Reg 833/2014 (OpenSanctions program `EU-MARE`; Phase 0 records which dataset slug carries them and whether a listing date is present). UK: `gb_fcdo_sanctions` vessels with `listingDate`. EU: `eu_sanctions_map` Sanction entities with `programId = EU-MARE` and `startDate`; where `startDate` is missing, the CELEX id in `sourceUrl` gives the amending regulation, mapped to its Official Journal date. UK: `gb_fcdo_sanctions` vessels with `startDate`. Dated OpenSanctions exports are no longer free (403 without a delivery token), so the cross-check is CELEX/OJ dates plus a manual spot check of 5 vessels per source against official publications. Revisit if: EU entries lack listing dates and snapshots are unavailable (then EU dates come from the Official Journal amendment dates, parsed by hand for the handful of packages in the window).

**ADR-16 GFW usage (added Sep 17).** Base `https://gateway.api.globalfishingwatch.org/v3`. Dataset ids pinned in config (`public-global-gaps-events`, `public-global-encounters-events`, `public-global-loitering-events`, `public-global-port-visits-events`, `public-global-vessel-identity`), versions recorded from the first live response. All public gap events are intentional since Aug 2025, so no separate intentional feature; gap features are counts, hours and implied distance. Registry ownership fields (S&P, since Jun 2026) are as-of-now and excluded from features by the leakage static check. Only `built` year is used, as a static attribute. Rate-limit headers are logged from every response (GFW added custom usage headers in May 2026).

**ADR-17 Hormuz regime break and forward test (added Sep 17, decided with Adam).** Context: the Strait of Hormuz has been effectively closed since the Feb 28 2026 strikes (IRGC announcement Mar 2; about 95 percent less traffic by Jul to Aug 2026). That changed global tanker demand and routing, which can indirectly change Russia-trade shadow-fleet behaviour in the Baltic. Options: start the window at the closure; ignore it; treat it as a regime break. Choice: regime break. `PREREG.md` fixes `REGIME_BREAKS = {hormuz_closure: 2026-02-28}` before labels are built. Phase 6 reports metrics and PSI drift for cutoffs before and after it (post-closure cutoffs are few until their horizons close). Why not a window start: 180 d of history plus a 182 d horizon means the first evaluable post-closure cutoff (Aug 31 2026) resolves around Mar 2027, so a closure-anchored window has zero evaluable cutoffs today. Anchoring the window to a news event would also invite the tuned-cutoff criticism (R13). The Gulf and Iran trade stays out of scope. Forward test (Phase F): on or after 2026-10-01, score the current population with the frozen model, commit the ranked top 50 (hull ids and scores, no GFW-derived fields) to git, and evaluate it against OFAC/EU/UK designations as they occur through the 182 d horizon (about Apr 2027). This is a prospective, tamper-evident result that complements the backtest. Revisit if: the model is not frozen by Oct 2026 (then score with the best available rules baseline and label it as such).

**ADR-11 addendum (window arithmetic, Sep 17).** Let W be the first ingested day and D the last day whose horizon is closed (today minus 182 d). Cutoffs run from the first month-end at or after W + 180 d to the last month-end at or before D. Supervised scoring needs T' + 182 d <= T, so the first supervised cutoff is about 12 months after W. On Sep 17 2026, D is about Mar 19 2026, so the last evaluable cutoff is Feb 28 2026. A window starting Sep 2024 gives 12 evaluable cutoffs (Mar 2025 to Feb 2026) and 6 supervised ones (Sep 2025 to Feb 2026); each month the project runs adds one of each. A window starting Jun 2025 gives 3 and 0. Every day the bulk ingest is delayed loses a day at the front if DMA deletes on a rolling basis.

## 4. Evaluation design

### Pre-registration
Before Phase 2 builds labels, commit `PREREG.md` declaring: cutoff rule (monthly, last calendar day), primary endpoint (precision@50 within the B1 stratum, label OFAC∪EU∪UK), primary model (LightGBM), frozen feature list by family, and the rule that ablations are reporting only. Any later change is labelled post-hoc in reports and README. This does not make feature design blind to the test period (it was informed by reporting through 2026); it limits what can be tuned after results are seen, and the README says both things.

### Backtest protocol
For each cutoff T (monthly):
1. Physically build `store_T` = all records with `observed_at <= T` (a DuckDB view with a WHERE clause; the leakage test also materialises it).
2. Population_T = hulls with at least one DMA observation in [T-180d, T] and no unrevoked listing by any of OFAC, EU, or UK on or before T.
3. Features_T = `features(hull, T)` for every hull in Population_T.
4. Labels_T = 1 if the hull has a designation action in (T, T+182d], else 0. Record `designation_date`.
5. Score with each model. Supervised models are fit on the concatenation of (Features_T', Labels_T') for all T' < T with T' + 182d <= T (label must be fully observed before it is used to train).
6. Metrics per T, monthly and quarterly aggregates, per-T tables retained. Also a first-appearance evaluation in which each hull is scored only at its first eligible cutoff, so repeated hulls cannot inflate pooled numbers.

### Metrics
- Precision@k and recall@k for k in {25, 50, 100}; PR-AUC. No ROC-AUC in headlines (tiny positive class makes it flattering).
- Lead time (event-study): for each hull designated in the window, weeks between its designation date and the earliest cutoff at which it ranked in the top k; median and distribution. Per-cutoff lead time is a supplement. Still bounded by the horizon and right-censored at the last cutoff; say so.
- Alert volume: number of alerts needed to reach 50 percent recall, and FPR at the k=50 operating point.
- Qualitative false-positive review: top 20 non-listed flags per quarterly aggregate, each with a labelled reason (lightering, CPC crude, ice-class, unknown).
- Stratified: all of the above restricted to hulls flagged by B1 (Russia-port callers). This is the headline lift table.
- Label variants: OFAC-only and OFAC∪EU∪UK.
- Calibration plot for the supervised model, because a compliance audience asks.

### Baselines that must be beaten (or the failure reported)
- B0 random.
- B1 rule: called at a Russian Baltic or Black Sea port in window (from DMA destination text, draught pattern, and GFW port visits).
- B1b rule: listed by a Western authority other than the one defining the label (only meaningful for the OFAC-only sensitivity table; reported to expose the trivial channel).
- B2 hand-weighted rules: B1 plus any GFW gap > 24h with intentional flag, plus any encounter with a sanctioned-as-of-T hull, plus a flag change into a known convenience registry (Gabon, Cameroon, Comoros, Sierra Leone, Eswatini, Guyana, Cook Islands, Palau, Vanuatu; keep the list in a config file with a source citation), plus vessel age > 15 y. Score = weighted sum, weights fixed before seeing labels.
- B3 logistic regression on the full feature set.
- Then LightGBM, isolation forest, and (stretch) LightGBM + PPR features. Ablations by feature family (identity, DMA transits, GFW gaps, GFW encounters, self-built detections, graph) plus GFW-only vs self-built-only arms. Ablations never drive feature removal.
- Drift: PSI per family per cutoff; performance by training-window age.

### Leakage tests (must pass before any metric is reported)
1. Feature equality: `features(h, T)` from full store equals `features(h, T)` from the truncated store for a sample of 200 hulls at each T.
2. No feature uses any column sourced from OpenSanctions or OFAC except `listed_as_of_T` fields computed from dated actions, and those are used only for partner features and population exclusion. No feature uses GFW registry ownership fields.
3. Permutation sanity: shuffle labels; PR-AUC must fall to base rate.
4. Time-direction check: train on later cutoffs, score earlier ones; if that is dramatically better than forward, something is leaking.
5. Entity-resolution sensitivity: primary metrics recomputed with GFW-based hull merges disabled; the delta is reported.

### Brief faithfulness
- Generate briefs for the top 50 at each cutoff for the best model.
- Deterministic verifier: citation validity (every `[E#]` resolves), coverage (each of the top 5 SHAP contributors has at least one cited evidence item), numeric/date grounding (every date, count, duration, coordinate in the prose string-matches a value in the bundle after normalisation), name grounding (every vessel or company name appears in the bundle).
- LLM judge (different model family from the generator, temperature 0): for each `finding`, entailed / partially / not entailed by the cited evidence items. Report entailment rate.
- Human audit: Adam reads 30 briefs against their bundles and records errors by type. Report agreement between the judge and the human on those 30.
- Headline: percentage of briefs with zero verifier failures and 100 percent judge entailment, reported alongside judge-vs-human kappa; kappa is the credibility number.

## 5. Phased build plan

Each phase is one Opus session. Adam's time per phase: 4 to 12 hours to run, verify acceptance, and correct. Phases 0 to 5b are the critical path and front-load every risk in Section 1. Phase 1 (wall-clock) and Phase 5a (developer time) are the likeliest to overrun; both have gates.

| # | Phase | Goal | Key acceptance criteria | Unblocks |
|---|---|---|---|---|
| 0 | Probes, scaffold, frozen ingest | Repo skeleton, config, WSL2 doctor; probe every data source with real calls; frozen ingest schema (ADR-14) with disk guard; benchmark one DMA day; start the bulk ingest oldest first (filter and delete, never download-only); 30-minute llama.cpp CUDA smoke test; window gate (ADR-11 addendum) | One DMA day ingested with timings and row counts; GFW returns vessel ids and GAP/ENCOUNTER events for 5 known-listed IMOs; OFAC 2025 PDF change archive parsed into >= 100 vessel actions with dates; EU/UK vessel sources located; llama-server answers one prompt on the GPU; window recorded in `config/window.json` and `reports/phase0.md` | Everything |
| 1 | DMA ingest completion (see `phase1-prompt.md`) | Finish and monitor the unattended ingest; population, type-change table, gap evidence, STS-readiness check | `make ingest-dma-check` shows < 3 percent failed days; population table with counts per month; gap-length plot demonstrating R4; STS pair count on a Skagen day; raw directory empty | 2, 3, 4b, 5a |
| 2 | Labels and PREREG | Sanctions action table from OFAC archive replay plus OpenSanctions EU/UK; `labels(T, horizon)` and `listed_as_of`; `PREREG.md` committed before labels are inspected | PREREG.md in git history before the positives table; positives per monthly cutoff for both label variants, with the count already EU/UK-listed at T (R12); spot-check of 10 IMOs against press releases | 5a |
| 3 | Identity resolution | hull_id assignment, identity_intervals, MID flag mapping, silver test set, population cross-check against GFW type and dimensions | >= 80 percent of population hulls have a valid IMO; silver-set precision/recall reported; share of merges depending on GFW linking reported; identity-change counts computed as-of arbitrary T | 4a, 4b, 5a |
| 4a | GFW events | Cached, population-limited pull of GAP, ENCOUNTER, LOITERING, PORT_VISIT; Parquet with `observed_at` | Coverage report: percent of population with any GFW event; pipeline runs with `--no-gfw` | 5a |
| 4b | Self-built detectors | STS candidates, anchorage loitering, draught inconsistency, MMSI-IMO churn on DMA tracks, all with `observed_at` | Detection tables with counts; a plot of STS candidates at Skagen over time; 10 candidates hand-checked and described | 5a |
| 5a | Feature store and leakage suite | `features(hull, T)`, FEATURES registry frozen per PREREG, five leakage tests | All leakage tests pass; feature matrix for every monthly cutoff | 5b |
| 5b | Baselines and harness | B0/B1/B1b/B2/B3, harness, metrics, first-appearance evaluation, event-study lead time | Metrics for every cutoff and both label sets; stratified table; `make backtest` reproduces it under 30 min | 6, 8 |
| 6 | Models, ablations, drift, SHAP | LightGBM, isolation forest, ablations (families, GFW-only, self-built-only), calibration, PSI drift, SHAP export, top-20 FP review sheet | Beats or documents failure to beat B2; ablation and drift tables; SHAP per (hull, T); FP review sheet for Adam | 7, 8 |
| 7 | Graph layer (stretch only) | Encounter graph, PPR feature, dated manager edges | Marginal lift table vs Phase 6; graph built strictly from events with end <= T; skipped unless ahead of schedule | 8 |
| 8 | Briefs | Evidence bundler, llama.cpp server setup, constrained generation, renderer | 50 briefs per cutoff generated unattended; throughput logged; VRAM peak logged | 9 |
| 9 | Faithfulness evaluation | Verifier, cross-family judge, human-audit sheet, kappa | Faithfulness table; 30-finding audit completed by Adam; judge-vs-human kappa; failure taxonomy | 10 |
| 10 | Report and portfolio | README with findings, figures, limitations, licence note; reproducibility check from a clean clone; resume material with real numbers | `git clone && make all` works given data on disk; README contains R3, R12, R14 discussion, FP review, and the no-committed-GFW-data note | done |

Week mapping at 12 to 15 h/week: Phase 0 (wk 1), 1 (wk 1-2, mostly machine time), 2 (wk 2), 3 (wk 3), 4a (wk 4), 4b (wk 4-5), 5a (wk 5-6), 5b (wk 6), 6 (wk 7), 8 (wk 8), 9 (wk 9), 10 (wk 10), buffer (wk 11-12); 7 only if the buffer is intact at week 8.

Things that will break in week 7 and what week 1 does about them: DMA files deleted (Phase 0 starts the filtered ingest oldest first); a jump baseline or population cross-check that needs vessels the filter dropped (Phase 0 writes `jump_baseline` and `vessel_day` for all vessels at ingest); GFW identity is undated (Phase 0 probe decides feature policy); AUC too good to be true (Phase 5 leakage tests exist before any model); positives vanish under OFAC-only (Phase 2 gate); "it just learned Russia" (stratified evaluation is in the harness from Phase 5); OOM during briefs (Phase 8 runs the LLM alone, everything else CPU); disk full (Phase 0 ingest streams and deletes raw, with a disk guard); llama.cpp Blackwell build eats a weekend (Phase 0 smoke test); "you just classified GFW's detections" (Phase 4b exists); "your cutoffs are tuned" (PREREG.md is in git before labels).

## 6. Handoff kit
See `CLAUDE.md` and `phase-prompts.md`.

## 7. Portfolio framing

### Fintech / sanctions-AML compliance reading
- Point-in-time correctness is the central discipline of both quant backtesting and model-risk governance; this project treats it as a tested software contract, not a caveat.
- Entity resolution across mutable identifiers (MMSI, name, flag) is the same problem as customer/counterparty resolution in KYC.
- Rules baselines vs ML with ablations and calibration mirror how a compliance model would be validated before deployment (and the "rules are hard to beat" finding is credible to that audience).
- Explainable alerts with evidence citations and a measured hallucination rate speak directly to analyst-facing LLM tooling in screening teams.

### Defense / maritime domain awareness reading
- Evasion-indicator scoring from cooperative data: self-built STS, loitering and draught-inconsistency detection on raw terrestrial AIS, fused with satellite-informed gap events. Do not say "dark fleet detection"; the reviewer knows the difference.
- Multi-source fusion: terrestrial AIS, satellite-informed events, watchlists, port-state inspection records.
- Network analysis of at-sea rendezvous (who meets whom) as a proxy for STS oil transfers.
- Analyst decision support: an on-prem LLM producing sourced briefs, no cloud dependency.

### Resume bullets (fill placeholders from `reports/`)
- Built a point-in-time backtested sanctions-evasion indicator model over [N] tanker hulls transiting the Baltic exit using open AIS and watchlist data; ranked [P] percent of tankers later listed by OFAC, the EU, or the UK in the top 50 within a six-month horizon (precision@50 of [X] among Russia-trade tankers not yet on any list), a median of [W] weeks before listing under a pre-registered evaluation.
- Engineered a leakage-tested as-of feature store (DuckDB/Parquet, [R]M AIS messages) reconstructing identity changes, laden transits, satellite-detected AIS gaps, and at-sea encounters exactly as knowable on each cutoff date.
- Showed that a gradient-boosted model [beat / did not beat] a hand-weighted rules baseline by [D] points of PR-AUC, and that most lift over a naive Russia-port rule came from [feature family], via per-family ablations across [C] monthly cutoffs, and that self-built AIS detectors contributed [S] of the lift independent of Global Fishing Watch events.
- Generated analyst briefs with a locally hosted [model] on a single 12 GB GPU using schema-constrained decoding; [F] percent of briefs passed deterministic citation and numeric-grounding checks with [J] percent claim-level entailment under a cross-family LLM judge (kappa [K] against a manual audit of 30 findings).
- Resolved [N] vessel identities across [M] MMSI reassignments and [K] renames using IMO majority voting over AIS static messages, cross-checked with Global Fishing Watch identity records.

Talking point for both audiences: the open-data ceiling. Ownership through shell companies is invisible in free data; the project quantifies how far behaviour alone gets you and states where paid registries (Equasis-derived, Lloyd's) would change the answer.
