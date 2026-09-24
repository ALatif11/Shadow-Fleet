"""Loitering on DMA tracks: a hull barely moving for a long stretch, not at a berth.

No anchorage polygon (see the note in sts.py). The event carries its own position, so the anchorages fall
out of `by_cell` instead of being asserted in advance; `config.SKAGEN_ANCHORAGE_BBOX` is now only used by
the Phase 1 readiness count and is no longer on the critical path.
"""

from __future__ import annotations

import logging

import duckdb

from shadowfleet import config
from shadowfleet.ingest.dma import connect
from shadowfleet.resolve.identity import as_of_hull
from shadowfleet.util.store import glob_table, rel_path

log = logging.getLogger(__name__)

MAX_SOG_KN = 1.0  # phase-prompts Phase 4b task 2
MIN_HOURS = 12.0
GAP_TOLERANCE_MIN = 30  # a longer tolerance than STS: over 12 hours a few missed minutes mean nothing
TABLE = "detect_loitering"


def run(con: duckdb.DuckDBPyConnection | None = None, max_sog: float = MAX_SOG_KN,
        min_hours: float = MIN_HOURS) -> dict:
    """Write `detect_loitering`, one row per stretch, `observed_at` = when it ended."""
    con = con or connect()
    src = (f"(SELECT mmsi, observed_at, lat, lon, sog, nav_status"
           f" FROM read_parquet('{glob_table('ais_dynamic')}', hive_partitioning=true)"
           f" WHERE sog < {max_sog}"
           f" AND coalesce(lower(nav_status), '') NOT LIKE '%moor%') x")
    out = config.PARQUET_DIR / TABLE / "dt=all"
    out.mkdir(parents=True, exist_ok=True)
    n = con.execute(f"""
        COPY (
          WITH s AS (
            SELECT hm.hull_id, x.mmsi, date_trunc('minute', x.observed_at) AS minute,
                   avg(x.lat) AS lat, avg(x.lon) AS lon,
                   any_value(x.nav_status) AS nav_status
            FROM {as_of_hull(src, 'observed_at')}
        -- Only hulls the resolver has named. A record from a vessel's warm-up (before its first
        -- window closed) has no hull id, and inventing one from the MMSI would split the hull's
        -- history at the boundary: its first real transition would land between two different ids
        -- and vanish. The warm-up is always before the first cutoff, so nothing evaluable is lost.
            WHERE hm.hull_id IS NOT NULL
            GROUP BY 1, 2, 3
          ), q AS (
            SELECT *, epoch(minute) / 60 AS mi FROM s
          ), gaps AS (
            SELECT *, mi - lag(mi) OVER (PARTITION BY hull_id ORDER BY mi) AS since_prev FROM q
          ), g AS (  -- a new run starts wherever the gap to the previous slow minute is too long
            SELECT *, sum(CASE WHEN since_prev <= {GAP_TOLERANCE_MIN} THEN 0 ELSE 1 END)
                        OVER (PARTITION BY hull_id ORDER BY mi) AS run
            FROM gaps
          )
          SELECT hull_id, any_value(mmsi) AS mmsi,
            min(minute) AS start, max(minute) AS "end", max(minute) AS observed_at,
            (epoch(max(minute)) - epoch(min(minute))) / 3600.0 AS hours,
            avg(lat) AS lat, avg(lon) AS lon,
            mode(nav_status) AS modal_nav_status
          FROM g GROUP BY hull_id, run
          HAVING (epoch(max(minute)) - epoch(min(minute))) / 3600.0 >= {min_hours}
        ) TO '{(out / 'part-0.parquet').as_posix()}' (FORMAT parquet, COMPRESSION zstd)
    """).fetchone()[0]
    return {"events": n, "max_sog": max_sog, "min_hours": min_hours,
            "table": rel_path(config.PARQUET_DIR / TABLE)}


def by_cell(con: duckdb.DuckDBPyConnection | None = None, cell_deg: float = 0.05) -> list[dict]:
    """The anchorages, derived. Top cells by loitering hours."""
    con = con or connect()
    rows = con.execute(f"""
        SELECT round(floor(lat / {cell_deg}) * {cell_deg}, 3) AS lat,
               round(floor(lon / {cell_deg}) * {cell_deg}, 3) AS lon,
               count(*) AS events, round(sum(hours), 1) AS hours, count(DISTINCT hull_id) AS hulls
        FROM read_parquet('{glob_table(TABLE)}', hive_partitioning=true)
        GROUP BY 1, 2 ORDER BY hours DESC LIMIT 20
    """).fetchall()
    return [{"lat": r[0], "lon": r[1], "events": r[2], "hours": r[3], "hulls": r[4]} for r in rows]
