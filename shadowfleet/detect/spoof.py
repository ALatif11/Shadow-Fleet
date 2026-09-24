"""Spoof-jump excess: a hull's jump rate minus what the cells it sat in did that day.

The normalisation is the whole point (plan R9). GNSS interference in the eastern Baltic makes every vessel
in a cell jump on the same day, so a raw jump count scores the geography, not the hull. The baseline is
`jump_baseline` (share of all vessels jumping, per 0.5-degree cell per day, computed over every vessel at
ingest because the raw rows are deleted -- ADR-14), weighted by how many of the hull's rows were in each
cell. Excess can be negative; that is information, not an error, so it is not clipped.
"""

from __future__ import annotations

import logging

import duckdb

from shadowfleet import config
from shadowfleet.ingest.dma import connect
from shadowfleet.resolve.identity import as_of_hull
from shadowfleet.util.store import glob_table, rel_path

log = logging.getLogger(__name__)

TABLE = "detect_spoof"


def run(con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """Write `detect_spoof`, one row per hull-day, `observed_at` = end of that day (as ingest set it)."""
    con = con or connect()
    src = (f"(SELECT mmsi, day, observed_at, n_rows_fullres, n_jumps, cell_rows"
           f" FROM read_parquet('{glob_table('ais_artifacts')}', hive_partitioning=true)"
           f" WHERE n_rows_fullres > 0) x")
    out = config.PARQUET_DIR / TABLE / "dt=all"
    out.mkdir(parents=True, exist_ok=True)
    n = con.execute(f"""
        COPY (
          WITH a AS (
            SELECT hm.hull_id, x.mmsi, x.day, x.observed_at,
                   x.n_rows_fullres, x.n_jumps, x.cell_rows
            FROM {as_of_hull(src, 'day')}
        -- Only hulls the resolver has named. A record from a vessel's warm-up (before its first
        -- window closed) has no hull id, and inventing one from the MMSI would split the hull's
        -- history at the boundary: its first real transition would land between two different ids
        -- and vanish. The warm-up is always before the first cutoff, so nothing evaluable is lost.
            WHERE hm.hull_id IS NOT NULL
          ), cells AS (  -- one row per (hull-day, cell) with that cell's share of the hull's rows
            SELECT a.hull_id, a.mmsi, a.day, a.observed_at, a.n_rows_fullres, a.n_jumps,
                   c.cell_id, c.n_rows AS rows_in_cell
            FROM a, unnest(a.cell_rows) AS t(c)
          ), joined AS (
            SELECT cells.*, coalesce(b.frac_jumped, 0) AS cell_frac
            FROM cells LEFT JOIN read_parquet('{glob_table('jump_baseline')}', hive_partitioning=true) b
              ON b.cell_id = cells.cell_id AND b.day = cells.day
          )
          SELECT hull_id, any_value(mmsi) AS mmsi, day, any_value(observed_at) AS observed_at,
            any_value(n_rows_fullres) AS n_rows, any_value(n_jumps) AS n_jumps,
            any_value(n_jumps)::DOUBLE / any_value(n_rows_fullres) AS jump_rate,
            sum(cell_frac * rows_in_cell) / sum(rows_in_cell) AS expected_rate,
            any_value(n_jumps)::DOUBLE / any_value(n_rows_fullres)
              - sum(cell_frac * rows_in_cell) / sum(rows_in_cell) AS excess,
            count(*) AS cells_visited
          FROM joined GROUP BY hull_id, day ORDER BY day, hull_id
        ) TO '{(out / 'part-0.parquet').as_posix()}' (FORMAT parquet, COMPRESSION zstd)
    """).fetchone()[0]
    stats = con.execute(f"""
        SELECT count(*) FILTER (WHERE excess > 0), count(*) FILTER (WHERE n_jumps > 0),
               round(max(excess), 4), round(avg(excess), 6), count(DISTINCT hull_id)
        FROM read_parquet('{(out / 'part-0.parquet').as_posix()}')
    """).fetchone()
    return {"hull_days": n, "with_positive_excess": stats[0], "with_any_jump": stats[1],
            "max_excess": stats[2], "mean_excess": stats[3], "hulls": stats[4],
            "table": rel_path(config.PARQUET_DIR / TABLE)}
