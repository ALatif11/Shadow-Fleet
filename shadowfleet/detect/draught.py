"""Declared-draught changes, and whether anything in the data explains them.

A laden/ballast change with no berth call in between is the lightering signal; one that coincides with an
STS candidate is the same event seen from the other side. "No port visit" is read off DMA's own
`nav_status` (a "Moored" report between the two draughts), because GFW port visits only arrive in Phase 4a.
When they do, this gains a second, stronger predicate; the column name stays.
"""

from __future__ import annotations

import logging

import duckdb

from shadowfleet import config
from shadowfleet.ingest.dma import connect
from shadowfleet.resolve.identity import as_of_hull
from shadowfleet.util.store import glob_table, has_table, rel_path

log = logging.getLogger(__name__)

MIN_CHANGE_M = 1.0  # phase-prompts Phase 4b task 3
STS_TABLE = "detect_sts"
TABLE = "detect_draught"


def run(con: duckdb.DuckDBPyConnection | None = None, min_change_m: float = MIN_CHANGE_M) -> dict:
    """Write `detect_draught`, one row per change over the threshold, `observed_at` = the new reading."""
    con = con or connect()
    src = (f"(SELECT mmsi, observed_at, draught"
           f" FROM read_parquet('{glob_table('ais_static')}', hive_partitioning=true)"
           f" WHERE draught IS NOT NULL AND draught > 0) x")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE dch AS
        WITH d AS (
          SELECT hm.hull_id, x.mmsi, x.observed_at, x.draught
          FROM {as_of_hull(src, 'observed_at')}
        -- Only hulls the resolver has named. A record from a vessel's warm-up (before its first
        -- window closed) has no hull id, and inventing one from the MMSI would split the hull's
        -- history at the boundary: its first real transition would land between two different ids
        -- and vanish. The warm-up is always before the first cutoff, so nothing evaluable is lost.
          WHERE hm.hull_id IS NOT NULL
        ), c AS (
          SELECT *, lag(draught) OVER w AS prev_draught, lag(observed_at) OVER w AS prev_at
          FROM d WINDOW w AS (PARTITION BY hull_id ORDER BY observed_at)
        )
        SELECT hull_id, mmsi, prev_at AS "from", observed_at AS "to", observed_at AS observed_at,
               prev_draught, draught, draught - prev_draught AS delta_m
        FROM c
        WHERE prev_draught IS NOT NULL AND abs(draught - prev_draught) >= {min_change_m}
    """)
    # a "Moored" report between the two readings is the DMA-native stand-in for a port call
    moored = (f"(SELECT mmsi, observed_at FROM read_parquet('{glob_table('ais_dynamic')}',"
              f" hive_partitioning=true) WHERE lower(nav_status) LIKE '%moor%')")
    empty_sts = ('(SELECT NULL AS hull_a, NULL AS hull_b, NULL::TIMESTAMP AS start,'
                 ' NULL::TIMESTAMP AS "end" WHERE false)')
    sts = (f"read_parquet('{glob_table(STS_TABLE)}', hive_partitioning=true)"
           if has_table(STS_TABLE) else empty_sts)
    out = config.PARQUET_DIR / TABLE / "dt=all"
    out.mkdir(parents=True, exist_ok=True)
    n = con.execute(f"""
        COPY (
          SELECT c.*,
            EXISTS (SELECT 1 FROM {moored} m
                    WHERE m.mmsi = c.mmsi AND m.observed_at > c."from" AND m.observed_at < c."to")
              AS moored_between,
            -- interval overlap, not "started strictly between": a transfer that began before the earlier
            -- reading and ended after it still explains the change
            EXISTS (SELECT 1 FROM {sts} s
                    WHERE (s.hull_a = c.hull_id OR s.hull_b = c.hull_id)
                      AND s."end" > c."from" AND s.start < c."to")
              AS sts_between
          FROM dch c ORDER BY hull_id, "to"
        ) TO '{(out / 'part-0.parquet').as_posix()}' (FORMAT parquet, COMPRESSION zstd)
    """).fetchone()[0]
    counts = con.execute(f"""
        SELECT count(*) FILTER (WHERE NOT moored_between AND NOT sts_between) AS unexplained,
               count(*) FILTER (WHERE sts_between) AS with_sts,
               count(*) FILTER (WHERE moored_between) AS with_berth,
               count(DISTINCT hull_id) AS hulls
        FROM read_parquet('{(out / 'part-0.parquet').as_posix()}')
    """).fetchone()
    return {"changes": n, "unexplained": counts[0], "coinciding_with_sts": counts[1],
            "after_a_berth_call": counts[2], "hulls": counts[3], "min_change_m": min_change_m,
            "table": rel_path(config.PARQUET_DIR / TABLE)}
