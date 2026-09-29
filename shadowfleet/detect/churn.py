"""MMSI-IMO churn, the half of it that is a fact about one transmitter: the IMO moving under a stable MMSI.

The other half, an MMSI moving under a stable hull, depends on which transmitters belong to that hull, and
that is only decided at a cutoff (ADR-23). So it is counted in `features.asof` from `hull_at(T)`, not stored
here.

An IMO counts as established under an MMSI on the MIN_IMO_DAYS-th distinct day it was broadcast there, and
that moment is the change's `observed_at`. This reads the raw broadcasts, not hull_map's cumulative vote:
a lifetime vote only flips once the new IMO has outlasted the old one, and is `syn` while it catches up, so
on the real store it found 0 changes in 31 months (Sep 29), which is a detector that cannot fire.
"""

from __future__ import annotations

import duckdb

from shadowfleet import config
from shadowfleet.ingest.dma import connect
from shadowfleet.resolve.identity import MIN_IMO_DAYS
from shadowfleet.util.ids import imo_valid_sql
from shadowfleet.util.store import glob_table, rel_path

TABLE = "detect_churn"


def run(con: duckdb.DuckDBPyConnection | None = None, min_days: int = MIN_IMO_DAYS) -> dict:
    """Write `detect_churn`, one row per change, `observed_at` = when the new IMO became established."""
    con = con or connect()
    out = config.PARQUET_DIR / TABLE / "dt=all"
    out.mkdir(parents=True, exist_ok=True)
    # ponytail: an IMO is counted once per MMSI, so A -> B -> A is one change, not two. Add a re-establish
    # rule if flip-backs turn out to matter.
    n = con.execute(f"""
        COPY (
          WITH d AS (
            SELECT mmsi, CAST(imo AS BIGINT) AS imo, CAST(observed_at AS DATE) AS day, min(observed_at) AS t
            FROM read_parquet('{glob_table('ais_static')}', hive_partitioning=true) s
            WHERE {imo_valid_sql('s.imo')} GROUP BY 1, 2, 3
          ), est AS (
            SELECT mmsi, imo, t FROM d
            QUALIFY row_number() OVER (PARTITION BY mmsi, imo ORDER BY day) = {int(min_days)}
          ), ch AS (
            SELECT *, lag(imo) OVER (PARTITION BY mmsi ORDER BY t, imo) AS p_imo FROM est
          )
          SELECT 'imo_under_mmsi' AS kind, mmsi, t AS observed_at,
                 CAST(p_imo AS VARCHAR) AS old_value, CAST(imo AS VARCHAR) AS new_value
          FROM ch WHERE p_imo IS NOT NULL
        ) TO '{(out / 'part-0.parquet').as_posix()}' (FORMAT parquet, COMPRESSION zstd)
    """).fetchone()[0]
    by_kind = dict(con.execute(f"SELECT kind, count(*) FROM read_parquet('{glob_table(TABLE)}',"
                               f" hive_partitioning=true) GROUP BY kind").fetchall())
    return {"changes": n, "by_kind": by_kind, "min_days": min_days, "table": rel_path(config.PARQUET_DIR / TABLE)}
