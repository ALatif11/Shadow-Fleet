"""Phase 5a: `features(hull_id, T)` over records with `observed_at <= T`, and the frozen registry.

One query per family, merged in Python on hull_id. Not one giant join: the families have different grains
(intervals, minutes, hull-days, events) and a single query would need five levels of subquery to say the
same thing. Missing rows mean "nothing observed", which is a real zero, not a null, except where a null is
meaningful (`days_since_...`, `current_flag`, `vessel_age_years`).
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from shadowfleet import config
from shadowfleet.detect import churn, draught, loitering, spoof, sts
from shadowfleet.features import identity as fid
from shadowfleet.ingest import gfw
from shadowfleet.ingest.dma import connect
from shadowfleet.labels import labels as lab
from shadowfleet.resolve.identity import INTERVALS, at_cutoff, hull_at
from shadowfleet.util.store import glob_table, has_table, rel_path

TRANSIT_GAP_HOURS = 12  # a hull out of DMA coverage this long and back is a new transit
LADEN_PERCENTILE = 0.75  # phase-prompts 5a: laden = draught above the hull's own 75th percentile

# The frozen registry: name -> (family, description). PREREG section 7 fixes the families; a test asserts
# this registry's families equal that list. Anything added after Phase 5b results is tagged post_hoc.
FEATURES: dict[str, tuple[str, str]] = {
    **{k: ("identity", v) for k, v in fid.FEATURES.items() if k != "vessel_age_years"},
    "n_transits": ("ais", "Separate appearances in DMA coverage in the feature window"),
    "n_laden_transits": ("ais", "Transits whose max draught is above the hull's 75th percentile"),
    "n_ballast_transits": ("ais", "Transits that are not laden"),
    "share_russian_destination": ("ais", "Share of static messages naming a Russian port in `destination`"),
    "mean_transit_speed_kn": ("ais", "Mean SOG over moving positions in the window"),
    "spoof_jump_rate_excess": ("ais", "Mean of (hull jumped that day) minus its cells' jump incidence"),
    "n_days_observed": ("ais", "Distinct days with a DMA position in the window"),
    "n_gaps": ("gfw_gaps", "GFW AIS-off events ending in the window"),
    "gap_hours_total": ("gfw_gaps", "Total GFW gap hours in the window"),
    "max_gap_distance_km": ("gfw_gaps", "Longest implied distance across a gap"),
    "n_gaps_offshore": ("gfw_gaps", "Gaps starting more than 50 nm from shore"),
    "n_encounters": ("gfw_encounters", "GFW at-sea encounters ending in the window"),
    "n_encounters_with_sanctioned_partner": ("gfw_encounters",
                                             "Encounters whose partner was listed by any of OFAC, EU or "
                                             "UK as of T"),
    "n_loitering": ("gfw_ports", "GFW loitering events ending in the window"),
    "loitering_hours": ("gfw_ports", "Total GFW loitering hours in the window"),
    "n_port_visits": ("gfw_ports", "GFW port visits ending in the window"),
    "n_russian_port_visits": ("gfw_ports", "Port visits at a B1 port: anchorage country RUS inside "
                                           "config.RUSSIAN_PORT_REGIONS (PREREG amendment 2026-09-28)"),
    "days_since_last_russian_port_visit": ("gfw_ports", "Days from the last Russian port visit to T"),
    "n_sts_candidates": ("detect", "Self-built STS candidates ending in the window"),
    "n_sts_with_draught_change": ("detect", "Draught changes coinciding with an STS candidate"),
    "anchorage_loitering_hours": ("detect", "Self-built loitering hours in the window"),
    "n_draught_inconsistencies": ("detect", "Draught changes with neither a berth call nor an STS between"),
    "n_mmsi_imo_churn": ("detect", "MMSI-under-hull and IMO-under-MMSI changes on or before T"),
    "vessel_age_years": ("static", "Years since the GFW registry build year; null until Phase 4a"),
    "length_m": ("static", "Modal reported length"),
    "dwt": ("static", "Deadweight tonnage; DMA does not carry it, so null until a source does"),
}
SOURCE_OF_FAMILY = {"identity": "dma", "ais": "dma", "gfw_gaps": "gfw", "gfw_encounters": "gfw",
                    "gfw_ports": "gfw", "detect": "self_built", "static": "dma"}
# Everything defaults to 0, because "nothing observed" is a real zero. These are the exceptions, where a
# zero would be a lie. Listed explicitly rather than inferred from the name: a prefix rule would quietly
# give a newly added feature the wrong default.
NULLABLE = {"days_since_last_identity_change", "current_flag", "days_since_last_russian_port_visit",
            "vessel_age_years", "dwt", "mean_transit_speed_kn", "spoof_jump_rate_excess",
            "share_russian_destination", "max_gap_distance_km"}


def _ts(T: date) -> str:
    return f"TIMESTAMP '{T} 23:59:59'"


def population(T: date, con: duckdb.DuckDBPyConnection | None = None) -> list[str]:
    """Hulls with a DMA position in [T-180d, T] and not listed by any of OFAC, EU or UK as of T (rule 3)."""
    con = con or connect()
    # every feature family reads through this, so the "nothing ingested yet" guard belongs here rather
    # than surfacing as a DuckDB parse error about a glob that matched no files
    if not has_table("ais_dynamic"):
        raise SystemExit("no ais_dynamic parquet; run `make ingest-dma` (Phase 1) first")
    start = T - timedelta(days=config.FEATURE_WINDOW_DAYS)
    src = (f"(SELECT mmsi, observed_at FROM read_parquet('{glob_table('ais_dynamic')}',"
           f" hive_partitioning=true) WHERE observed_at BETWEEN TIMESTAMP '{start} 00:00:00'"
           f" AND {_ts(T)}) x")
    hulls = [r[0] for r in con.execute(f"""
        SELECT DISTINCT hm.hull_id FROM {at_cutoff(src, T)} WHERE hm.hull_id IS NOT NULL
    """).fetchall()]
    listed = lab.listed_as_of(T) if (config.PARQUET_DIR / lab.ACTIONS_FILE).exists() else {}
    return sorted(h for h in hulls if not (h.isdigit() and int(h) in listed))


def _identity(T: date, con: duckdb.DuckDBPyConnection) -> dict[str, dict]:
    if not (config.PARQUET_DIR / INTERVALS).exists():
        return {}
    return {r["hull_id"]: r for r in fid.features(T, con)}


def russian_destination_sql(col: str) -> str:
    """B1's DMA half: `col` names a B1 port, as a UN/LOCODE (RUULU, RU ULU, RU-ULU>EGPSD) or a name."""
    codes = "|".join(config.RUSSIAN_PORT_LOCODES)
    names = "|".join(config.RUSSIAN_PORTS)
    return (f"(regexp_matches(upper({col}), '(^|[^A-Z])RU[^A-Z0-9]?({codes})([^A-Z]|$)')"
            f" OR regexp_matches(regexp_replace(upper({col}), '[^A-Z0-9]', '', 'g'), '{names}'))")


def _ais(T: date, con: duckdb.DuckDBPyConnection) -> dict[str, dict]:
    """Transits, laden/ballast, destination text and spoof excess, all inside the feature window.

    A transit is a contiguous run of positions; a gap of more than TRANSIT_GAP_HOURS starts a new one. Laden
    is judged per transit against the hull's OWN draught distribution, so hull size does not decide it, and
    per transit rather than per message because the count of laden transits approximates liftings, whereas a
    share of laden messages is near one half for anything doing round trips.
    """
    start = T - timedelta(days=config.FEATURE_WINDOW_DAYS)
    since = f"BETWEEN TIMESTAMP '{start} 00:00:00' AND {_ts(T)}"
    dyn = (f"(SELECT mmsi, observed_at, sog FROM read_parquet('{glob_table('ais_dynamic')}',"
           f" hive_partitioning=true) WHERE observed_at {since}) x")
    stat = (f"(SELECT mmsi, observed_at, draught, destination FROM"
            f" read_parquet('{glob_table('ais_static')}', hive_partitioning=true)"
            f" WHERE observed_at {since}) x")
    ports = russian_destination_sql("destination")
    rows = con.execute(f"""
        WITH d AS (
          SELECT hm.hull_id, x.observed_at, x.sog FROM {at_cutoff(dyn, T)}
          WHERE hm.hull_id IS NOT NULL
        ), gaps AS (
          SELECT *, (epoch(observed_at) - epoch(lag(observed_at) OVER w)) / 3600.0 AS since_prev
          FROM d WINDOW w AS (PARTITION BY hull_id ORDER BY observed_at)
        ), runs AS (
          SELECT *, sum(CASE WHEN since_prev <= {TRANSIT_GAP_HOURS} THEN 0 ELSE 1 END)
                      OVER (PARTITION BY hull_id ORDER BY observed_at) AS transit
          FROM gaps
        ), tr AS (
          SELECT hull_id, transit, CAST(min(observed_at) AS DATE) AS d0,
                 CAST(max(observed_at) AS DATE) AS d1
          FROM runs GROUP BY hull_id, transit
        ), hull AS (
          SELECT hull_id, count(DISTINCT CAST(observed_at AS DATE)) AS n_days_observed,
                 avg(sog) FILTER (WHERE sog >= {config.SLOW_SOG_KN}) AS mean_transit_speed_kn
          FROM d GROUP BY hull_id
        ), s AS (
          SELECT hm.hull_id, CAST(x.observed_at AS DATE) AS day, max(x.draught) AS draught,
                 avg(CASE WHEN {ports} THEN 1.0 ELSE 0.0 END) AS rus
          FROM {at_cutoff(stat, T)} WHERE hm.hull_id IS NOT NULL GROUP BY 1, 2
        ), p AS (
          SELECT hull_id, quantile_cont(draught, {LADEN_PERCENTILE}) AS p75
          FROM s WHERE draught > 0 GROUP BY hull_id
        ), td AS (  -- a transit's draught is the deepest reported on any of its days
          SELECT tr.hull_id, tr.transit, max(s.draught) AS draught
          FROM tr LEFT JOIN s ON s.hull_id = tr.hull_id AND s.day BETWEEN tr.d0 AND tr.d1
          GROUP BY 1, 2
        )
        SELECT h.hull_id, h.n_days_observed, h.mean_transit_speed_kn,
               count(td.transit) AS n_transits,
               count(*) FILTER (WHERE td.draught > p.p75) AS n_laden_transits,
               count(*) FILTER (WHERE td.draught IS NOT NULL AND td.draught <= p.p75)
                 AS n_ballast_transits,
               (SELECT avg(rus) FROM s WHERE s.hull_id = h.hull_id) AS share_russian_destination
        FROM hull h LEFT JOIN td ON td.hull_id = h.hull_id LEFT JOIN p ON p.hull_id = h.hull_id
        GROUP BY h.hull_id, h.n_days_observed, h.mean_transit_speed_kn
    """).fetchall()
    keys = ["n_days_observed", "mean_transit_speed_kn", "n_transits", "n_laden_transits",
            "n_ballast_transits", "share_russian_destination"]
    out = {r[0]: dict(zip(keys, r[1:], strict=True)) for r in rows}

    if has_table(spoof.TABLE):
        for hull, excess in con.execute(f"""
            SELECT hm.hull_id, avg(d.excess)
            FROM read_parquet('{glob_table(spoof.TABLE)}', hive_partitioning=true) d
            JOIN {hull_at(T)} hm ON hm.mmsi = d.mmsi
            WHERE d.day BETWEEN DATE '{start}' AND DATE '{T}' GROUP BY 1
        """).fetchall():
            out.setdefault(hull, {})["spoof_jump_rate_excess"] = excess
    return out


def _detect(T: date, con: duckdb.DuckDBPyConnection) -> dict[str, dict]:
    start = T - timedelta(days=config.FEATURE_WINDOW_DAYS)
    out: dict[str, dict] = {}
    if has_table(sts.TABLE):
        for hull, n in con.execute(f"""
            SELECT hull, count(*) FROM (  -- one scan; a candidate counts for both of its hulls
              SELECT unnest([a.hull_id, b.hull_id]) AS hull
              FROM read_parquet('{glob_table(sts.TABLE)}', hive_partitioning=true) s
              JOIN {hull_at(T)} a ON a.mmsi = s.mmsi_a
              JOIN {hull_at(T)} b ON b.mmsi = s.mmsi_b
              WHERE s.observed_at BETWEEN TIMESTAMP '{start} 00:00:00' AND {_ts(T)}
                -- two transmitters that are one hull at T are one vessel with two transponders, which
                -- is a spoofing signal, not a transfer between two ships
                AND a.hull_id <> b.hull_id
            ) GROUP BY 1
        """).fetchall():
            out.setdefault(hull, {})["n_sts_candidates"] = n
    if has_table(loitering.TABLE):
        for hull, hours in con.execute(f"""
            SELECT hm.hull_id, sum(d.hours)
            FROM read_parquet('{glob_table(loitering.TABLE)}', hive_partitioning=true) d
            JOIN {hull_at(T)} hm ON hm.mmsi = d.mmsi
            WHERE d.observed_at BETWEEN TIMESTAMP '{start} 00:00:00' AND {_ts(T)} GROUP BY 1
        """).fetchall():
            out.setdefault(hull, {})["anchorage_loitering_hours"] = hours
    if has_table(draught.TABLE):
        for hull, unexplained, with_sts in con.execute(f"""
            SELECT hm.hull_id, count(*) FILTER (WHERE NOT d.moored_between AND NOT d.sts_between),
                   count(*) FILTER (WHERE d.sts_between)
            FROM read_parquet('{glob_table(draught.TABLE)}', hive_partitioning=true) d
            JOIN {hull_at(T)} hm ON hm.mmsi = d.mmsi
            WHERE d.observed_at BETWEEN TIMESTAMP '{start} 00:00:00' AND {_ts(T)} GROUP BY 1
        """).fetchall():
            out.setdefault(hull, {}).update(n_draught_inconsistencies=unexplained,
                                            n_sts_with_draught_change=with_sts)
    if has_table(churn.TABLE):
        # Lifetime, not windowed: churn is an identity fact, and the feature is a count as of T. Two halves:
        # transmitters the hull has used beyond its first, which only exists once hulls are assigned at T
        # (ADR-23), plus IMO changes under any of those transmitters, which the churn table stores.
        for hull, n in con.execute(f"""
            WITH m AS (SELECT * FROM {hull_at(T)}),
            imo AS (
              SELECT m.hull_id, count(*) AS n
              FROM read_parquet('{glob_table(churn.TABLE)}', hive_partitioning=true) c
              JOIN m ON m.mmsi = c.mmsi
              WHERE c.observed_at <= {_ts(T)} GROUP BY 1
            )
            SELECT m.hull_id, count(*) - 1 + coalesce(any_value(imo.n), 0)
            FROM m LEFT JOIN imo USING (hull_id) GROUP BY m.hull_id
        """).fetchall():
            if n:
                out.setdefault(hull, {})["n_mmsi_imo_churn"] = n
    return out


def _gfw(T: date, con: duckdb.DuckDBPyConnection) -> dict[str, dict]:
    """The three GFW families, from `gfw_events` (Phase 4a).

    Events belong to the IMO their vessel ids were fetched for, so they attach to IMO-keyed hulls only; a
    `syn:` hull has no IMO and so no GFW behaviour (ADR-23). Counts are over events that ENDED in the feature
    window, since `observed_at` is the end. `days_since_last_russian_port_visit` looks back over everything
    on or before T, because a visit a year ago is still the last one.
    """
    if not has_table(gfw.EVENTS_TABLE):
        return {}
    start = T - timedelta(days=config.FEATURE_WINDOW_DAYS)
    in_region = " OR ".join(f"(lat BETWEEN {a} AND {b} AND lon BETWEEN {c} AND {d})"
                            for a, b, c, d in config.RUSSIAN_PORT_REGIONS.values())
    russian = f"(event_type = 'port_visit' AND port_country = 'RUS' AND ({in_region}))"
    listed = sorted(lab.listed_as_of(T)) if (config.PARQUET_DIR / lab.ACTIONS_FILE).exists() else []
    con.execute("CREATE OR REPLACE TEMP TABLE listed_t AS SELECT unnest(?::BIGINT[]) AS imo", [listed])
    vmap = config.PARQUET_DIR / gfw.VESSEL_MAP
    partner_by_id = (f"SELECT gfw_vessel_id, imo FROM read_parquet('{vmap.as_posix()}')" if vmap.exists()
                     else "SELECT NULL::VARCHAR AS gfw_vessel_id, NULL::BIGINT AS imo WHERE false")
    cur = con.execute(f"""
        WITH e AS (
          SELECT * FROM read_parquet('{glob_table(gfw.EVENTS_TABLE)}', hive_partitioning=true)
          WHERE observed_at <= {_ts(T)}
        ), w AS (SELECT * FROM e WHERE observed_at >= TIMESTAMP '{start} 00:00:00'),
        -- an encounter partner is sanctioned if it is listed as of T, identified either by GFW vessel id
        -- (through the Phase 4a map) or by its MMSI (through the hull that MMSI is at T)
        partner AS (
          SELECT DISTINCT w.event_id
          FROM w
          LEFT JOIN ({partner_by_id}) pid ON pid.gfw_vessel_id = w.partner_gfw_id
          LEFT JOIN {hull_at(T)} pms ON pms.mmsi = w.partner_ssvid
          WHERE w.event_type = 'encounter'
            AND (pid.imo IN (SELECT imo FROM listed_t)
                 OR (regexp_full_match(pms.hull_id, '[0-9]+')
                     AND CAST(pms.hull_id AS BIGINT) IN (SELECT imo FROM listed_t)))
        )
        SELECT CAST(w.imo AS VARCHAR) AS hull_id,
          count(*) FILTER (WHERE event_type = 'gap') AS n_gaps,
          coalesce(sum(duration_h) FILTER (WHERE event_type = 'gap'), 0) AS gap_hours_total,
          coalesce(max(gap_distance_km) FILTER (WHERE event_type = 'gap'), 0) AS max_gap_distance_km,
          count(*) FILTER (WHERE event_type = 'gap'
                             AND start_distance_from_shore_km > {gfw.OFFSHORE_KM}) AS n_gaps_offshore,
          count(*) FILTER (WHERE event_type = 'encounter') AS n_encounters,
          count(*) FILTER (WHERE event_id IN (SELECT event_id FROM partner))
            AS n_encounters_with_sanctioned_partner,
          count(*) FILTER (WHERE event_type = 'loitering') AS n_loitering,
          coalesce(sum(duration_h) FILTER (WHERE event_type = 'loitering'), 0) AS loitering_hours,
          count(*) FILTER (WHERE event_type = 'port_visit') AS n_port_visits,
          count(*) FILTER (WHERE {russian}) AS n_russian_port_visits
        FROM w GROUP BY w.imo
    """)
    cols = [d[0] for d in cur.description]
    out = {r[0]: dict(zip(cols[1:], r[1:], strict=True)) for r in cur.fetchall()}
    for imo, days in con.execute(f"""
        SELECT CAST(imo AS VARCHAR), datediff('day', last_visit, {_ts(T)}) FROM (
          SELECT imo, max(observed_at) AS last_visit
          FROM read_parquet('{glob_table(gfw.EVENTS_TABLE)}', hive_partitioning=true)
          WHERE observed_at <= {_ts(T)} AND {russian} GROUP BY imo)
    """).fetchall():
        out.setdefault(imo, {})["days_since_last_russian_port_visit"] = days
    return out


def _static(T: date, con: duckdb.DuckDBPyConnection) -> dict[str, dict]:
    start = T - timedelta(days=config.FEATURE_WINDOW_DAYS)
    src = (f"(SELECT mmsi, day, length FROM read_parquet('{glob_table('vessel_day')}',"
           f" hive_partitioning=true) WHERE day BETWEEN DATE '{start}' AND DATE '{T}') x")
    return {r[0]: {"length_m": r[1]} for r in con.execute(f"""
        SELECT hm.hull_id, mode(x.length) FROM {at_cutoff(src, T)}
        WHERE hm.hull_id IS NOT NULL GROUP BY hm.hull_id
    """).fetchall()}


FEATURE_DECIMALS = 6


def features(T: date, con: duckdb.DuckDBPyConnection | None = None) -> list[dict]:
    """One row per hull in the population at T, every column in FEATURES."""
    con = con or connect()
    # Single-threaded, so every aggregate sees rows in the same order: with 8 threads DuckDB's float sums
    # and its tie-breaks (mode, arg_max) varied between runs, and rounding alone left 2 values a cutoff that
    # still flipped. Costs seconds per cutoff; reproducibility is worth more.
    threads = con.execute("SELECT current_setting('threads')").fetchone()[0]
    con.execute("SET threads = 1")
    try:
        hulls = population(T, con)
        parts = (_identity(T, con), _ais(T, con), _gfw(T, con), _detect(T, con), _static(T, con))
    finally:
        con.execute(f"SET threads = {int(threads)}")
    rows = []
    for h in hulls:
        row: dict = {"hull_id": h, "cutoff": T}
        for part in parts:
            row.update(part.get(h) or {})
        for name in FEATURES:
            if name not in row:
                row[name] = None if name in NULLABLE else 0
        # Rounded so a re-run gives the same numbers: DuckDB sums floats across threads in no fixed order,
        # so averages differed in the 14th digit between runs (2,096 of a cutoff's values on Oct 6 2026), and
        # LightGBM, which bins on exact values, trained a different model each time. Six decimals is far
        # below anything a feature measures.
        rows.append({k: round(row[k], FEATURE_DECIMALS) if isinstance(row[k], float) else row[k]
                     for k in ["hull_id", "cutoff", *FEATURES]})
    return rows


def build(cutoffs: list[date] | None = None) -> dict:
    """Write `feature_matrix/cutoff=T/` for every monthly cutoff. Without `gfw_events` (Phase 4a not run,
    or run with no token) the GFW families stay at their defaults; `gfw_present` reports it."""


    con = connect()
    cutoffs = cutoffs or config.monthly_cutoffs(config.load_window(), date.today())
    out: dict = {"cutoffs": [], "features": len(FEATURES),
                 "families": sorted({f for f, _ in FEATURES.values()}),
                 "gfw_present": has_table(gfw.EVENTS_TABLE),
                 # published for the report, so `util.report` never has to import a phase: the arrow runs
                 # phase -> report only, and util/ stays a leaf
                 "registry": {n: [f, d] for n, (f, d) in FEATURES.items()},
                 "source_of_family": SOURCE_OF_FAMILY, "transit_gap_hours": TRANSIT_GAP_HOURS}
    for T in cutoffs:
        t0 = time.time()
        rows = features(T, con)
        d = config.PARQUET_DIR / "feature_matrix" / f"cutoff={T.isoformat()}"
        if rows:  # a cutoff with no population writes nothing; readers glob the partitions
            d.mkdir(parents=True, exist_ok=True)
            pq.write_table(pa.Table.from_pylist(rows), d / "part-0.parquet", compression="zstd")
        out["cutoffs"].append({"cutoff": T.isoformat(), "hulls": len(rows),
                               "seconds": round(time.time() - t0, 1)})
    out["table"] = rel_path(config.PARQUET_DIR / "feature_matrix")
    return out


