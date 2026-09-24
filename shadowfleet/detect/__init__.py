"""Phase 4b: detection layers the project owns, built on DMA tracks (R14, ADR-12).

The point of these is that the evaluation can separate what GFW detected from what we detected: Phase 6
reports a GFW-only arm and a self-built-only arm, and neither can be read if every signal comes from GFW.
"""

from __future__ import annotations

from shadowfleet.util.store import has_table


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
    out["sts_cells"] = sts.by_cell(con)
    out["loitering_cells"] = loitering.by_cell(con)
    probes.write("detect", out)
    report.write_phase4b(out)
    return out
