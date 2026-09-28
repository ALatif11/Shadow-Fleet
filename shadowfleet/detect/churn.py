"""MMSI-IMO churn, the half of it that is a fact about one transmitter: the IMO moving under a stable MMSI.

The other half, an MMSI moving under a stable hull, depends on which transmitters belong to that hull, and
that is only decided at a cutoff (ADR-23). So it is counted in `features.asof` from `hull_at(T)`, not stored
here. An IMO change carries a real `observed_at`: the day the first window whose cumulative vote picked a
different number took effect.
"""

from __future__ import annotations

import duckdb

from shadowfleet import config
from shadowfleet.ingest.dma import connect
from shadowfleet.resolve.identity import HULL_MAP
from shadowfleet.util.store import glob_table, rel_path

TABLE = "detect_churn"


def run(con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """Write `detect_churn`, one row per change, `observed_at` = when the new value first applied."""
    con = con or connect()
    hull_map = (config.PARQUET_DIR / HULL_MAP).as_posix()
    out = config.PARQUET_DIR / TABLE / "dt=all"
    out.mkdir(parents=True, exist_ok=True)
    n = con.execute(f"""
        COPY (
          SELECT 'imo_under_mmsi' AS kind, mmsi,
                 effective_from::TIMESTAMP AS observed_at,
                 CAST(p_imo AS VARCHAR) AS old_value, CAST(voted_imo AS VARCHAR) AS new_value
          FROM (SELECT mmsi, effective_from, voted_imo,
                       lag(voted_imo) OVER (PARTITION BY mmsi ORDER BY window_start) AS p_imo
                FROM read_parquet('{hull_map}') WHERE method = 'imo_majority')
          WHERE p_imo IS NOT NULL AND p_imo <> voted_imo
        ) TO '{(out / 'part-0.parquet').as_posix()}' (FORMAT parquet, COMPRESSION zstd)
    """).fetchone()[0]
    by_kind = dict(con.execute(f"SELECT kind, count(*) FROM read_parquet('{glob_table(TABLE)}',"
                               f" hive_partitioning=true) GROUP BY kind").fetchall())
    return {"changes": n, "by_kind": by_kind, "table": rel_path(config.PARQUET_DIR / TABLE)}
