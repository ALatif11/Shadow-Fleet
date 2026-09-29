"""Phase 3: stable hull ids, dated identity intervals, and the population cross-check.

Point-in-time rule for identity: a window's vote can only be used once the window has closed, otherwise
`features(hull, T)` for a T inside the window would read static messages from after T. So the vote is
cumulative (everything observed up to the end of that window) and takes effect the day after the window
ends. A vessel has no hull id for its first 30 days of life; every cutoff is at least FEATURE_WINDOW_DAYS
after the window start, so this costs a hull at most its first cutoff and never leaks.

The prompt specified a per-window vote with a warm-up. That leaked at any cutoff landing inside a vessel's
first window, which month-end cutoffs do, so the vote is cumulative here instead. Recorded in the report.

Attribution happens at the cutoff, not at the record (ADR-23). `hull_at(T)` maps every transmitter to the hull
it belongs to as known at the end of day T, and every record that transmitter wrote on or before T goes to
that hull. The first version stamped each record with the id in force when it was written, so a vessel
whose IMO vote resolved partway through a feature window appeared under two ids at one cutoff, and the
`syn:` half of a vessel listed before T stayed in the population, which rule 3 forbids. Attributing at T
uses nothing after T, so it is exactly as point-in-time, and it keeps the records from a vessel's first
window that the per-record version had to throw away.
"""

from __future__ import annotations

import logging
from datetime import date

import duckdb

from shadowfleet import config
from shadowfleet.ingest import mid
from shadowfleet.ingest.dma import connect
from shadowfleet.util import probes, report
from shadowfleet.util.ids import imo_valid_sql
from shadowfleet.util.store import glob_table, has_table, rel_path

log = logging.getLogger(__name__)

WINDOW_DAYS = 30  # phase-prompts Phase 3 task 1
MIN_SUPPORT = 0.60
MIN_IMO_DAYS = 5
# The prompt says "5 messages", but `ais_static` is change-point compressed at ingest (ADR-14): a hull that
# broadcasts the same IMO all month leaves one row per day, not one per message. Counting days instead keeps
# the threshold meaning what it says. `coverage_by_threshold` reports what the choice costs.
THRESHOLD_GRID = [(1, 0.60), (2, 0.60), (3, 0.60), (5, 0.60), (5, 0.80), (10, 0.60)]
HULL_MAP = "hull_map.parquet"
INTERVALS = "identity_intervals.parquet"

def _kept_days() -> str:
    """Kept MMSI-days as a subquery. A function, not a constant: tests repoint config.PARQUET_DIR, and a
    module-level f-string would bind the path at import time (the same trap as config.load_window)."""
    return (f"(SELECT mmsi, day FROM read_parquet('{glob_table('vessel_day')}', hive_partitioning=true)"
            f" WHERE kept) x")


def _window_start(con: duckdb.DuckDBPyConnection) -> date:
    return con.execute(f"SELECT min(day) FROM read_parquet('{glob_table('vessel_day')}', hive_partitioning=true)"
                       ).fetchone()[0]


def _mid_table(con: duckdb.DuckDBPyConnection) -> int:
    """MID -> ISO3 as a temp table. Empty until `make probe-mid` vendors mid.csv; flags are then null."""
    con.execute("CREATE OR REPLACE TEMP TABLE mid (mid INTEGER, iso3 VARCHAR)")
    rows = list(mid.load().items())
    if rows:
        con.executemany("INSERT INTO mid VALUES (?, ?)", rows)
    return len(rows)


def hull_map(con: duckdb.DuckDBPyConnection | None = None, window_days: int = WINDOW_DAYS,
             min_imo_days: int = MIN_IMO_DAYS, min_support: float = MIN_SUPPORT) -> dict:
    """Cumulative majority-vote IMO per (MMSI, window) -> `hull_map.parquet`.

    One vote per day an IMO was broadcast, counted over everything observed up to the end of the window.
    Support is the winner's share of those day-votes, so a Class B hull is not punished for part-A messages
    that carry no IMO field at all. See the module docstring for why the vote is cumulative.
    """
    con = con or connect()
    w0 = _window_start(con)
    valid = imo_valid_sql("s.imo")
    out = config.PARQUET_DIR / HULL_MAP
    wi = f"CAST(floor(datediff('day', DATE '{w0}', {{d}}) / {window_days}) AS INTEGER)"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE hm AS
        WITH vd AS (
          SELECT mmsi, {wi.format(d='day')} AS wi, count(*) AS n_days,
                 mode(length) AS vd_length, mode(width) AS vd_width
          FROM read_parquet('{glob_table('vessel_day')}', hive_partitioning=true)
          WHERE kept GROUP BY 1, 2
        ), s AS (
          SELECT mmsi, imo, observed_at, {wi.format(d='CAST(observed_at AS DATE)')} AS wi
          FROM read_parquet('{glob_table('ais_static')}', hive_partitioning=true) s
        ), votes AS (  -- one vote per day an IMO was broadcast, so ingest-time compression cannot skew it
          SELECT mmsi, wi, CAST(imo AS BIGINT) AS imo, count(DISTINCT CAST(observed_at AS DATE)) AS n
          FROM s WHERE {valid} GROUP BY 1, 2, 3
        ), grid AS (  -- every (mmsi, candidate IMO, window), so the running total carries past quiet windows
          SELECT w.mmsi, w.wi, p.imo, coalesce(v.n, 0) AS n
          FROM (SELECT DISTINCT mmsi, wi FROM vd) w
          JOIN (SELECT DISTINCT mmsi, imo FROM votes) p USING (mmsi)
          LEFT JOIN votes v ON v.mmsi = w.mmsi AND v.wi = w.wi AND v.imo = p.imo
        ), cum AS (
          SELECT mmsi, wi, imo, sum(n) OVER (PARTITION BY mmsi, imo ORDER BY wi) AS cum_votes FROM grid
        ), tot AS (
          SELECT mmsi, wi, sum(cum_votes) AS cum_total FROM cum GROUP BY 1, 2
        ), best AS (
          SELECT mmsi, wi, imo, cum_votes FROM cum
          QUALIFY row_number() OVER (PARTITION BY mmsi, wi ORDER BY cum_votes DESC, imo) = 1
        )
        SELECT vd.mmsi, vd.wi, vd.n_days, vd.vd_length, vd.vd_width,
          DATE '{w0}' + vd.wi * {window_days} AS window_start,
          DATE '{w0}' + (vd.wi + 1) * {window_days} - 1 AS window_end,
          coalesce(tot.cum_total, 0) AS n_imo_days, best.imo AS voted_imo, best.cum_votes AS n_votes,
          CASE WHEN tot.cum_total > 0 THEN best.cum_votes::DOUBLE / tot.cum_total END AS support,
          coalesce(tot.cum_total, 0) >= {min_imo_days}
            AND best.cum_votes::DOUBLE / tot.cum_total >= {min_support} AS by_imo
        FROM vd LEFT JOIN tot USING (mmsi, wi) LEFT JOIN best USING (mmsi, wi)
    """)
    # ponytail: no GFW-by-MMSI fallback. It only pays for itself if IMO coverage misses the 80 percent bar
    # in the report below, and leaving it out keeps identity free of any GFW-dated model (leakage test 5).
    con.execute(f"""
        COPY (
          SELECT mmsi, window_start, window_end,
            window_end + 1 AS effective_from,
            CASE WHEN coalesce(by_imo, false) THEN CAST(voted_imo AS VARCHAR)
                 -- Transmitter plus physical size, not name: a rename is an identity change to count
                 -- within one hull, not a reason to mint a second one. Size still separates two vessels
                 -- that reused one MMSI.
                 ELSE 'syn:' || substr(sha256(concat_ws('|', mmsi,
                        coalesce(CAST(vd_length AS VARCHAR), ''),
                        coalesce(CAST(vd_width AS VARCHAR), ''))), 1, 16) END AS hull_id,
            CASE WHEN coalesce(by_imo, false) THEN 'imo_majority' ELSE 'syn' END AS method,
            support, n_imo_days, n_days, voted_imo
          FROM hm ORDER BY mmsi, window_start
        ) TO '{out.as_posix()}' (FORMAT parquet, COMPRESSION zstd)
    """)
    n, n_mmsi, n_imo, n_hulls, n_syn = con.execute(f"""
        SELECT count(*), count(DISTINCT mmsi), count(*) FILTER (WHERE method = 'imo_majority'),
               count(DISTINCT hull_id), count(DISTINCT hull_id) FILTER (WHERE method = 'syn')
        FROM read_parquet('{out.as_posix()}')
    """).fetchone()
    return {"windows": n, "mmsi": n_mmsi, "windows_by_imo": n_imo, "hull_ids": n_hulls,
            "hull_ids_syn": n_syn, "window_days": window_days, "min_imo_days": min_imo_days,
            "min_support": min_support, "file": rel_path(out)}


def hull_at(T: date) -> str:
    """`(mmsi, hull_id)` as known at the end of day T: each transmitter's latest window whose vote had closed.

    A transmitter with no closed window by T (its first 30 days) has no row, so its records are left out of
    that cutoff and counted from the next one on. `effective_from` is the day after a window ends, so
    nothing here was decided with data from after T.
    """
    return (f"(SELECT mmsi, arg_max(hull_id, effective_from) AS hull_id"
            f" FROM read_parquet('{(config.PARQUET_DIR / HULL_MAP).as_posix()}')"
            f" WHERE effective_from <= DATE '{T}' GROUP BY mmsi)")


def at_cutoff(src: str, T: date) -> str:
    """Join `src` (aliased `x`, with an `mmsi` column) to the hull each transmitter maps to at T.

    Callers select `hm.hull_id` and filter `hm.hull_id IS NOT NULL`, the same shape as before ADR-23.
    """
    return f"{src} LEFT JOIN {hull_at(T)} hm ON x.mmsi = hm.mmsi"


def _as_of_record(src: str, ts_col: str) -> str:
    """The id in force when each record was written. Coverage reporting only.

    Features, population and evidence must go through `hull_at`/`at_cutoff` instead (ADR-23); a test fails if
    anything outside this module reaches for this.
    """
    return (f"{src} ASOF LEFT JOIN read_parquet('{(config.PARQUET_DIR / HULL_MAP).as_posix()}') hm"
            f" ON x.mmsi = hm.mmsi AND CAST(x.{ts_col} AS DATE) >= hm.effective_from")


def identity_intervals(con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """Dated (mmsi, name, callsign, flag) intervals from static messages, one lineage per transmitter.

    Keyed by MMSI, not by hull (ADR-23): which hull a transmitter belongs to depends on the cutoff, so the
    features join these to `hull_at(T)` rather than this table fixing an answer. Null fields are carried
    forward from the last message that had them, so a Class B part-A message with no name does not read as
    a name change. Phase 1 learned this the hard way: without the guard, `type_changes` reported 476,861
    changes, almost all null flapping.
    """
    con = con or connect()
    n_mid = _mid_table(con)
    out = config.PARQUET_DIR / INTERVALS
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE iv AS
        WITH src AS (
          SELECT x.mmsi, x.observed_at,
            nullif(upper(trim(regexp_replace(x.name, '\\s+', ' ', 'g'))), '') AS name_raw,
            nullif(upper(trim(x.callsign)), '') AS callsign_raw, mid.iso3 AS flag_raw
          FROM read_parquet('{glob_table('ais_static')}', hive_partitioning=true) x
          -- a 9-digit MMSI beginning 2-7 is a ship station; its first three digits are the MID
          LEFT JOIN mid ON length(CAST(x.mmsi AS VARCHAR)) = 9
            AND substr(CAST(x.mmsi AS VARCHAR), 1, 1) BETWEEN '2' AND '7'
            AND mid.mid = CAST(substr(CAST(x.mmsi AS VARCHAR), 1, 3) AS INTEGER)
        ), ff AS (
          SELECT mmsi, observed_at, hash(name_raw, callsign_raw, flag_raw) AS tb,
            last_value(name_raw IGNORE NULLS) OVER w AS name_normalised,
            last_value(callsign_raw IGNORE NULLS) OVER w AS callsign,
            last_value(flag_raw IGNORE NULLS) OVER w AS flag_iso3
          -- ties (same MMSI, same instant) are ordered by content so a rerun gives the same intervals
          FROM src WINDOW w AS (PARTITION BY mmsi ORDER BY observed_at, hash(name_raw, callsign_raw, flag_raw))
        ), h AS (
          SELECT *, hash(concat_ws('|', coalesce(name_normalised, ''), coalesce(callsign, ''),
                                   coalesce(flag_iso3, ''))) AS tup
          FROM ff
        ), c AS (
          SELECT *, lag(tup) OVER (PARTITION BY mmsi ORDER BY observed_at, tb) AS prev FROM h
        )
        SELECT mmsi, name_normalised, callsign, flag_iso3, observed_at AS "start",
               lead(observed_at) OVER (PARTITION BY mmsi ORDER BY observed_at, tb) AS "end"
        FROM c WHERE prev IS NULL OR prev <> tup
    """)
    con.execute(f"COPY (SELECT * FROM iv ORDER BY mmsi, \"start\") "
                f"TO '{out.as_posix()}' (FORMAT parquet, COMPRESSION zstd)")
    n, n_mmsi, n_multi, n_flag = con.execute("""
        SELECT count(*), count(DISTINCT mmsi),
          count(DISTINCT mmsi) FILTER (WHERE n > 1), count(*) FILTER (WHERE flag_iso3 IS NOT NULL)
        FROM (SELECT *, count(*) OVER (PARTITION BY mmsi) AS n FROM iv)
    """).fetchone()
    return {"intervals": n, "mmsis": n_mmsi, "mmsis_with_a_change": n_multi,
            "intervals_with_flag": n_flag, "mid_rows": n_mid, "file": rel_path(out)}


def coverage(con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """Share of kept MMSI-days whose as-of hull id is IMO-based (Phase 3 acceptance: >= 80 percent)."""
    con = con or connect()
    total, by_imo, unmapped = con.execute(f"""
        SELECT count(*), count(*) FILTER (WHERE hm.method = 'imo_majority'),
               count(*) FILTER (WHERE hm.hull_id IS NULL)
        FROM {_as_of_record(_kept_days(), 'day')}
    """).fetchone()
    mapped = total - unmapped
    return {"mmsi_days": total, "mmsi_days_by_imo": by_imo, "mmsi_days_unmapped": unmapped,
            "share_by_imo": round(by_imo / total, 4) if total else None,
            # unmapped days are the pre-first-window warm-up, not a resolver failure; both are reported
            "share_by_imo_of_mapped": round(by_imo / mapped, 4) if mapped else None}


def coverage_by_threshold(con: duckdb.DuckDBPyConnection | None = None) -> list[dict]:
    """What the (min IMO-days, min support) choice costs in MMSI-day coverage.

    The hull_map file is written at the configured thresholds; this re-scores the same votes at others so the
    report shows the trade-off instead of defending a guess.
    """
    con = con or connect()
    out = []
    for min_days, min_support in THRESHOLD_GRID:
        total, by_imo = con.execute(f"""
            SELECT count(*), count(*) FILTER (WHERE hm.n_imo_days >= {min_days}
                                                AND hm.support >= {min_support})
            FROM {_as_of_record(_kept_days(), 'day')}
        """).fetchone()
        out.append({"min_imo_days": min_days, "min_support": min_support,
                    "share_by_imo": round(by_imo / total, 4) if total else None,
                    "chosen": min_days == MIN_IMO_DAYS and min_support == MIN_SUPPORT})
    return out


def fragmentation(con: duckdb.DuckDBPyConnection | None = None, n: int = 20,
                  T: date | None = None) -> dict:
    """At cutoff T, does every transmitter that carried a designated IMO map to that one hull?

    Measured at a cutoff because that is where it matters (ADR-23): a vessel counted under two ids at one T
    is two rows in the population, and only the IMO-keyed one can be excluded by rule 3 or labelled. The
    first version unioned every id a transmitter ever had across the whole store, which reported 0 of 20
    designated IMOs as whole and was a measure of the old per-record attribution, not of the model's inputs.
    Defaults to the last day any vote took effect, the cutoff with the most history behind it.
    """
    con = con or connect()
    actions = config.PARQUET_DIR / "sanctions_actions.parquet"
    if not actions.exists():
        return {"skipped": "sanctions_actions.parquet missing; run `make labels` first"}
    hm = (config.PARQUET_DIR / HULL_MAP).as_posix()
    T = T or con.execute(f"SELECT max(effective_from) FROM read_parquet('{hm}')").fetchone()[0]
    rows = con.execute(f"""
        WITH listed AS (
          SELECT DISTINCT imo FROM read_parquet('{actions.as_posix()}') WHERE imo IS NOT NULL
        ), carriers AS (  -- transmitters that voted this IMO through in a window closed by T
          SELECT DISTINCT voted_imo AS imo, mmsi FROM read_parquet('{hm}')
          WHERE method = 'imo_majority' AND effective_from <= DATE '{T}'
            AND voted_imo IN (SELECT imo FROM listed)
        )
        SELECT c.imo, count(DISTINCT h.hull_id) AS n_hull_ids, count(DISTINCT c.mmsi) AS n_mmsi,
               list(DISTINCT h.hull_id) AS hull_ids
        FROM carriers c JOIN {hull_at(T)} h ON h.mmsi = c.mmsi
        GROUP BY c.imo ORDER BY n_hull_ids DESC, c.imo LIMIT {n}
    """).fetchall()
    frag = [{"imo": r[0], "n_hull_ids": r[1], "n_mmsi": r[2], "hull_ids": r[3][:5]} for r in rows if r[1] > 1]
    return {"cutoff": str(T), "checked": len(rows), "single_hull_id": len(rows) - len(frag),
            "fragmented": frag[:10]}


def silver_set(con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """IMO-MMSI pairs from OpenSanctions vessels vs what the resolver decided, where both are known."""
    con = con or connect()
    path = config.CACHE_DIR / "opensanctions" / "maritime.csv"
    if not path.exists():
        return {"skipped": f"{rel_path(path)} missing; run `make probe-opensanctions` first"}
    hm = (config.PARQUET_DIR / HULL_MAP).as_posix()
    cols = {c[0] for c in con.execute(f"DESCRIBE SELECT * FROM read_csv('{path.as_posix()}')").fetchall()}
    imo_col = next((c for c in ("imoNumber", "imo") if c in cols), None)
    mmsi_col = next((c for c in ("mmsi", "mmsiNumber") if c in cols), None)
    if not (imo_col and mmsi_col):
        return {"skipped": f"maritime.csv has no usable IMO/MMSI columns (saw {sorted(cols)[:12]})"}
    pairs, agree, resolved_other = con.execute(f"""
        WITH s AS (
          SELECT CAST(regexp_extract(CAST("{imo_col}" AS VARCHAR), '(\\d{{7}})', 1) AS BIGINT) AS imo,
                 CAST(regexp_extract(CAST("{mmsi_col}" AS VARCHAR), '(\\d{{9}})', 1) AS BIGINT) AS mmsi
          FROM read_csv('{path.as_posix()}', union_by_name=true)
        ), p AS (SELECT DISTINCT imo, mmsi FROM s WHERE imo IS NOT NULL AND mmsi IS NOT NULL),
        r AS (
          SELECT p.imo, p.mmsi, max(h.voted_imo = p.imo) AS ok, count(h.mmsi) AS n_windows
          FROM p LEFT JOIN read_parquet('{hm}') h ON h.mmsi = p.mmsi AND h.method = 'imo_majority'
          GROUP BY 1, 2
        )
        SELECT count(*) FILTER (WHERE n_windows > 0), count(*) FILTER (WHERE ok),
               count(*) FILTER (WHERE n_windows > 0 AND NOT ok) FROM r
    """).fetchone()
    return {"pairs_in_population": pairs, "agree": agree, "disagree": resolved_other,
            "precision": round(agree / pairs, 4) if pairs else None}


def population_crosscheck(con: duckdb.DuckDBPyConnection | None = None) -> dict:
    """Tanker-sized hulls that never reported a tanker type, and whether any were later designated."""
    con = con or connect()
    actions = config.PARQUET_DIR / "sanctions_actions.parquet"
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE xc AS
        SELECT mmsi, max(length) AS length, mode(name) AS name, mode(imo) AS imo, count(*) AS n_days
        FROM read_parquet('{glob_table('vessel_day')}', hive_partitioning=true)
        WHERE length >= {config.MIN_TANKER_LENGTH_M}
          AND (ship_type IS NULL OR lower(ship_type) IN ('undefined', 'unknown', 'other'))
        GROUP BY mmsi
        HAVING NOT bool_or(tanker_today)
    """)
    rows = con.execute("SELECT * FROM xc ORDER BY n_days DESC").fetchall()
    out = {"mmsi_tanker_sized_never_tanker_class": len(rows),
           "examples": [{"mmsi": r[0], "length": r[1], "name": r[2], "n_days": r[4]} for r in rows[:10]]}
    if actions.exists():
        out["of_those_later_designated"] = con.execute(f"""
            SELECT count(DISTINCT imo) FROM read_parquet('{actions.as_posix()}')
            WHERE action = 'add' AND imo IN (SELECT imo FROM xc WHERE imo IS NOT NULL)
        """).fetchone()[0]
    return out


def run_all(**hull_map_kwargs) -> dict:
    """Phase 3 end to end over the ingested Parquet."""
    for table in ("vessel_day", "ais_static"):
        if not has_table(table):
            raise SystemExit(f"no {table} parquet; run `make ingest-dma` first")
    con = connect()
    out = {"hull_map": hull_map(con, **hull_map_kwargs), "identity_intervals": identity_intervals(con),
           "coverage": coverage(con), "fragmentation": fragmentation(con), "silver_set": silver_set(con),
           "crosscheck": population_crosscheck(con),
           "coverage_by_threshold": coverage_by_threshold(con)}
    probes.write("identity", out)
    report.write_report("phase3", out)
    out["report"] = rel_path(config.REPORTS_DIR / "phase3.md")
    return out
