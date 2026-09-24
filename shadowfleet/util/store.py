"""Parquet store helpers: where a table lives, whether it exists, and great-circle distance in SQL.

These were private names in `phase1` until Phase 4b, when four detector modules needed them too.
"""

from __future__ import annotations

from pathlib import Path

from shadowfleet import config


def glob_table(table: str) -> str:
    """Hive-partitioned glob for a table under data/parquet/."""
    return (config.PARQUET_DIR / table / "dt=*" / "*.parquet").as_posix()


def has_table(table: str) -> bool:
    return any((config.PARQUET_DIR / table).glob("dt=*/*.parquet"))


def haversine_km_sql(lat1: str, lon1: str, lat2: str, lon2: str) -> str:
    """Great-circle km between two SQL expressions."""
    return (f"2 * 6371.0088 * asin(sqrt(pow(sin(radians({lat2} - {lat1}) / 2), 2) + "
            f"cos(radians({lat1})) * cos(radians({lat2})) * pow(sin(radians({lon2} - {lon1}) / 2), 2)))")


def rel_path(p: Path) -> str:
    """Repo-relative when possible; tests point the paths elsewhere."""
    try:
        return str(p.relative_to(config.REPO_ROOT))
    except ValueError:
        return str(p)
