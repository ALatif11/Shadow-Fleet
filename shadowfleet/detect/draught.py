"""Declared-draught changes, and whether anything in the data explains them.

A laden/ballast change with no berth call in between is the lightering signal; one that coincides with an
STS candidate is the same event seen from the other side. "No port visit" is read off DMA's own
`nav_status` (a "Moored" report between the two draughts), because GFW port visits only arrive in Phase 4a.
When they do, this gains a second, stronger predicate; the column name stays.
"""

from __future__ import annotations

import duckdb

from shadowfleet import config
from shadowfleet.ingest.dma import connect
from shadowfleet.util.store import glob_table, has_table, rel_path

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
          -- per transmitter; hulls are assigned at each cutoff (ADR-23). Two different draughts stamped
          -- the same instant are two voices on one MMSI, not a change of draught, and made the count
          -- differ between runs (63,726 vs 63,609 on Sep 29) because the order of tied rows is arbitrary.
          SELECT x.mmsi, x.observed_at, any_value(x.draught) AS draught
          FROM {src} GROUP BY 1, 2 HAVING min(x.draught) = max(x.draught)
        ), c AS (
          SELECT *, lag(draught) OVER w AS prev_draught, lag(observed_at) OVER w AS prev_at
          FROM d WINDOW w AS (PARTITION BY mmsi ORDER BY observed_at)
        )
        SELECT mmsi, prev_at AS "from", observed_at AS "to", observed_at AS observed_at,
               prev_draught, draught, draught - prev_draught AS delta_m
        FROM c
        WHERE prev_draught IS NOT NULL AND abs(draught - prev_draught) >= {min_change_m}
    """)
    # a "Moored" report between the two readings is the DMA-native stand-in for a port call
    moored = (f"(SELECT mmsi, observed_at FROM read_parquet('{glob_table('ais_dynamic')}',"
              f" hive_partitioning=true) WHERE lower(nav_status) LIKE '%moor%')")
    empty_sts = ('(SELECT NULL::BIGINT AS mmsi_a, NULL::BIGINT AS mmsi_b, NULL::TIMESTAMP AS start,'
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
                    WHERE (s.mmsi_a = c.mmsi OR s.mmsi_b = c.mmsi)
                      AND s."end" > c."from" AND s.start < c."to")
              AS sts_between
          FROM dch c ORDER BY mmsi, "to"
        ) TO '{(out / 'part-0.parquet').as_posix()}' (FORMAT parquet, COMPRESSION zstd)
    """).fetchone()[0]
    counts = con.execute(f"""
        SELECT count(*) FILTER (WHERE NOT moored_between AND NOT sts_between) AS unexplained,
               count(*) FILTER (WHERE sts_between) AS with_sts,
               count(*) FILTER (WHERE moored_between) AS with_berth,
               count(DISTINCT mmsi) AS mmsis
        FROM read_parquet('{(out / 'part-0.parquet').as_posix()}')
    """).fetchone()
    return {"changes": n, "unexplained": counts[0], "coinciding_with_sts": counts[1],
            "after_a_berth_call": counts[2], "mmsis": counts[3], "min_change_m": min_change_m,
            "table": rel_path(config.PARQUET_DIR / TABLE)}
