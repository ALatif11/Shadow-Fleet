"""Evidence bundles for (hull, T): the only thing a brief is allowed to state.

Every record gets an id (`E1`, `E2`, ...) and the generator must cite one for each claim, which is what
makes faithfulness checkable rather than a matter of taste. Dates are ISO strings and positions are rounded
to two decimals, because a model that has to reproduce `57.7031` will invent digits; two decimals is about
a kilometre, which is all a brief needs.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import duckdb

from shadowfleet import config
from shadowfleet.detect import churn, draught, loitering, spoof, sts
from shadowfleet.features.asof import FEATURES
from shadowfleet.ingest.dma import connect
from shadowfleet.resolve.identity import INTERVALS, hull_at
from shadowfleet.util.store import glob_table, has_table

TOP_FEATURES = 8  # phase-prompts Phase 8 task 1
MAX_RECORDS_PER_FAMILY = 6
# Least important first: when a bundle is over budget these families lose records before the others do,
# because a brief that drops a spoof-artefact day is still a brief, one that drops the STS is not.
TRUNCATION_ORDER = ("spoof", "loitering", "churn", "draught", "sts", "identity")
TOKEN_BUDGET = 3000
CHARS_PER_TOKEN = 4  # crude on purpose; the real check is that the bundle fits the 8k context with room


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _window(T: date) -> tuple[str, str]:
    start = T - timedelta(days=config.FEATURE_WINDOW_DAYS)
    return f"TIMESTAMP '{start} 00:00:00'", f"TIMESTAMP '{T} 23:59:59'"


def _iso(v: Any) -> Any:
    return v.isoformat() if hasattr(v, "isoformat") else v


def evidence(hull_id: str, T: date, con: duckdb.DuckDBPyConnection | None = None) -> dict[str, list[dict]]:
    """The concrete records behind this hull's features, by family. Nothing dated after T."""
    con = con or connect()
    lo, hi = _window(T)
    h = hull_id.replace("'", "''")
    # The transmitters this hull is made of at T (ADR-23), so the evidence is exactly what the features read.
    mine = f"(SELECT mmsi FROM {hull_at(T)} WHERE hull_id = '{h}')"
    out: dict[str, list[dict]] = {}

    if (config.PARQUET_DIR / INTERVALS).exists():
        out["identity"] = _rows(con, f"""
            SELECT 'identity_interval' AS kind, "start" AS at, mmsi, name_normalised AS name,
                   callsign, flag_iso3 AS flag
            FROM read_parquet('{(config.PARQUET_DIR / INTERVALS).as_posix()}')
            WHERE mmsi IN {mine} AND "start" <= {hi} ORDER BY "start" DESC LIMIT {MAX_RECORDS_PER_FAMILY}
        """)
    if has_table(sts.TABLE):
        out["sts"] = _rows(con, f"""
            SELECT 'sts_candidate' AS kind, s.observed_at AS at,
                   p.hull_id AS partner_hull,
                   round(s.hours, 1) AS hours, round(s.min_distance_m) AS min_distance_m,
                   round(s.lat, 2) AS lat, round(s.lon, 2) AS lon
            FROM read_parquet('{glob_table(sts.TABLE)}', hive_partitioning=true) s
            -- the partner is whichever transmitter is not ours, named by the hull it is at T
            JOIN {hull_at(T)} p
              ON p.mmsi = CASE WHEN s.mmsi_a IN {mine} THEN s.mmsi_b ELSE s.mmsi_a END
            WHERE (s.mmsi_a IN {mine} OR s.mmsi_b IN {mine}) AND p.hull_id <> '{h}'
              AND s.observed_at BETWEEN {lo} AND {hi}
            ORDER BY s.hours DESC LIMIT {MAX_RECORDS_PER_FAMILY}
        """)
    if has_table(loitering.TABLE):
        out["loitering"] = _rows(con, f"""
            SELECT 'loitering' AS kind, observed_at AS at, round(hours, 1) AS hours,
                   round(lat, 2) AS lat, round(lon, 2) AS lon, modal_nav_status AS nav_status
            FROM read_parquet('{glob_table(loitering.TABLE)}', hive_partitioning=true)
            WHERE mmsi IN {mine} AND observed_at BETWEEN {lo} AND {hi}
            ORDER BY hours DESC LIMIT {MAX_RECORDS_PER_FAMILY}
        """)
    if has_table(draught.TABLE):
        out["draught"] = _rows(con, f"""
            SELECT 'draught_change' AS kind, observed_at AS at, prev_draught, draught,
                   round(delta_m, 1) AS delta_m, moored_between, sts_between
            FROM read_parquet('{glob_table(draught.TABLE)}', hive_partitioning=true)
            WHERE mmsi IN {mine} AND observed_at BETWEEN {lo} AND {hi}
            ORDER BY abs(delta_m) DESC LIMIT {MAX_RECORDS_PER_FAMILY}
        """)
    if has_table(spoof.TABLE):
        out["spoof"] = _rows(con, f"""
            SELECT 'spoof_day' AS kind, observed_at AS at, day, n_jumps,
                   round(expected_incidence, 3) AS expected_incidence, round(excess, 3) AS excess
            FROM read_parquet('{glob_table(spoof.TABLE)}', hive_partitioning=true)
            WHERE mmsi IN {mine} AND day BETWEEN DATE '{T - timedelta(days=config.FEATURE_WINDOW_DAYS)}'
              AND DATE '{T}' AND n_jumps > 0
            ORDER BY excess DESC LIMIT {MAX_RECORDS_PER_FAMILY}
        """)
    if has_table(churn.TABLE):
        out["churn"] = _rows(con, f"""
            SELECT 'churn' AS kind, observed_at AS at, kind AS change_kind, old_value, new_value
            FROM read_parquet('{glob_table(churn.TABLE)}', hive_partitioning=true)
            WHERE mmsi IN {mine} AND observed_at <= {hi}
            ORDER BY observed_at DESC LIMIT {MAX_RECORDS_PER_FAMILY}
        """)
    # GFW events belong to the IMO they were fetched for, not to a transmitter: their value is behaviour
    # outside Danish waters, often under MMSIs DMA never saw (Phase 4a). A syn hull has no IMO, so none.
    if has_table("gfw_events") and hull_id.isdigit():
        out["gfw"] = _rows(con, f"""
            SELECT event_type AS kind, observed_at AS at, round(duration_h, 1) AS hours,
                   round(lat, 2) AS lat, round(lon, 2) AS lon
            FROM read_parquet('{glob_table('gfw_events')}', hive_partitioning=true)
            WHERE imo = {int(hull_id)} AND observed_at BETWEEN {lo} AND {hi}
            ORDER BY observed_at DESC LIMIT {MAX_RECORDS_PER_FAMILY}
        """)
    return {k: v for k, v in out.items() if v}


def build(hull_id: str, T: date, feature_row: dict, shap_top: list[dict] | None = None,
          con: duckdb.DuckDBPyConnection | None = None, budget: int = TOKEN_BUDGET) -> dict:
    """One bundle. `shap_top` is the hull's SHAP list from Phase 6; without it the drivers are omitted
    rather than guessed, because a brief citing invented drivers is worse than one with none."""

    records = evidence(hull_id, T, con)
    ids: dict[str, dict] = {}
    for family in sorted(records, key=lambda f: TRUNCATION_ORDER.index(f)
                         if f in TRUNCATION_ORDER else len(TRUNCATION_ORDER)):
        for rec in records[family]:
            ids[f"E{len(ids) + 1}"] = {"family": family, **{k: _iso(v) for k, v in rec.items()}}

    drivers = [{"feature": d["feature"], "value": d["value"], "contribution": d["contribution"],
                "description": FEATURES.get(d["feature"], ("", ""))[1]}
               for d in (shap_top or [])[:TOP_FEATURES]]
    bundle = {
        "hull_id": hull_id, "cutoff": T.isoformat(),
        "header": {k: _iso(feature_row.get(k)) for k in
                   ("current_flag", "vessel_age_years", "length_m", "n_days_observed", "n_transits")},
        "drivers": drivers, "evidence": ids,
    }
    # trim least-important families first until the bundle fits; recorded so the brief can say it was cut
    dropped: list[str] = []
    for family in TRUNCATION_ORDER:
        if len(json.dumps(bundle)) <= budget * CHARS_PER_TOKEN:
            break
        victims = [k for k, v in bundle["evidence"].items() if v["family"] == family]
        if victims:
            for k in victims:
                del bundle["evidence"][k]
            dropped.append(family)
    bundle["truncated_families"] = dropped
    bundle["approx_tokens"] = len(json.dumps(bundle)) // CHARS_PER_TOKEN
    return bundle
