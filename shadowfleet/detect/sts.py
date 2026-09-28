"""STS candidates on DMA tracks: two tanker-class hulls close and slow for long enough, not at a berth.

Why there is no port polygon here (ADR-12, revisited in Phase 4b): the Phase 4b prompt says "outside port
polygons", but DMA already carries the vessel's own `nav_status`, and a hull alongside a berth reports
"Moored". Using that is one predicate instead of a hand-drawn polygon set that would be wrong in ways nobody
could check. Vessels *at anchor* are kept on purpose: the Skagen anchorage transfers happen at anchor.

`ais_dynamic` is already filtered to tanker-class hulls at ingest (ADR-14), so there is no type filter here.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import duckdb

from shadowfleet import config
from shadowfleet.ingest.dma import connect
from shadowfleet.util.store import glob_table, haversine_km_sql, rel_path

log = logging.getLogger(__name__)

RADIUS_M = 500  # phase-prompts Phase 4b task 1
MAX_SOG_KN = 2.0
MIN_HOURS = 2.0
BUCKET_DEG = 0.01  # 3x3 neighbourhood covers 500 m at Danish latitudes (0.01 deg lat = 1.11 km)
GAP_TOLERANCE_MIN = 10  # a missed minute must not split one transfer into two
MIN_COVERAGE = 0.5  # qualifying minutes as a share of elapsed minutes, so a sparse pair does not qualify
TABLE = "detect_sts"


def _months(start: date, end: date) -> list[date]:
    out, m = [], start.replace(day=1)
    while m <= end:
        out.append(m)
        m = (m + timedelta(days=32)).replace(day=1)
    return out


def _slow_positions(con: duckdb.DuckDBPyConnection, first: date, last: date, max_sog: float) -> None:
    """Minute-averaged slow positions per transmitter, bucketed for the pair join.

    Keyed by MMSI, never by hull (ADR-23): a candidate is two transmitters close together, a physical fact.
    Which hulls they are is decided at each cutoff by `resolve.identity.hull_at`.
    """
    src = (f"(SELECT mmsi, observed_at, lat, lon, sog, nav_status"
           f" FROM read_parquet('{glob_table('ais_dynamic')}', hive_partitioning=true)"
           f" WHERE sog < {max_sog} AND observed_at >= TIMESTAMP '{first} 00:00:00'"
           f" AND observed_at <= TIMESTAMP '{last} 23:59:59'"
           f" AND coalesce(lower(nav_status), '') NOT LIKE '%moor%') x")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE slow AS
        SELECT x.mmsi,
               date_trunc('minute', x.observed_at) AS minute,
               avg(x.lat) AS lat, avg(x.lon) AS lon,
               CAST(floor(avg(x.lat) / {BUCKET_DEG}) AS INTEGER) AS blat,
               CAST(floor(avg(x.lon) / {BUCKET_DEG}) AS INTEGER) AS blon
        FROM {src}
        GROUP BY 1, 2
    """)


def run(con: duckdb.DuckDBPyConnection | None = None, radius_m: int = RADIUS_M,
        max_sog: float = MAX_SOG_KN, min_hours: float = MIN_HOURS) -> dict:
    """Write `detect_sts`, one row per candidate transfer, `observed_at` = when it ended."""
    con = con or connect()
    span = con.execute(f"SELECT min(day), max(day) FROM "
                       f"read_parquet('{glob_table('vessel_day')}', hive_partitioning=true)").fetchone()
    if not span or span[0] is None:
        return {"skipped": "no vessel_day parquet"}
    dist_km = haversine_km_sql("a.lat", "a.lon", "b.lat", "b.lon")
    written, months = 0, _months(span[0], span[1])
    for m in months:
        nxt = (m + timedelta(days=32)).replace(day=1)
        # one day of overlap on the left so a transfer running over midnight on the 1st is still seen whole;
        # runs are then kept only if they start inside the month, so no candidate is written twice
        _slow_positions(con, m - timedelta(days=1), nxt - timedelta(days=1), max_sog)
        out = config.PARQUET_DIR / TABLE / f"dt={m.isoformat()}"
        out.mkdir(parents=True, exist_ok=True)
        n = con.execute(f"""
            COPY (
              WITH p AS (
                SELECT a.mmsi AS mmsi_a, b.mmsi AS mmsi_b,
                       a.minute, {dist_km} * 1000 AS m_apart,
                       (a.lat + b.lat) / 2 AS lat, (a.lon + b.lon) / 2 AS lon
                FROM slow a JOIN slow b
                  ON a.minute = b.minute AND a.mmsi < b.mmsi
                 AND b.blat BETWEEN a.blat - 1 AND a.blat + 1
                 AND b.blon BETWEEN a.blon - 1 AND a.blon + 1
                WHERE {dist_km} * 1000 < {radius_m}
              ), q AS (
                SELECT *, epoch(minute) / 60 AS mi FROM p
              ), gaps AS (
                SELECT *, mi - lag(mi) OVER (PARTITION BY mmsi_a, mmsi_b ORDER BY mi) AS since_prev
                FROM q
              ), g AS (  -- a new run starts wherever the gap to the previous qualifying minute is too long
                SELECT *, sum(CASE WHEN since_prev <= {GAP_TOLERANCE_MIN} THEN 0 ELSE 1 END)
                            OVER (PARTITION BY mmsi_a, mmsi_b ORDER BY mi) AS run
                FROM gaps
              )
              SELECT mmsi_a, mmsi_b,
                min(minute) AS start, max(minute) AS "end", max(minute) AS observed_at,
                count(*) AS qualifying_minutes,
                (epoch(max(minute)) - epoch(min(minute))) / 3600.0 AS hours,
                avg(lat) AS lat, avg(lon) AS lon, min(m_apart) AS min_distance_m
              FROM g GROUP BY mmsi_a, mmsi_b, run
              HAVING (epoch(max(minute)) - epoch(min(minute))) / 60.0 >= {int(min_hours * 60)}
                 AND count(*) >= {MIN_COVERAGE} * ((epoch(max(minute)) - epoch(min(minute))) / 60.0)
                 AND CAST(min(minute) AS DATE) >= DATE '{m}'
            ) TO '{(out / 'part-0.parquet').as_posix()}' (FORMAT parquet, COMPRESSION zstd)
        """).fetchone()[0]
        written += n
        log.info("sts %s: %d candidates", m, n)
    return {"candidates": written, "months": len(months), "radius_m": radius_m, "max_sog": max_sog,
            "min_hours": min_hours, "table": rel_path(config.PARQUET_DIR / TABLE)}
