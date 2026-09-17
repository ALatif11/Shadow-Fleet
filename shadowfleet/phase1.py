"""Phase 1 analysis over the ingested Parquet: population, type changes, gap evidence, STS readiness.

Everything here reads `data/parquet/` and writes tables or figures; nothing re-reads raw DMA files.
"""

from __future__ import annotations

import csv
import logging
from datetime import date
from pathlib import Path

import duckdb

from shadowfleet import config
from shadowfleet.ingest.dma import connect

log = logging.getLogger(__name__)
GRID_DEG = 0.1  # coverage-edge grid (phase1-prompt task 6)
GAP_HOURS = 6
EDGE_NEIGHBOURS = 8  # a cell with fewer occupied neighbours than this sits on the coverage boundary


def _rel(p: Path) -> str:
    """Repo-relative when possible; tests point the paths elsewhere."""
    try:
        return str(p.relative_to(config.REPO_ROOT))
    except ValueError:
        return str(p)


def _glob(table: str) -> str:
    return (config.PARQUET_DIR / table / "dt=*" / "*.parquet").as_posix()


def _has(table: str) -> bool:
    return any((config.PARQUET_DIR / table).glob("dt=*/*.parquet"))


def population(con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """One row per MMSI from `vessel_day` (already one row per MMSI per day), plus monthly counts."""
    con = con or connect()
    out = config.PARQUET_DIR / "population.parquet"
    con.execute(f"""
        COPY (
          SELECT mmsi,
            min(day) AS first_seen, max(day) AS last_seen, count(*) AS n_days_observed,
            sum(n_rows)::BIGINT AS n_rows, sum(n_pos_ok)::BIGINT AS n_pos_ok,
            bool_or(tanker_today) AS ever_tanker_class, bool_or(kept) AS ever_kept,
            count(*) FILTER (WHERE tanker_today) AS n_days_tanker_class,
            mode(ship_type) AS modal_ship_type, mode(cargo_type) AS modal_cargo_type,
            mode(length) AS modal_length, mode(width) AS modal_width,
            mode(imo) FILTER (WHERE imo > 0) AS modal_imo, mode(name) AS modal_name,
            sum(n_rows) FILTER (WHERE lower(mobile_type) = 'class b') / sum(n_rows) AS share_class_b
          FROM read_parquet('{_glob('vessel_day')}', hive_partitioning=true)
          GROUP BY mmsi ORDER BY mmsi
        ) TO '{out.as_posix()}' (FORMAT parquet, COMPRESSION zstd)
    """)
    monthly = con.execute(f"""
        SELECT strftime(day, '%Y-%m') AS month,
          count(DISTINCT mmsi) FILTER (WHERE tanker_today) AS tanker_mmsi,
          count(DISTINCT mmsi) FILTER (WHERE kept) AS kept_mmsi,
          count(DISTINCT mmsi) AS all_vessel_mmsi, count(DISTINCT day) AS days
        FROM read_parquet('{_glob('vessel_day')}', hive_partitioning=true)
        GROUP BY month ORDER BY month
    """).fetchall()
    csv_path = config.REPORTS_DIR / "phase1_population_by_month.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["month", "tanker_mmsi", "kept_mmsi", "all_vessel_mmsi", "days"])
        w.writerows(monthly)
    totals = con.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE ever_tanker_class), count(*) FILTER (WHERE modal_imo IS NOT NULL)
        FROM read_parquet('{out.as_posix()}')
    """).fetchone()
    return {"mmsi_total": totals[0], "mmsi_ever_tanker_class": totals[1], "mmsi_with_imo": totals[2],
            "months": len(monthly), "by_month_csv": _rel(csv_path)}


def type_changes(con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """Reported ship_type / cargo_type changes between consecutive static messages (phase1-prompt task 5)."""
    con = con or connect()
    out = config.PARQUET_DIR / "type_changes.parquet"
    con.execute(f"""
        COPY (
          WITH s AS (
            SELECT mmsi, observed_at, ship_type, cargo_type,
              lag(ship_type) OVER w AS prev_ship_type, lag(cargo_type) OVER w AS prev_cargo_type
            FROM read_parquet('{_glob('ais_static')}', hive_partitioning=true)
            WINDOW w AS (PARTITION BY mmsi ORDER BY observed_at)
          )
          SELECT mmsi, observed_at, prev_ship_type, ship_type, prev_cargo_type, cargo_type
          FROM s
          WHERE (prev_ship_type IS NOT NULL AND prev_ship_type IS DISTINCT FROM ship_type)
             OR (prev_cargo_type IS NOT NULL AND prev_cargo_type IS DISTINCT FROM cargo_type)
          ORDER BY mmsi, observed_at
        ) TO '{out.as_posix()}' (FORMAT parquet, COMPRESSION zstd)
    """)
    n, n_mmsi, n_left_tanker = con.execute(f"""
        SELECT count(*), count(DISTINCT mmsi),
          count(DISTINCT mmsi) FILTER (WHERE lower(prev_ship_type) = 'tanker'
                                         AND lower(ship_type) IS DISTINCT FROM 'tanker')
        FROM read_parquet('{out.as_posix()}')
    """).fetchone()
    return {"changes": n, "mmsi_with_change": n_mmsi, "mmsi_that_stopped_reporting_tanker": n_left_tanker}


def gap_evidence(con: duckdb.DuckDBPyConnection | None = None, sample: int = 500) -> dict:
    """Are long DMA gaps coverage artefacts? Compare gap-start positions against the coverage edge.

    Coverage edge is derived from the data itself: a 0.1-degree cell with fewer than EDGE_NEIGHBOURS
    occupied neighbours is a boundary cell. No external polygon needed.
    """
    con = con or connect()
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE cells AS
        SELECT CAST(floor(lat / {GRID_DEG}) AS INTEGER) AS cy, CAST(floor(lon / {GRID_DEG}) AS INTEGER) AS cx,
               count(*) AS n
        FROM read_parquet('{_glob('ais_dynamic')}', hive_partitioning=true)
        GROUP BY cy, cx
    """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE edge AS
        SELECT c.cy, c.cx,
          (SELECT count(*) FROM cells o
           WHERE o.cy BETWEEN c.cy - 1 AND c.cy + 1 AND o.cx BETWEEN c.cx - 1 AND c.cx + 1
             AND NOT (o.cy = c.cy AND o.cx = c.cx)) AS neighbours
        FROM cells c
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE gaps AS
        WITH d AS (
          SELECT mmsi, observed_at, lat, lon,
            lead(observed_at) OVER w AS next_at
          FROM read_parquet('{_glob('ais_dynamic')}', hive_partitioning=true)
          WINDOW w AS (PARTITION BY mmsi ORDER BY observed_at)
        )
        SELECT mmsi, observed_at AS gap_start, next_at AS gap_end,
          (epoch(next_at) - epoch(observed_at)) / 3600.0 AS gap_hours, lat, lon,
          CAST(floor(lat / {GRID_DEG}) AS INTEGER) AS cy, CAST(floor(lon / {GRID_DEG}) AS INTEGER) AS cx
        FROM d
        WHERE next_at IS NOT NULL AND epoch(next_at) - epoch(observed_at) > {GAP_HOURS * 3600}
    """)
    n_gaps, median_h, p90_h = con.execute(
        "SELECT count(*), median(gap_hours), quantile_cont(gap_hours, 0.9) FROM gaps"
    ).fetchone()
    at_edge = con.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE e.neighbours < {EDGE_NEIGHBOURS})
        FROM (SELECT * FROM gaps USING SAMPLE {sample} ROWS) g
        LEFT JOIN edge e USING (cy, cx)
    """).fetchone()
    share = at_edge[1] / at_edge[0] if at_edge[0] else None
    fig = _plot_gaps(con, n_gaps, share)
    return {"gaps_over_6h": n_gaps, "median_gap_hours": median_h, "p90_gap_hours": p90_h,
            "sampled": at_edge[0], "sampled_at_coverage_edge": at_edge[1],
            "share_at_coverage_edge": share, "figure": fig,
            "conclusion": ("DMA gaps are coverage artefacts, not evasion features"
                           if (share or 0) >= 0.5 else
                           "unexpected: most long gaps start inside coverage; investigate before Phase 5a")}


def _plot_gaps(con: duckdb.DuckDBPyConnection, n_gaps: int, share: float | None) -> str | None:
    if not n_gaps:
        return None
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    hours = [r[0] for r in con.execute(
        "SELECT gap_hours FROM gaps WHERE gap_hours < 400 USING SAMPLE 50000 ROWS").fetchall()]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(hours, bins=60)
    ax.set_xlabel("gap length (hours)")
    ax.set_ylabel("gaps")
    title = f"DMA gaps over {GAP_HOURS} h (n={n_gaps:,})"
    if share is not None:
        title += f"; {share:.0%} start at the coverage edge"
    ax.set_title(title)
    fig.tight_layout()
    out = config.REPORTS_DIR / "phase1_gaps.png"
    fig.savefig(out, dpi=120)
    plt.close(fig)
    return _rel(out)


def sts_readiness(day: date, con: duckdb.DuckDBPyConnection | None = None,
                  radius_m: int = 500, max_sog: float = 2.0, min_hours: float = 2.0) -> dict:
    """Count tanker pairs close and slow for long enough, in full-resolution vs downsampled rows.

    Pairs are counted by minute buckets that satisfy the distance and speed test; a pair qualifies when it
    has at least min_hours of such minutes. ponytail: minute-bucket count, not contiguous-run detection;
    Phase 4b does the real detector with run-length logic.
    """
    con = con or connect()
    box = config.SKAGEN_ANCHORAGE_BBOX
    res = {}
    for table in ("ais_fullres", "ais_dynamic"):
        part = config.PARQUET_DIR / table / f"dt={day.isoformat()}" / "part-0.parquet"
        if not part.exists():
            res[table] = None
            continue
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE slow AS
            SELECT mmsi, date_trunc('minute', observed_at) AS minute,
                   avg(lat) AS lat, avg(lon) AS lon
            FROM read_parquet('{part.as_posix()}')
            WHERE sog < {max_sog}
              AND lat BETWEEN {box['lat'][0]} AND {box['lat'][1]}
              AND lon BETWEEN {box['lon'][0]} AND {box['lon'][1]}
            GROUP BY mmsi, minute
        """)
        pairs = con.execute(f"""
            SELECT count(*) FROM (
              SELECT a.mmsi AS m1, b.mmsi AS m2, count(*) AS minutes
              FROM slow a JOIN slow b ON a.minute = b.minute AND a.mmsi < b.mmsi
              WHERE 2 * 6371000 * asin(sqrt(
                      pow(sin(radians(b.lat - a.lat) / 2), 2) +
                      cos(radians(a.lat)) * cos(radians(b.lat)) * pow(sin(radians(b.lon - a.lon) / 2), 2)
                    )) < {radius_m}
              GROUP BY m1, m2
              HAVING count(*) >= {int(min_hours * 60)}
            )
        """).fetchone()[0]
        res[table] = pairs
    return {"day": day.isoformat(), "radius_m": radius_m, "max_sog": max_sog, "min_hours": min_hours,
            "pairs_fullres": res["ais_fullres"], "pairs_downsampled": res["ais_dynamic"],
            "downsample_loses_pairs": (res["ais_fullres"] is not None and res["ais_dynamic"] is not None
                                       and res["ais_dynamic"] < res["ais_fullres"])}


def run_all(sts_day: date | None = None) -> dict:
    """Everything Phase 1 derives from the ingested tables. Missing inputs are reported, not fatal."""
    con = connect()
    out: dict = {}
    for name, fn, table in (("population", population, "vessel_day"),
                            ("type_changes", type_changes, "ais_static"),
                            ("gap_evidence", gap_evidence, "ais_dynamic")):
        if not _has(table):
            out[name] = {"error": f"no {table} partitions yet"}
            continue
        log.info("phase1", extra={"step": name})
        out[name] = fn(con)
    if sts_day is None:
        fullres = sorted(Path(config.PARQUET_DIR / "ais_fullres").glob("dt=*")) if _has("ais_fullres") else []
        sts_day = date.fromisoformat(fullres[-1].name.removeprefix("dt=")) if fullres else None
    out["sts_readiness"] = sts_readiness(sts_day, con) if sts_day else {"error": "no ais_fullres day ingested"}
    return out
