"""Phase 4b: detection layers the project owns, built on DMA tracks (R14, ADR-12).

The point of these is that the evaluation can separate what GFW detected from what we detected: Phase 6
reports a GFW-only arm and a self-built-only arm, and neither can be read if every signal comes from GFW.
"""

from __future__ import annotations

import duckdb

from shadowfleet.util.store import glob_table, has_table


def by_cell(table: str, extra: str, con: duckdb.DuckDBPyConnection | None = None,
            cell_deg: float = 0.05) -> list[dict]:
    """Top 0.05-degree cells for a detection table, ranked by hours.

    This is the honest version of the Skagen anchorage polygon: the clusters come out of the data
    instead of a box drawn from a pilot chart. `extra` names the third column to report per cell.
    """
    from shadowfleet.ingest.dma import connect

    con = con or connect()
    rows = con.execute(f"""
        SELECT round(floor(lat / {cell_deg}) * {cell_deg}, 3) AS lat,
               round(floor(lon / {cell_deg}) * {cell_deg}, 3) AS lon,
               count(*) AS events, round(sum(hours), 1) AS hours, {extra}
        FROM read_parquet('{glob_table(table)}', hive_partitioning=true)
        GROUP BY 1, 2 ORDER BY hours DESC LIMIT 20
    """).fetchall()
    keys = ["lat", "lon", "events", "hours", extra.split(" AS ")[-1]]
    return [dict(zip(keys, r, strict=True)) for r in rows]


def run_all() -> dict:
    """Every detector, in dependency order: draught reads the STS table, churn reads Phase 3's outputs."""
    from shadowfleet import config
    from shadowfleet.detect import churn, draught, loitering, spoof, sts
    from shadowfleet.ingest.dma import connect
    from shadowfleet.resolve.identity import HULL_MAP
    from shadowfleet.util import probes, report

    if not (config.PARQUET_DIR / HULL_MAP).exists():
        raise SystemExit("no hull_map.parquet; run `make identity` first")
    con = connect()
    out = {"sts": sts.run(con), "loitering": loitering.run(con), "draught": draught.run(con),
           "spoof": spoof.run(con) if has_table("ais_artifacts") else {"skipped": "no ais_artifacts"},
           "churn": churn.run(con)}
    out["sts_cells"] = by_cell(sts.TABLE, "count(DISTINCT hull_a) AS hulls", con)
    out["loitering_cells"] = by_cell(loitering.TABLE, "count(DISTINCT hull_id) AS hulls", con)
    probes.write("detect", out)
    report.write_phase4b(out)
    return out
