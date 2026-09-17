"""Render reports/phase0.md from reports/probes/*.json and config/window.json.

Numbers come only from those files (CLAUDE.md rule 4); a missing probe renders as NOT RUN.
"""

from __future__ import annotations

import json
from datetime import date

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
              f"- Ship type values: {diag.get('ship_type_values')}",
              f"- Cargo type values: {diag.get('cargo_type_values')}",
              f"- Mobile type values: {diag.get('mobile_type_values')}", ""]
    return lines


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


def write() -> str:
    text = render()
    (config.REPORTS_DIR / "phase0.md").write_text(text)
    return text
