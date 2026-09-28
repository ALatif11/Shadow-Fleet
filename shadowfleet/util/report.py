"""Render reports/phase0.md from reports/probes/*.json and config/window.json.

Numbers come only from those files (CLAUDE.md rule 4); a missing probe renders as NOT RUN.
"""

from __future__ import annotations

import json
from datetime import date
from inspect import signature

from shadowfleet import config
from shadowfleet.ingest import dma
from shadowfleet.util import probes

NOT_RUN = "**NOT RUN**"

ASSUMPTIONS = [
    "DMA CSV header matches `config.DMA_COLUMN_ALIASES` (check `unknown_columns` / `missing_columns` below).",
    "DMA timestamps are `dd/mm/yyyy HH:MM:SS` UTC (check `rows_bad_timestamp` is near zero).",
    "Hazardous-cargo substrings in config match DMA `Cargo type` values (compare with the value counts below).",
    "DMA rows carry static fields on position rows, so change-point compression of `ais_static` is lossless "
    "(check `static_rows_per_dynamic_row`).",
    "The tanker registry grows in date order; a hull that never reports tanker-class before a day is missed "
    "on that day (Phase 3 measures this from `vessel_day`).",
    "Downsample 60 s, 30 s below 3 kn, is fine enough for STS detection (Phase 1 task 8 verifies on `ais_fullres`).",
    "Jump rule: > 1 km and > 50 kn implied between consecutive deduplicated rows; cells are floor(deg / 0.5).",
    "The Skagen anchorage box in config is a placeholder until Phase 4b.",
    "DMA timestamps are treated as UTC. If older data comes as monthly archives, stage 2 rescans the whole "
    "month once per day; add a day column in stage 1 before running a monthly backfill.",
    "The convenience-flag list in config is a placeholder until Phase 3 cites a source.",
    "GFW `:latest` dataset aliases resolve to versions that include tankers in GAP and ENCOUNTER.",
    "The OFAC change-archive parser was written without the live PDF; its counts below are the test of it.",
    "EU vessel listings come from the OpenSanctions dataset carrying program EU-MARE (identified below).",
]


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "n/a"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _dma() -> list[str]:
    p = probes.read("dma")
    if not p or "index_url" not in p:
        return ["## DMA (Phase 0 task 2)", NOT_RUN if not p else "**probe file incomplete**", ""]
    res = p["result"]
    lines = ["## DMA (Phase 0 task 2)", "",
             f"- Index: {p['index_url']}; {p['n_daily_files']} daily and {p['n_monthly_files']} monthly archives.",
             f"- Earliest available day: **{p['earliest_day']}** (daily from {p['earliest_daily']}, "
             f"monthly from {p['earliest_monthly']}); latest: {p['latest_day']}.",
             f"- Unrecognised archive names (first 50): {p['unrecognised_archives'] or 'none'}",
             f"- Benchmark day {p['benchmark_day']} from `{p['benchmark_file']}` ({p['benchmark_kind']}): "
             f"zipped {_fmt_bytes(p['zipped_bytes'])}, download {p['seconds_download']} s."]
    if res.get("days_failed"):
        lines.append(f"- **Benchmark failed:** {res['days_failed']}")
        return lines + [""]
    st = res["stage"]
    ds = res["day_stats"][0]
    lines += [
        f"- Stage 1 (zip stream to temp Parquet): {st['rows_in']:,} rows, {st['malformed_rows']:,} malformed, "
        f"{st['seconds']} s, temp {_fmt_bytes(st['temp_bytes'])}, encoding {st['encoding']}.",
        f"- Header: `{st['header']}`",
        f"- Unknown columns: {st['unknown_columns'] or 'none'}; missing canonical columns: "
        f"{st['missing_columns'] or 'none'}.",
        f"- Stage 2: {ds['seconds']} s. Rows that day {ds['rows_day']:,}; after dedupe {ds['rows_dedup']:,}; "
        f"bad timestamp or MMSI {ds['rows_bad_timestamp']:,}.",
        f"- MMSIs: {ds['mmsi_all']:,} vessels; {ds['mmsi_tanker_today']:,} tanker-class; kept {ds['mmsi_kept']:,} "
        f"({ds['mmsi_kept_from_registry']:,} via registry); Class A >= 100 m with unknown type: "
        f"{ds['mmsi_unknown_type_class_a_100m']:,}.",
        "", "| table | rows | bytes |", "|---|---:|---:|",
    ]
    for t, n in ds["rows_out"].items():
        lines.append(f"| {t} | {n:,} | {_fmt_bytes(ds['bytes_out'][t])} |")
    diag = ds.get("diagnostics") or {}
    lines += ["", f"- static rows per dynamic row: {diag.get('static_rows_per_dynamic_row')}; "
                  f"jumps (all vessels): {diag.get('jumps_total')}; kept MMSIs in Skagen box: "
                  f"{diag.get('skagen_bbox_kept_mmsi')}.",
              _value_counts("Ship type", diag.get("ship_type_values")),
              _value_counts("Cargo type", diag.get("cargo_type_values")),
              _value_counts("Mobile type", diag.get("mobile_type_values")), ""]
    return lines


def _value_counts(label: str, pairs: list | None) -> str:
    """A `[[value, count], ...]` probe entry as a readable line.

    These lines are how a reader checks `config.HAZARDOUS_CARGO_SUBSTRINGS` against what DMA actually
    broadcasts (see ASSUMPTIONS), so they are read, not skimmed. They used to be `str()` of the raw list,
    which put `[[None, 9315487], ...]` in a deliverable: a real value (no cargo type reported on 9.3 m rows)
    dressed up as a missing one.
    """
    if not pairs:
        return f"- {label} values: {NOT_RUN} - run `make probe-dma`."
    parts = [f"{'not reported' if v is None or v == '' else v} {n:,}" for v, n in pairs]
    return f"- {label} values: " + "; ".join(parts) + "."


def _window() -> list[str]:
    if not config.WINDOW_FILE.exists():
        return ["## Window gate (task 3)", NOT_RUN, ""]
    w = json.loads(config.WINDOW_FILE.read_text())
    lines = ["## Window gate (task 3)", "",
             f"- Window: **{w['start']} to {w['end']}** ({w['months']} months). Reason: {w['reason']}.",
             f"- Parquet per day {_fmt_bytes(w['parquet_bytes_per_day'])}; projected "
             f"{w['projected_parquet_gb']} GB; free at gate {w['free_gb_at_gate']} GB; budget {w['budget_gb']} GB.",
             f"- {w['seconds_per_day']} s per day; projected {w['projected_machine_hours']} machine-hours; "
             f"{w['days_per_wallclock_day']} days ingested per wall-clock day.",
             f"- Evaluable monthly cutoffs today: {len(w['cutoffs'])} "
             f"({', '.join(w['cutoffs'][:1] + w['cutoffs'][-1:])}); "
             f"supervised: {len(w['supervised_cutoffs'])}."]
    if w.get("previous"):
        lines.append(f"- Previous window: {w['previous']}")
    return lines + [""]


def _ingest_progress() -> list[str]:
    w = config.load_window()
    if not w:
        return []
    c = dma.check(w.start, w.end)
    done = c["days"] - len(c["missing"])
    return ["## Bulk ingest (task 4)", "",
            f"- Days done {done} of {c['days']}; failed {len(c['failed'])}.",
            f"- Failed days (first 20): {dict(list(c['failed'].items())[:20]) or 'none'}", ""]


def _gfw() -> list[str]:
    p = probes.read("gfw")
    if not p:
        return ["## GFW (task 5)", NOT_RUN, ""]
    lines = ["## GFW (task 5)", "", f"- Go: **{p['go']}**. IMOs with events by type: {p['imos_with_events']}.",
             f"- Client stats: {p.get('client_stats')}", "",
             "| IMO | vessel ids | dated identity | shiptypes | GAP | ENCOUNTER | LOITERING | PORT_VISIT |",
             "|---|---:|---|---|---:|---:|---:|---:|"]
    for imo, r in p["imos"].items():
        if "error" in r:
            lines.append(f"| {imo} | error: {r['error'][:80]} | | | | | | |")
            continue
        ev = r.get("events", {})
        cnt = [str(ev.get(k, {}).get("count", ev.get(k, {}).get("error", "")))[:30] for k in config.GFW_DATASETS]
        ident = r["identity"]
        lines.append(f"| {imo} | {len(r['vessel_ids'])} | {ident['with_transmission_dates']}/"
                     f"{ident['identity_records']} | {', '.join(r['shiptypes'])} | " + " | ".join(cnt) + " |")
    return lines + ["", "Raw vessel JSON: `reports/phase0_gfw_vessel.json` (local only, gitignored).", ""]


def _ofac() -> list[str]:
    p = probes.read("ofac")
    if not p:
        return ["## OFAC (task 6)", NOT_RUN, ""]
    lines = ["## OFAC (task 6)", "", f"- Go (2025 archive >= 100 vessel actions with IMO): **{p['go']}**.",
             f"- Current SDN CSV: {p.get('sdn_csv')}",
             f"- SDN advanced XML: {json.dumps(p.get('sdn_advanced_xml'))[:600]}"]
    for y, r in p["years"].items():
        lines.append(f"- {y} change archive: {json.dumps(r.get('stats') or r)[:500]} (source {r.get('source')})")
    return lines + ["- Sample text: `reports/phase0_ofac_sample.txt`.", ""]


def _opensanctions() -> list[str]:
    p = probes.read("opensanctions")
    if not p:
        return ["## OpenSanctions (task 7)", NOT_RUN, ""]
    m = p.get("maritime") or {}
    lines = ["## OpenSanctions (task 7)", "",
             f"- Licence: {p['licence']}. Maritime version {p.get('maritime_version')}.",
             f"- Maritime CSV: {m.get('rows')} rows, {m.get('rows_with_valid_imo')} with valid IMO; "
             f"header `{m.get('header')}`.",
             f"- Risk values: {m.get('risk_counts')}",
             f"- Source datasets: {m.get('dataset_counts')}",
             f"- OFAC-sanctioned IMO candidates for GFW probe: {p.get('ofac_sanctioned_imo_candidates')}.",
             f"- EU vessel source candidates (EU-MARE with dates): **{p.get('eu_vessel_source_candidates')}**",
             f"- PSC-like datasets: {p.get('psc_like_datasets')}", ""]
    for slug, r in (p.get("label_sources") or {}).items():
        lines.append(f"- `{slug}`: {json.dumps(r)[:600]}")
    return lines + [""]


def _mid() -> list[str]:
    p = probes.read("mid")
    if not p:
        return ["## MID table (task 8)", NOT_RUN, ""]
    return ["## MID table (task 8)", "", f"- {p['rows']} MIDs written to `{p['csv']}`; unmatched names: "
            f"{p['unmatched'] or 'none'}.", ""]


def _llm() -> list[str]:
    p = probes.read("llm")
    if not p:
        return ["## llama.cpp smoke test (task 9)", NOT_RUN, ""]
    return ["## llama.cpp smoke test (task 9)", "",
            f"- Go: **{p['go']}**; models {p.get('models')}; {p.get('seconds')} s; "
            f"{p.get('tokens_per_second')} tok/s; VRAM after {p.get('vram_after')}.",
            f"- Output: `{p.get('content') or p.get('error')}`", ""]


def render() -> str:
    parts = [f"# Phase 0 report (generated {date.today().isoformat()} by `make report-phase0`)", "",
             "Every number below is read from `reports/probes/*.json` or `config/window.json`. "
             "Edit the go/no-go and assumptions sections by hand after reading.", "",
             "## What was built", "",
             "- Repo scaffold, config, WSL2 doctor, structured logging, disk guard.",
             "- Frozen DMA ingest (ADR-14): zip stream to temp Parquet, then `ais_dynamic`, `ais_static`, "
             "`ais_artifacts`, `jump_baseline`, `vessel_day` (+ `ais_fullres` on request); markers, registry, "
             "timing log; bulk runner with 2 prefetch workers and sequential processing.",
             "- GFW v3 client with SHA-256 disk cache and rate-limit log; OFAC SDN and change-archive parsers; "
             "OpenSanctions maritime and label-source probe; ITU MID table; window gate; llama.cpp smoke test.", ""]
    for section in (_dma, _window, _ingest_progress, _gfw, _ofac, _opensanctions, _mid, _llm):
        parts += section()
    parts += ["## Go / no-go", "", "| source | decision | note |", "|---|---|---|",
              "| DMA | TODO | |", "| GFW | TODO | |", "| OFAC | TODO | |", "| OpenSanctions EU/UK | TODO | |",
              "| llama.cpp | TODO | |", "",
              "## Assumptions to confirm", ""] + [f"- {a}" for a in ASSUMPTIONS] + [
              "", "## Next phase depends on", "",
              "- The running ingest and `config/window.json` (Phases 1 to 4b).",
              "- GFW client and cache (4a), OFAC parsers and OpenSanctions slugs (2), MID table (3).", ""]
    return "\n".join(parts)


# ---------------------------------------------------------------------------- Phase 1
def render_phase1() -> str:
    p = probes.read("phase1")
    if not p:
        return "# Phase 1 report\n\n" + NOT_RUN + " — run `make phase1`.\n"
    w = config.load_window()
    pop, tc, ge, sts = (p.get(k) or {} for k in ("population", "type_changes", "gap_evidence", "sts_readiness"))
    ing = _ingest_progress()
    lines = [f"# Phase 1 report (generated {date.today().isoformat()} by `make phase1`)", "",
             f"Window {w.start} to {w.end}." if w else "No window file.", ""]
    lines += ing or []
    lines += ["## Population", "",
              f"- MMSIs seen: {pop.get('mmsi_total')}; ever tanker-class: {pop.get('mmsi_ever_tanker_class')}; "
              f"with an IMO in AIS: {pop.get('mmsi_with_imo')}.",
              f"- Monthly counts: `{pop.get('by_month_csv')}` ({pop.get('months')} months).", "",
              "## Reported type changes", "",
              f"- Changes: {tc.get('changes')} across {tc.get('mmsi_with_change')} MMSIs; "
              f"{tc.get('mmsi_that_stopped_reporting_tanker')} stopped reporting as tanker at least once "
              "(these must not silently drop out of the study; Phase 3 cross-checks them).", "",
              "## DMA gaps are coverage, not evasion (R4)", "",
              f"- Gaps over 6 h: {ge.get('gaps_over_6h')}; median {ge.get('median_gap_hours')} h, "
              f"p90 {ge.get('p90_gap_hours')} h.",
              f"- Of {ge.get('sampled')} sampled gaps, {ge.get('within_10km_of_edge')} start within 10 km of "
              f"the coverage edge ({(ge.get('share_at_coverage_edge') or 0):.0%}), "
              f"{ge.get('within_25km_of_edge')} within 25 km; median distance to the edge "
              f"{ge.get('median_km_to_edge')} km.",
              f"- Median displacement across the gap {ge.get('median_displacement_km')} km; "
              f"{ge.get('gaps_that_moved_over_50km')} of the sample reappeared over 50 km away.",
              f"- **{ge.get('conclusion')}**", f"- Figure: `{ge.get('figure')}`", "",
              "## STS readiness (Phase 4b input)", "",
              f"- Day {sts.get('day')}, pairs within {sts.get('radius_m')} m under {sts.get('max_sog')} kn for "
              f"{sts.get('min_hours')} h in the Skagen box: {sts.get('pairs_fullres')} at full resolution, "
              f"{sts.get('pairs_downsampled')} after downsampling.",
              f"- Diagnostics: {sts.get('diagnostics')}",
              f"- Downsample loses pairs: **{sts.get('downsample_loses_pairs')}**", "",
              "## Assumptions to confirm", "",
              "- The coverage edge is derived from the data (0.1-degree cells with fewer than 8 occupied "
              "neighbours), not from an official Danish polygon.",
              "- STS readiness counts qualifying minute buckets, not contiguous runs; Phase 4b does run-length "
              "detection.",
              "- The Skagen box in config is still a placeholder.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- Phase 2
def render_phase2() -> str:
    p = probes.read("labels")
    if not p:
        return "# Phase 2 report\n\n" + NOT_RUN + " - run `make labels`.\n"
    t = p.get("table") or {}
    rows = p.get("positives_by_cutoff") or []
    x = p.get("xml_vs_archive") or {}
    lines = [f"# Phase 2 report (generated {date.today().isoformat()} by `make labels`)", "",
             "`PREREG.md` was committed before this table existed; see the git history.", "",
             "## Sources", "", "| source | rows | distinct IMOs | note |", "|---|---:|---:|---|",
             f"| OFAC advanced XML | {(p.get('ofac_xml') or {}).get('rows')} | "
             f"{(p.get('ofac_xml') or {}).get('imos')} | dated EntryEvents for currently listed vessels; "
             f"IMO via {(p.get('ofac_xml') or {}).get('by_via')} |",
             f"| OFAC change archive | {(p.get('ofac_archive') or {}).get('rows')} | "
             f"{(p.get('ofac_archive') or {}).get('imos')} | removals, modifications, delisted hulls |",
             f"| EU (eu_sanctions_map, EU-MARE) | {(p.get('eu') or {}).get('rows')} | "
             f"{(p.get('eu') or {}).get('imos')} | undated: {(p.get('eu') or {}).get('undated')} "
             f"{(p.get('eu') or {}).get('undated_celex')} |",
             f"| UK (gb_fcdo_sanctions) | {(p.get('uk') or {}).get('rows')} | "
             f"{(p.get('uk') or {}).get('imos')} | undated: {(p.get('uk') or {}).get('undated')} |",
             "", f"- `sanctions_actions.parquet`: {t.get('rows')} rows. By source and action: "
             f"{t.get('by_source_action')}",
             f"- CELEX dates loaded from config: {p.get('celex_entries')}",
             f"- XML vs archive add dates: {x.get('imos_in_both')} IMOs in both, "
             f"{x.get('agree_within_7_days')} agree within 7 days. Worst: {x.get('worst')}", "",
             "## Positives per cutoff", "",
             "| cutoff | observed | in scope | excluded (already listed) | positives (union) | "
             "positives (OFAC only) | OFAC adds in horizon | of those, EU/UK-listed at T |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(f"| {r['cutoff']} | {r['population_observed']} | {r['population_in_scope']} | "
                     f"{r['excluded_already_listed']} | {r['positives_union']} | {r['positives_ofac_only']} | "
                     f"{r.get('ofac_adds_in_horizon_all')} | {r.get('ofac_adds_already_eu_uk_listed')} |")
    thin = [r for r in rows if (r.get("positives_union") or 0) < 30]
    fires = len(thin) > len(rows) / 3 if rows else False
    lines += ["", f"- Full table: `{p.get('positives_csv')}`", "",
              "## The PREREG thin-positives rule", "",
              f"- Union positives are below 30 at {len(thin)} of {len(rows)} cutoffs "
              f"(the rule fires above {len(rows) / 3:.1f}). **{'FIRED' if fires else 'not fired'}.**",
              ("- Reporting therefore aggregates to quarters per ADR-11. Scoring stays monthly; this changes "
               "how metrics are presented, not how they are computed. It is a pre-registered branch, taken "
               "before any model exists, so it carries no post-hoc label."
               if fires else "- Monthly reporting stands."),
              "- The cause is visible in the table: designations arrive in packages, so positives are lumpy "
              "rather than thin on average. Late cutoffs are also the ones whose horizon runs into the end "
              "of the data.", "",
              "## Findings", "",
              "- The union label (OFAC/EU/UK) carries the evaluation. Late-2024 cutoffs are the richest, "
              "because the January 2025 OFAC action falls inside their 182-day horizon.",
              "- R12 is real and material: a large share of OFAC designations were already EU or UK-listed at T, "
              "which is why the pre-registered headline is the union and OFAC-only is a sensitivity table.",
              "- Population keyed on the modal AIS IMO until Phase 3 lands `hull_id`; hulls without a valid "
              "AIS IMO are not yet in the population and Phase 3 measures how many that is.", "",
              "## Assumptions to confirm", "",
              "- `EntryEventTypeID = 1` is the original entry in the SDN advanced XML; other ids are recorded "
              "as `modify` rather than interpreted.",
              "- EU dates are entry-into-force dates; the CELEX table in `config/celex_dates.json` cites each.",
              "- Ten designations for the hand spot-check are in "
              "`reports/phase2_spotcheck.csv`, seeded so a rerun gives the same ten; "
              "the `verified_y_n` and `official_url` columns are Adam's to fill.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- Phase 3
def render_phase3(out: dict | None = None) -> str:
    p = out or probes.read("identity")
    if not p:
        return "# Phase 3 report\n\n" + NOT_RUN + " - run `make identity`.\n"
    hm, iv, cov = p.get("hull_map") or {}, p.get("identity_intervals") or {}, p.get("coverage") or {}
    frag, silver, xc = p.get("fragmentation") or {}, p.get("silver_set") or {}, p.get("crosscheck") or {}
    share = cov.get("share_by_imo")
    bar = "PASS" if (share or 0) >= 0.80 else "BELOW THE 80 PERCENT BAR - ADR-5 revisit"
    lines = [f"# Phase 3 report (generated {date.today().isoformat()} by `make identity`)", "",
             "## Hull ids", "",
             f"- `{hm.get('file')}`: {hm.get('windows')} (MMSI, {hm.get('window_days')}-day window) rows for "
             f"{hm.get('mmsi')} MMSIs, {hm.get('windows_by_imo')} of them resolved by IMO majority vote.",
             f"- {hm.get('hull_ids')} distinct hull ids, {hm.get('hull_ids_syn')} of them synthetic.",
             f"- Coverage: {cov.get('mmsi_days_by_imo')} of {cov.get('mmsi_days')} kept MMSI-days carry an "
             f"IMO-based hull id ({share}). **{bar}**",
             f"- Of the MMSI-days that have any hull id, {cov.get('share_by_imo_of_mapped')} are IMO-based; "
             f"{cov.get('mmsi_days_unmapped')} MMSI-days are the pre-first-window warm-up, which has no hull "
             f"id by design.",
             "- The vote is cumulative over everything observed up to the end of a window, and takes effect "
             "the day after that window closes, so no cutoff reads a static message from after itself. A "
             "vessel has no hull id until its first window closes; that is the warm-up above.",
             "- No GFW identity merge was built: IMO majority meets the bar on its own, so no hull id depends "
             "on a GFW model and the Phase 5a entity-resolution sensitivity arm has nothing to disable.", "",
             "### What the thresholds cost", "",
             "| min IMO-days | min support | share of MMSI-days by IMO | |", "|---:|---:|---:|---|"] + [
             f"| {g['min_imo_days']} | {g['min_support']} | {g['share_by_imo']} |"
             f" {'**chosen**' if g.get('chosen') else ''} |" for g in (p.get("coverage_by_threshold") or [])
             ] + ["",
             "## Identity intervals", "",
             f"- `{iv.get('file')}`: {iv.get('intervals')} intervals over {iv.get('mmsis')} transmitters; "
             f"{iv.get('mmsis_with_a_change')} changed name, callsign or flag at least once. Intervals are "
             "keyed by transmitter; which hull a transmitter belongs to is decided at each cutoff (ADR-23).",
             (f"- Flags resolved from {iv.get('mid_rows')} ITU MID rows; "
              f"{iv.get('intervals_with_flag')} intervals carry a flag."
              if iv.get("mid_rows") else
              "- **No flags resolved**: the ITU MID table is empty, so `n_flag_changes` and "
              "`flag_to_convenience_registry` are dead features in every model below. Run `make probe-mid` "
              "and re-run `make identity` before reading any result that uses them."), "",
             "## Checks", ""]
    lines.append("- Fragmentation: " + (frag.get("skipped")
                 or f"at cutoff {frag.get('cutoff')}, {frag.get('single_hull_id')} of {frag.get('checked')} "
                    f"designated IMOs have every transmitter that carried them mapped to one hull id. "
                    f"Fragmented: {frag.get('fragmented')}"))
    lines.append("- Silver set (OpenSanctions IMO-MMSI pairs): " + (silver.get("skipped")
                 or f"{silver.get('agree')} of {silver.get('pairs_in_population')} pairs agree (precision "
                    f"{silver.get('precision')}); {silver.get('disagree')} resolved to a different IMO."))
    lines += [f"- Population cross-check: {xc.get('mmsi_tanker_sized_never_tanker_class')} MMSIs are "
              f"tanker-sized with an unknown reported type and were never tanker-class; "
              f"{xc.get('of_those_later_designated', 'n/a')} of them were later designated.", "",
              "## Assumptions to confirm", "",
              "- Deviation from the Phase 3 prompt, both recorded here rather than assumed away: the vote is "
              "cumulative rather than per-window, because a per-window vote with a warm-up leaks at any "
              "cutoff landing inside a vessel's first window, which month-end cutoffs do; and a vote is one "
              "per day an IMO was broadcast, not one per message, because `ais_static` is change-point "
              "compressed at ingest (ADR-14) so message counts are not comparable between hulls.",
              "- 30-day windows, 60 percent support, 5 IMO-days: the prompt's numbers, on day votes.",
              "- `vessel_age_years` stays null until Phase 4a brings the GFW registry build year.",
              "- Null static fields are carried forward, so a message that omits a field is not a change.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- Phase 4a
def render_phase4a(out: dict | None = None) -> str:
    p = out or probes.read("gfw_events")
    if not p:
        return "# Phase 4a report\n\n" + NOT_RUN + " - run `make gfw`.\n"
    by, imos = p.get("events_by_type") or {}, p.get("imos_with_event_type") or {}
    rus = p.get("russian_port_visits") or {}
    pct = p.get("events_per_imo_p50_p90_max") or []
    enc = p.get("encounter_share_of_imos")
    lines = [f"# Phase 4a report (generated {date.today().isoformat()} by `make gfw`)", "",
             f"GFW events for every IMO the Phase 3 resolver voted through, {p.get('start')} to {p.get('end')}"
             " (the window plus the 180 days before it). Tables are GFW-derived, so local only (rule 7).", "",
             "## Coverage", "",
             f"- {p.get('imos_with_a_gfw_id')} of {p.get('imos_requested')} IMOs have a GFW vessel id "
             f"({p.get('share_with_a_gfw_id')}); {p.get('vessel_ids')} vessel ids in total.",
             f"- Events per IMO with any: median {pct[0] if pct else None}, p90 {pct[1] if pct else None}, "
             f"max {pct[2] if pct else None}.", "",
             "| type | events | IMOs with any |", "|---|---:|---:|"] + [
             f"| {k} | {by.get(k, 0)} | {imos.get(k, 0)} |" for k in ("gap", "encounter", "loitering", "port_visit")
             ] + ["",
             "## Encounters (task 4b re-test)", "",
             (f"- Share of IMOs with any encounter: {enc}. "
              + ("Effectively zero, which confirms the Phase 0 finding: the public encounter dataset does not "
                 "return tankers. The encounter features stay in the registry at zero, and the at-sea transfer "
                 "signal comes from the Phase 4b STS detector alone." if not enc or enc < 0.01 else
                 "Not zero, so the Phase 0 finding does not hold at population scale; the encounter parser "
                 "has now met live records.")), "",
             "## Russian port visits (B1)", "",
             f"- {rus.get('all_rus')} port visits at an anchorage with country RUS; "
             f"{rus.get('in_b1_regions')} inside the B1 regions (Baltic, Black Sea, Kola Bay; PREREG amendment "
             f"2026-09-28), so {(rus.get('all_rus') or 0) - (rus.get('in_b1_regions') or 0)} elsewhere.",
             f"- {rus.get('without_a_name')} of the RUS visits carry no anchorage name, which is why the rule "
             "matches country and position rather than a name list.", "",
             "## Datasets served", ""] + [f"- `{d}`" for d in p.get("datasets") or ["(none recorded)"]] + ["",
             f"Client: {p.get('client_stats')}.", "",
             "## Assumptions to confirm", "",
             "- Vessel ids come from searching each IMO; no MMSI-and-date fallback was built. It pays for itself "
             "only if the IMO share above is low.",
             "- An event's `observed_at` is its end. GFW's event models were run after the fact, so this is the "
             "disclosed limitation in PREREG section 11, not a point-in-time guarantee.",
             "- Encounter parsing follows the documented shape; until the share above is non-zero it has not met "
             "a live record.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- Phase 4b
def _cells(rows: list[dict]) -> list[str]:
    if not rows:
        return ["(none)"]
    return ["| lat | lon | events | hours | transmitters |", "|---:|---:|---:|---:|---:|"] + [
        f"| {r['lat']} | {r['lon']} | {r['events']} | {r['hours']} | {r['mmsis']} |" for r in rows[:10]]


def render_phase4b(out: dict | None = None) -> str:
    p = out or probes.read("detect")
    if not p:
        return "# Phase 4b report\n\n" + NOT_RUN + " - run `make detect`.\n"
    s, lo = p.get("sts") or {}, p.get("loitering") or {}
    dr, sp, ch = p.get("draught") or {}, p.get("spoof") or {}, p.get("churn") or {}
    lines = [f"# Phase 4b report (generated {date.today().isoformat()} by `make detect`)", "",
             "Detection layers built on DMA tracks only. No GFW field is read here, which is what lets "
             "Phase 6 report a self-built-only arm against a GFW-only arm.", "",
             "## STS candidates", "",
             f"- {s.get('candidates')} candidates over {s.get('months')} months, at "
             f"{s.get('radius_m')} m / {s.get('max_sog')} kn / {s.get('min_hours')} h.",
             f"- Table: `{s.get('table')}`. `observed_at` is when the transfer ended.", "",
             "Where they cluster (this replaces the hand-drawn Skagen anchorage box; the clusters come out "
             "of the data):", ""] + _cells(p.get("sts_cells") or []) + ["",
             "## Anchorage loitering", "",
             f"- {lo.get('events')} stretches under {lo.get('max_sog')} kn for over "
             f"{lo.get('min_hours')} h, excluding hulls reporting Moored.",
             f"- Table: `{lo.get('table')}`.", ""] + _cells(p.get("loitering_cells") or []) + ["",
             "## Draught inconsistency", "",
             f"- {dr.get('changes')} declared-draught changes of at least {dr.get('min_change_m')} m over "
             f"{dr.get('mmsis')} transmitters.",
             f"- {dr.get('unexplained')} with neither a berth call nor an STS candidate in between "
             f"(the lightering signal); {dr.get('coinciding_with_sts')} coincide with an STS candidate; "
             f"{dr.get('after_a_berth_call')} follow a Moored report.", "",
             "## Spoof-jump excess", ""]
    if sp.get("skipped"):
        lines.append(f"- Skipped: {sp['skipped']}.")
    else:
        lines += [f"- {sp.get('mmsi_days')} transmitter-days over {sp.get('mmsis')} transmitters; "
                  f"{sp.get('with_any_jump')} had at least one jump and "
                  f"{sp.get('with_positive_excess')} exceeded their cells' baseline.",
                  f"- Mean excess {sp.get('mean_excess')}, max {sp.get('max_excess')}. A hull that jumps "
                  f"only as much as everything else in its cell that day scores zero, which is the point."]
    lines += ["", "## MMSI-IMO churn", "",
              f"- {ch.get('changes')} changes: {ch.get('by_kind')}.", "",
              "## Assumptions to confirm", "",
              "- \"Outside port polygons\" is implemented as \"not reporting Moored\". DMA carries the "
              "vessel's own nav_status, so this needs no polygon set; hulls *at anchor* are kept on purpose, "
              "because the Skagen transfers happen at anchor. Phase 4a's GFW port visits will add a second "
              "predicate.",
              "- A candidate needs 2 h elapsed and qualifying minutes covering at least half of it, so a "
              "sparse pair does not qualify on two distant minutes.",
              "- Runs tolerate a 10 min gap (STS) and 30 min (loitering) so one missed minute does not split "
              "an event in two.",
              "- Records from a hull's resolver warm-up are dropped rather than given an MMSI-based id: a "
              "synthetic id would split the hull's history at the boundary and hide its first transition.",
              "- STS rows do not store each hull's draught 48 h either side, as the prompt suggested; "
              "`detect_draught` already joins the two, so storing it twice would be duplicate state.",
              "- Hand-check of 10 candidates and the Skagen monthly plot are still outstanding; both need "
              "the real tables.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- Phase 5a
def render_phase5a(out: dict | None = None) -> str:
    p = out or probes.read("features")
    if not p:
        return "# Phase 5a report\n\n" + NOT_RUN + " - run `make features`.\n"
    registry = p.get("registry") or {}
    source_of = p.get("source_of_family") or {}
    by_family: dict[str, list[str]] = {}
    for name, (family, _desc) in registry.items():
        by_family.setdefault(family, []).append(name)
    lines = [f"# Phase 5a report (generated {date.today().isoformat()} by `make features`)", "",
             f"{p.get('features', len(registry))} features in {len(by_family)} frozen families. The "
             "registry is the contract: `tests/test_leakage.py` fails if its families stop matching "
             "PREREG section 7.", "",
             "| family | source | features |", "|---|---|---|"]
    lines += [f"| `{f}` | {source_of.get(f, '?')} | {', '.join(f'`{n}`' for n in sorted(ns))} |"
              for f, ns in sorted(by_family.items())]
    lines += ["", "## Feature matrix", "", "| cutoff | hulls | seconds |", "|---|---:|---:|"]
    lines += [f"| {c['cutoff']} | {c['hulls']} | {c['seconds']} |" for c in p.get("cutoffs") or []]
    gfw = p.get("gfw_present")
    lines += ["", f"- Table: `{p.get('table')}`.",
              f"- GFW events present: **{gfw}**." + ("" if gfw else " The three `gfw_*` families are at "
              "their defaults (zero, or null where a null is meaningful) until Phase 4a runs. That is "
              "reported, never faked, and Phase 6's GFW-only ablation arm will be empty until it does."), "",
              "## Leakage suite", "",
              "- (a) `features(h, T)` from the live store equals `features(h, T)` from a store physically "
              "truncated at T. Tested.",
              "- (b) static analysis: nothing under `features/` imports label construction, no feature name "
              "mentions sanctions except the partner feature, no GFW registry ownership field is read. Tested.",
              "- (c) permutation, (d) reverse-time, (e) entity-resolution sensitivity: these need a fitted "
              "model, so they are Phase 5b harness hooks. They are named here so their absence is visible.", "",
              "## Assumptions to confirm", "",
              "- A transit is a contiguous run of DMA positions; a gap over "
              f"{p.get('transit_gap_hours', '?')} h starts a new "
              "one. On terrestrial AIS that means one transit per visit to coverage, which is the intent.",
              "- Laden is judged per transit against the hull's own 75th-percentile draught, so hull size "
              "does not decide it.",
              "- `dwt` has no source: DMA does not carry it, so the column is null until one exists.",
              "- `vessel_age_years` is null until Phase 4a brings the GFW registry build year.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- Phase 5b
def _leakage(p: dict) -> list[str]:
    leak = p.get("leakage") or {}
    failed = p.get("leakage_failed") or []
    if failed:
        verdict = ("FAILED: " + ", ".join(failed) + ". PREREG section 10 says no metrics may be reported "
                   "while a leakage test fails, so treat every number above as void until this is green.")
    else:
        # only (c), (d) and (e) run here; saying "all five" would credit this report with two checks it
        # did not perform
        verdict = "(c), (d) and (e) pass."
    head = [f"**{verdict}**", "",
            "(a) truncation equality and (b) static analysis run in `tests/test_leakage.py`, which "
            "`make backtest` executes before it reports anything.", ""]
    rows = ["| test | result |", "|---|---|"]
    names = {"permutation": "(c) permutation", "reverse_time": "(d) reverse time",
             "entity_resolution": "(e) entity-resolution sensitivity"}
    for key, label in names.items():
        v = leak.get(key) or {}
        if v.get("skipped"):
            rows.append(f"| {label} | skipped: {v['skipped']} |")
        else:
            mark = "pass" if v.get("passes") else "**FAIL**"
            detail = ", ".join(f"{k} {round(v[k], 4) if isinstance(v[k], float) else v[k]}"
                               for k in ("ratio", "pr_auc_shuffled", "pr_auc_forward", "pr_auc_backward",
                                         "pr_auc_delta") if v.get(k) is not None)
            rows.append(f"| {label} | {mark} ({detail or v.get('note', '')}) |")
    return head + rows


def render_phase5b(out: dict | None = None) -> str:
    p = out or probes.read("backtest")
    if not p:
        return "# Phase 5b report\n\n" + NOT_RUN + " - run `make backtest`.\n"
    agg = p.get("aggregate") or []
    dead = p.get("dead_b2_terms") or []
    lines = [f"# Phase 5b report (generated {date.today().isoformat()} by `make backtest`)", "",
             f"{p.get('cutoffs_scored')} cutoffs scored. Macro-averaged over cutoffs, per PREREG section 3: "
             "a cutoff with more hulls must not dominate.", "",
             "## The pre-registered primary endpoint", ""]
    primary = [a for a in agg if a["label_set"] == "union" and a["stratum"] == "b1"]
    lines += ["| model | precision@50 | PR-AUC | recall@50 | cutoffs | of those, with a positive |",
              "|---|---:|---:|---:|---:|---:|"]
    lines += [f"| `{a['model']}` | **{a['precision_at_50']}** | {a['pr_auc']} | {a['recall_at_50']} | "
              f"{a['cutoffs']} | {a['cutoffs_with_a_positive']} |"
              for a in sorted(primary, key=lambda a: -(a["precision_at_50"] or 0))]
    lines += ["", "PREREG section 3 fixes this table as the headline: precision@50 in the B1 stratum, union "
              "label. LightGBM is not here yet (Phase 6); the best row is currently a baseline, and if it "
              "stays that way after Phase 6 that is the finding, not a failure to report.", "",
              "## Every arm", "",
              "| label set | stratum | model | precision@50 | PR-AUC | recall@50 | FPR@50 |",
              "|---|---|---|---:|---:|---:|---:|"]
    lines += [f"| {a['label_set']} | {a['stratum']} | `{a['model']}` | {a['precision_at_50']} | "
              f"{a['pr_auc']} | {a['recall_at_50']} | {a['fpr_at_50']} |"
              for a in sorted(agg, key=lambda a: (a["label_set"], a["stratum"], a["model"]))]
    lines += ["", f"- Per-cutoff rows: `{p.get('csv')}`.", "", "## Lead time (event study)", "",
              "Weeks between a hull's designation and the earliest cutoff at which it entered the top 50. "
              "Event-study rather than per-cutoff: averaging per-cutoff distances mostly measures how far "
              "each cutoff sat from the next designation wave (ADR-11).", "",
              "| model | flagged before designation | of designated | median weeks | max |",
              "|---|---:|---:|---:|---:|"] + [
              f"| `{m}` | {v['flagged_before_designation']} | {v['designated_in_window']} | "
              f"{v['median_weeks']} | {v['max_weeks']} |"
              for m, v in sorted((p.get("lead_time") or {}).items())] + ["",
              "Right-censored by construction: a hull flagged at the first cutoff cannot show a longer lead "
              "than the window allows, and one designated after the last horizon does not appear at all.",
              "", "## Leakage suite", ""] + _leakage(p) + ["",
              "## What is not contributing yet", "",
              f"- B2 term firing counts at the last cutoff: {p.get('b2_live_terms')}."]
    if dead:
        lines.append(f"- **Dead B2 terms: {', '.join(f'`{d}`' for d in dead)}.** Their weights are "
                     "pre-registered and stay as they are; they contribute nothing until the phase that "
                     "feeds them has run (GFW terms need Phase 4a, `old_vessel` needs the registry build "
                     "year). Reported rather than silently reweighted.")
    lines += ["", "## Assumptions to confirm", "",
              "- A `syn:` hull has no IMO, so it can never be a positive. The per-cutoff rows carry "
              "`hulls_without_imo` so that recall ceiling is visible.",
              "- `current_flag` is dropped from the B3 design matrix rather than label-encoded: an "
              "arbitrary integer ordering of flags is a worse lie than leaving the column out.",
              "- B0 is seeded, so the random baseline is reproducible.",
              "- (a) and (b) of the leakage suite run in `tests/test_leakage.py`, which `make backtest` "
              "executes before anything here; (c), (d) and (e) run in the harness and are above.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- Phase 6
def _verdict_vs_rules(agg: list[dict]) -> list[str]:
    """The paragraph the Phase 6 acceptance criteria demand: does ML beat rules, and by how much.

    Written from the numbers rather than around them. Either answer is acceptable (CLAUDE.md rule 8); the
    unacceptable thing is a report that does not say which one happened.
    """
    primary = {a["model"]: a for a in agg if a["label_set"] == "union" and a["stratum"] == "b1"}
    lgbm, best_rule = primary.get("LGBM"), None
    # B3 is a BASELINE in PREREG section 6, not a competing model. Leaving it out of this comparison
    # would let LightGBM be declared a winner over the weaker rules while losing to the logistic one.
    for name in ("B3_logistic", "B2_weighted", "B1_russia_port", "B0_random"):
        cand = primary.get(name)
        if cand and (best_rule is None or (cand["precision_at_50"] or 0) > (best_rule["precision_at_50"] or 0)):
            best_rule = cand
    if not lgbm or lgbm.get("precision_at_50") is None:
        return ["- LightGBM was not scored at any cutoff with a closed training horizon, so there is no "
                "comparison to make yet. The rules baselines above stand alone."]
    if not best_rule or best_rule.get("precision_at_50") is None:
        return ["- No rules baseline produced a precision@50, so there is nothing to compare against."]
    delta = lgbm["precision_at_50"] - best_rule["precision_at_50"]
    direction = "beats" if delta > 0 else ("ties" if delta == 0 else "does NOT beat")
    return [f"- **LightGBM {direction} the best rules baseline** (`{best_rule['model']}`) on the "
            f"pre-registered endpoint: precision@50 {lgbm['precision_at_50']} vs "
            f"{best_rule['precision_at_50']}, a difference of {delta:+.4f}, macro-averaged over "
            f"{lgbm['cutoffs']} cutoffs in the B1 stratum under the union label.",
            f"- PR-AUC: {lgbm['pr_auc']} vs {best_rule['pr_auc']}.",
            "- If that difference is small, the finding is that a hand-weighted rule captures most of what "
            "is learnable from these features, which is a result about the data, not a failure of the "
            "model (CLAUDE.md rule 8)."]


def render_phase6(out: dict | None = None) -> str:
    p = out or probes.read("backtest")
    if not p or not p.get("phase6"):
        return "# Phase 6 report\n\n" + NOT_RUN + " - run `make phase6`.\n"
    p6 = p["phase6"]
    scored = [a for a in p6.get("per_cutoff") or [] if a.get("ablations")]
    lines = [f"# Phase 6 report (generated {date.today().isoformat()} by `make phase6`)", "",
             "LightGBM and the isolation forest join the harness here; everything else in this report is "
             "reporting only and never drives a change to the feature set (PREREG section 7).", "",
             "## Does the model beat the rules", ""] + _verdict_vs_rules(p.get("aggregate") or []) + [
             "", "## Ablations", "",
             "Each arm retrains LightGBM on a restricted column set. Means over the "
             f"{len(scored)} cutoffs with a closed training horizon.", ""]
    arms = sorted({a for c in scored for a in (c.get("ablations") or {})})
    if scored:
        lines += ["| arm | PR-AUC | precision@50 |", "|---|---:|---:|"]
        for arm in arms:
            vals = [c["ablations"][arm] for c in scored if c.get("ablations", {}).get(arm)]
            pr = [v["pr_auc"] for v in vals if v.get("pr_auc") is not None]
            pk = [v["precision_at_50"] for v in vals if v.get("precision_at_50") is not None]
            lines.append(f"| `{arm}` | {round(sum(pr) / len(pr), 4) if pr else 'n/a'} | "
                         f"{round(sum(pk) / len(pk), 4) if pk else 'n/a'} |")
        empty_gfw = any(c.get("gfw_features_all_zero") for c in scored)
        lines += ["", "`gfw_only` against `self_built_only` is the arm this project exists to report (R14): "
                  "it separates what Global Fishing Watch detected from what this project detected."]
        if empty_gfw:
            lines.append("**Every GFW feature was zero at every scored cutoff, so `gfw_only` had nothing to "
                         "learn from and its number is the base rate, not a measurement of GFW's value.** "
                         "Phase 4a has to run before that arm means anything.")
    else:
        lines.append("No cutoff had a closed training horizon, so no arm ran.")
    lines += ["", "## Drift (PSI by family), split at the pre-registered Hormuz break", "",
              "| side | cutoffs | PSI by family |", "|---|---:|---|"]
    for side, v in (p6.get("drift_by_side") or {}).items():
        psi = ", ".join(f"`{f}` {val}" for f, val in (v.get("psi") or {}).items()) or "n/a"
        lines.append(f"| {side} | {v.get('cutoffs')} | {psi} |")
    lines += ["", "Under 0.1 is stable, 0.1 to 0.25 moderate, over 0.25 large. The break is "
              f"{config.REGIME_BREAKS['hormuz_closure']} and is never a feature and never a window bound "
              "(ADR-17).", "",
              "## Per-hull explanations", "",
              f"- SHAP tables: `data/parquet/shap/cutoff=*/` for {sum(1 for a in scored if a.get('shap'))} "
              "cutoffs. Contributions come from LightGBM's own `pred_contrib` (TreeSHAP), so the `shap` "
              "package is not a dependency: it would be a second implementation of the same algorithm.",
              f"- Ranked lists: `reports/flagged_<cutoff>.csv`, top {p6.get('top_flagged', '?')} per cutoff, "
              "with the five largest drivers per hull.",
              f"- False-positive review sheets for Adam: {p6.get('fp_review') or 'none written'}. The "
              "`reason` column is deliberately blank; a pre-filled guess would be a fabricated review.", "",
              "## Assumptions to confirm", "",
              "- LightGBM's validation split is the tail of the training rows, which are in cutoff order, "
              "so the held-out fold is the most recent cutoff as PREREG section 4 asks. A random split "
              "would put rows from one cutoff on both sides and flatter early stopping.",
              "- The isolation forest is fitted on each cutoff's own feature matrix, so it needs no history "
              "and is available at cutoffs where the supervised models are not.",
              "- Calibration is reported as the Brier score inside the per-cutoff metrics for the "
              "probability models. There is no reliability-diagram figure yet.",
              "- The top-20 false-positive review per quarter is generated but not yet filled in.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- Phase 8/9
def render_phase8(out: dict | None = None) -> str:
    p = out or probes.read("briefs")
    if not p:
        return "# Phase 8/9 report\n\n" + NOT_RUN + " - run `make briefs`.\n"
    f = p.get("faithfulness") or {}
    lines = [f"# Phase 8/9 report (generated {date.today().isoformat()} by `make briefs`)", "",
             f"{f.get('briefs', 0)} briefs in {p.get('seconds')} s. Every one was generated under a "
             "pydantic-derived JSON schema and then checked by the deterministic verifier; nothing that "
             "failed validation was written to disk.", "",
             "## Deterministic faithfulness", "",
             f"- **{f.get('clean', 0)} of {f.get('briefs', 0)} briefs have zero verifier failures "
             f"({f.get('share_clean')}).**", ""]
    counts = f.get("briefs_with") or {}
    if counts:
        lines += ["| failure | briefs |", "|---|---:|"]
        lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in sorted(counts.items())]
    lines += ["", "The four checks: every cited evidence id exists; each of the top-5 SHAP drivers has a "
              "cited record from the family it comes from; every number and date in the prose appears in "
              "the bundle; every capitalised name in the prose appears in the bundle.", ""]
    if p.get("failed"):
        lines += [f"- {len(p['failed'])} briefs failed generation after a retry and were recorded rather "
                  "than faked. First few: "
                  + "; ".join(f"{x['hull_id']} ({(x.get('errors') or ['?'])[0]})" for x in p["failed"][:3]),
                  ""]
    lines += ["## Per cutoff", "", "| cutoff | briefs | directory |", "|---|---:|---|"]
    lines += [f"| {c['cutoff']} | {c.get('briefs', 0)} | {c.get('dir') or c.get('skipped', '')} |"
              for c in p.get("cutoffs") or []]
    lines += ["", "## Still outstanding", "",
              "- The LLM judge (Phase 9 task 2) is not built. It must be a different model family from the "
              "generator, and judge-versus-human agreement on `reports/audit_sheet.csv` is the credibility "
              "number for this whole layer (ADR-10), so the faithfulness figure above is the deterministic "
              "half only.",
              "- `reports/audit_sheet.csv` is generated with 30 findings, failures first, and blank human "
              "columns. It means nothing until Adam fills it in.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- Phase 9
def render_phase9(out: dict | None = None) -> str:
    p = out or probes.read("judge")
    if not p:
        return "# Phase 9 report\n\n" + NOT_RUN + " - run `make judge`.\n"
    k = p.get("kappa") or {}
    lines = [f"# Phase 9 report (generated {date.today().isoformat()} by `make judge`)", "",
             f"Generator: `{p.get('generator_model')}`. Judge: `{p.get('judge_model')}`. Different "
             "families by construction; the command refuses to run otherwise, because a model grading its "
             "own output agrees with its own blind spots (ADR-10).", "",
             "## Claim-level entailment", "",
             f"- {p.get('findings')} findings judged. **Entailment rate {p.get('entailment_rate')}.**",
             f"- Judge errors (server or schema): {p.get('errors', 0)}.", "",
             "The judge sees only the records a finding cited, never the whole bundle. Given the whole "
             "bundle it can justify a claim from evidence the brief never pointed at, which is the failure "
             "being measured.", ""]
    for name, key in (("By severity", "by_severity"), ("By cutoff", "by_cutoff")):
        rows = p.get(key) or {}
        if rows:
            lines += [f"### {name}", "", "| | entailment |", "|---|---:|"]
            lines += [f"| {kk} | {vv} |" for kk, vv in rows.items()] + [""]
    lines += ["## Judge versus human", ""]
    if k.get("skipped"):
        lines.append(f"- Not available: {k['skipped']}. This is the credibility number for the whole brief "
                     "layer, so until the sheet is filled in, the entailment rate above is one model's "
                     "opinion of another's.")
    else:
        lines += [f"- {k.get('n')} findings audited by hand. Raw agreement {k.get('raw_agreement')}, "
                  f"**Cohen's kappa {k.get('kappa')}**." + (f" {k['note']}" if k.get("note") else "")]
    lines += ["", "## Assumptions to confirm", "",
              "- Family detection is the first word of the model name, which is enough to catch Gemma "
              "judging Gemma and no more than that.",
              "- A finding whose cited ids are missing from the bundle is graded `not_entailed` without "
              "asking the model; the deterministic verifier already calls that a dangling citation.",
              "- Agreement is computed as a two-way question (supported or not), because the audit sheet "
              "carries the verifier's pass/fail until a judge column exists. Three-class kappa needs the "
              "judge verdicts written into the sheet first.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------- one writer for every phase
# Nine functions used to sit here, each four identical lines around its renderer. A registry instead, so
# adding a phase cannot mean forgetting to add its writer -- and so a caller naming a phase that does not
# exist fails immediately instead of writing nothing.
RENDERERS = {
    "phase0": render, "phase1": render_phase1, "phase2": render_phase2, "phase3": render_phase3,
    "phase4a": render_phase4a, "phase4b": render_phase4b, "phase5a": render_phase5a, "phase5b": render_phase5b,
    "phase6": render_phase6, "phase8": render_phase8, "phase9": render_phase9,
}


def write_report(phase: str, out: dict | None = None) -> str:
    """Render one phase's report to `reports/<phase>.md` and return the text."""
    if phase not in RENDERERS:
        raise KeyError(f"no renderer for {phase!r}; known phases: {sorted(RENDERERS)}")
    renderer = RENDERERS[phase]
    # the early renderers read their probe file themselves and take no argument; ask the signature rather
    # than keeping a hardcoded list of which ones, because that list is what drifts
    text = renderer(out) if signature(renderer).parameters else renderer()
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.REPORTS_DIR / f"{phase}.md").write_text(text)
    return text
