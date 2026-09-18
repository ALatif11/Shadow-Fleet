"""As-of identity features (PREREG family `identity`).

Everything is read from `identity_intervals.parquet` with `start <= T`, so the same call against a store
physically truncated at T returns the same rows. That equality is Phase 5a leakage test (a); the version
here is asserted in `tests/test_identity.py`.
"""

from __future__ import annotations

from datetime import date

import duckdb

from shadowfleet import config
from shadowfleet.ingest.dma import connect
from shadowfleet.resolve.identity import INTERVALS

FEATURES = {
    "n_name_changes": "Reported-name changes observed on or before T",
    "n_mmsi_changes": "MMSI changes observed on or before T",
    "n_flag_changes": "Flag (from the ITU MID of the MMSI) changes observed on or before T",
    "flag_to_convenience_registry": "A flag change into a registry on config.CONVENIENCE_FLAGS",
    "days_since_last_identity_change": "Days from the last identity change to T; null if never changed",
    "current_flag": "Flag in force at T",
    "vessel_age_years": "Build year from the GFW registry; null until Phase 4a populates it",
}


def features(T: date, con: duckdb.DuckDBPyConnection | None = None,
             hull_ids: list[str] | None = None) -> list[dict]:
    """One row per hull with an identity interval starting on or before T."""
    con = con or connect()
    path = (config.PARQUET_DIR / INTERVALS).as_posix()
    flags = ", ".join(f"'{f}'" for f in config.CONVENIENCE_FLAGS)
    only = ""
    if hull_ids is not None:
        if not hull_ids:
            return []
        only = "AND hull_id IN (" + ", ".join(f"'{h}'" for h in hull_ids) + ")"
    rows = con.execute(f"""
        WITH iv AS (
          SELECT * FROM read_parquet('{path}') WHERE "start" <= TIMESTAMP '{T} 23:59:59' {only}
        ), c AS (
          SELECT hull_id, "start", mmsi, name_normalised, flag_iso3,
            row_number() OVER w AS rn,
            lag(mmsi) OVER w AS p_mmsi, lag(name_normalised) OVER w AS p_name,
            lag(flag_iso3) OVER w AS p_flag
          FROM iv WINDOW w AS (PARTITION BY hull_id ORDER BY "start")
        )
        SELECT hull_id,
          count(*) FILTER (WHERE p_name IS NOT NULL AND name_normalised IS NOT NULL
                             AND p_name <> name_normalised) AS n_name_changes,
          count(*) FILTER (WHERE p_mmsi IS NOT NULL AND mmsi <> p_mmsi) AS n_mmsi_changes,
          count(*) FILTER (WHERE p_flag IS NOT NULL AND flag_iso3 IS NOT NULL
                             AND p_flag <> flag_iso3) AS n_flag_changes,
          coalesce(bool_or(p_flag IS NOT NULL AND flag_iso3 IS NOT NULL AND p_flag <> flag_iso3
                           AND flag_iso3 IN ({flags})), false) AS flag_to_convenience_registry,
          datediff('day', max("start") FILTER (WHERE rn > 1), TIMESTAMP '{T} 23:59:59')
            AS days_since_last_identity_change,
          arg_max(flag_iso3, "start") AS current_flag
        FROM c GROUP BY hull_id ORDER BY hull_id
    """).fetchall()
    cols = ["hull_id", "n_name_changes", "n_mmsi_changes", "n_flag_changes",
            "flag_to_convenience_registry", "days_since_last_identity_change", "current_flag"]
    # vessel_age_years needs the GFW registry build year (Phase 4a); null keeps the column in the frozen list.
    return [dict(zip(cols, r, strict=True), vessel_age_years=None) for r in rows]
