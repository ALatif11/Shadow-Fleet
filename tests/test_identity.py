from __future__ import annotations

import csv
from datetime import timedelta

import duckdb
import pytest

from shadowfleet import config
from shadowfleet.features import identity as feat
from shadowfleet.ingest import dma, mid
from shadowfleet.resolve import identity
from shadowfleet.util.ids import imo_valid, imo_valid_sql
from tests.conftest import DAY, HEADER_V1, row, write_zip

# 219 = Denmark, 636 = Liberia, 341 = St Kitts and Nevis (on config.CONVENIENCE_FLAGS? no) -> use 636/GAB.
DK, LR, GAB = 219000111, 636000111, 626000111
IMO_A = "9074729"
# Two-day windows and a two-day vote so the fixtures stay small; the rule under test is the same one.
SMALL = {"window_days": 2, "min_imo_days": 2}


def test_imo_valid_sql_matches_the_python_check_digit():
    con = duckdb.connect()
    vals = [9074729, 9176187, 1234567, 123456, 0, 9402263, 12345678, 9074728, None]
    for v, ok in con.execute(f"SELECT v, {imo_valid_sql('v')} FROM (SELECT unnest($1) AS v) t",
                             [vals]).fetchall():
        assert bool(ok) is imo_valid(v), v


@pytest.fixture()
def mid_csv(tmp_data):
    config.MID_CSV = tmp_data / "mid.csv"
    with open(config.MID_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["mid", "itu_name", "iso3"])
        w.writerows([[219, "Denmark", "DNK"], [636, "Liberia", "LBR"], [626, "Gabon", "GAB"]])
    mid.load.cache_clear()
    yield
    mid.load.cache_clear()


def _ingest(days: dict[int, list[dict]]) -> None:
    """days: {day offset from DAY -> rows}. One zip per day, as the real ingest sees them."""
    config.DMA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    for off, rows in days.items():
        d = (DAY + timedelta(days=off)).date()
        z = write_zip(config.DMA_RAW_DIR / f"{d}.zip", rows, HEADER_V1)
        assert not dma.ingest_zip(z, [d], z.name).days_failed


def _day(off: int, mmsi: int, imo: str, name: str, hour: int = 10) -> list[dict]:
    t = (DAY + timedelta(days=off)).replace(hour=hour)
    return [row(t + timedelta(seconds=s), "Class A", mmsi, 57.0, 10.0 + s * 0.001, sog=8.0,
                name=name, imo=imo) for s in range(6)]


def test_hull_map_votes_the_imo_through_and_falls_back_to_a_synthetic_id(mid_csv):
    # DK broadcasts a good IMO every day; LR broadcasts one that fails the check digit, so it must land on a syn id.
    days = {off: _day(off, DK, IMO_A, "ALPHA") + _day(off, LR, "1234568", "BETA") for off in range(6)}
    _ingest(days)
    con = dma.connect()
    out = identity.hull_map(con, **SMALL)
    assert out["mmsi"] == 2
    rows = con.execute(f"SELECT mmsi, hull_id, method, n_imo_days FROM "
                       f"'{(config.PARQUET_DIR / identity.HULL_MAP).as_posix()}' "
                       f"QUALIFY row_number() OVER (PARTITION BY mmsi ORDER BY window_start DESC) = 1"
                       ).fetchall()
    by_mmsi = {r[0]: r for r in rows}
    assert by_mmsi[DK][1] == IMO_A and by_mmsi[DK][2] == "imo_majority"
    assert by_mmsi[LR][1].startswith("syn:") and by_mmsi[LR][2] == "syn"
    # the first window has not closed yet, so those MMSI-days are deliberately unmapped
    cov = identity.coverage(con)
    assert cov["mmsi_days_unmapped"] == 4 and cov["share_by_imo_of_mapped"] == 0.5


def test_intervals_record_a_name_change_but_not_a_missing_field(mid_csv):
    days = {off: _day(off, DK, IMO_A, "ALPHA" if off < 4 else "BRAVO") for off in range(7)}
    # a static message that omits the name entirely must not read as a change
    days[6] = _day(6, DK, IMO_A, "") + _day(6, DK, IMO_A, "BRAVO", hour=12)
    _ingest(days)
    con = dma.connect()
    identity.hull_map(con, **SMALL)
    out = identity.identity_intervals(con)
    names = con.execute(f"SELECT name_normalised, flag_iso3 FROM "
                        f"'{(config.PARQUET_DIR / identity.INTERVALS).as_posix()}' ORDER BY \"start\""
                        ).fetchall()
    # a value counts from its 2nd day (MIN_VALUE_DAYS), so day 0 is an interval with no name yet
    assert [n for n, _ in names if n] == ["ALPHA", "BRAVO"]
    assert names[0][1] == "DNK"  # flag from the ITU MID of the MMSI
    assert out["mmsis_with_a_change"] == 1


def test_two_names_at_one_instant_are_not_a_rename(mid_csv):
    # A steady ALPHA, and at one instant a second voice on the MMSI saying DELTA. That instant must not read
    # as ALPHA -> DELTA -> ALPHA, and must not depend on the order tied rows come back in.
    _ingest({off: _day(off, DK, IMO_A, "ALPHA") for off in range(3)})
    con = dma.connect()
    part = config.PARQUET_DIR / "ais_static" / f"dt={(DAY + timedelta(days=1)).date()}"
    first = sorted(part.glob("*.parquet"))[0].as_posix()
    con.execute(f"COPY (SELECT * REPLACE ('DELTA' AS name) FROM read_parquet('{first}') ORDER BY observed_at"
                f" LIMIT 1) TO '{(part / 'tie.parquet').as_posix()}' (FORMAT parquet)")
    identity.hull_map(con, **SMALL)
    out = identity.identity_intervals(con)
    names = [r[0] for r in con.execute(f"SELECT name_normalised FROM '{(config.PARQUET_DIR / identity.INTERVALS)}'"
                                       f" ORDER BY \"start\"").fetchall()]
    assert [n for n in names if n] == ["ALPHA"] and out["changes"]["name"] == 0


def test_placeholders_and_one_day_values_are_not_identity_changes(mid_csv):
    # callsign alternates with DMA's "Unknown" placeholder, one day carries a garbled name, a garbled name with
    # a symbol repeats on two days, and one day pads the real name with AIS '@'s
    days = {}
    for off in range(6):
        rows = _day(off, DK, IMO_A, {2: "ALPHX", 4: "ALP%A", 5: "ALP%A"}.get(off, "ALPHA@@@" if off == 3 else "ALPHA"))
        for i, r in enumerate(rows):
            r["Callsign"] = "Unknown" if i % 2 else "ABCD"
        days[off] = rows
    _ingest(days)
    con = dma.connect()
    identity.hull_map(con, **SMALL)
    out = identity.identity_intervals(con)
    assert out["changes"]["name"] == 0 and out["changes"]["callsign"] == 0


def test_name_changes_count_names_not_turns(mid_csv):
    # two units on one MMSI taking turns, ALPHA and BRAVO, every other day: one extra name, not five renames
    _ingest({off: _day(off, DK, IMO_A, "ALPHA" if off % 2 else "BRAVO") for off in range(8)})
    con = dma.connect()
    identity.hull_map(con, **SMALL)
    identity.identity_intervals(con)
    rows = feat.features((DAY + timedelta(days=7)).date(), con)
    assert rows[0]["n_name_changes"] == 1


def test_identity_features_are_the_same_from_a_store_truncated_at_T(mid_csv):
    """Phase 5a leakage test (a), for the identity family."""
    days = {off: _day(off, DK, IMO_A, "ALPHA" if off < 4 else "BRAVO") for off in range(9)}
    _ingest(days)
    con = dma.connect()
    identity.hull_map(con, **SMALL)
    identity.identity_intervals(con)
    T = (DAY + timedelta(days=7)).date()
    live = feat.features(T, con)
    assert live and live[0]["hull_id"] == IMO_A and live[0]["n_name_changes"] == 1

    # physically truncate: drop every parquet partition after T, rebuild, recompute
    for part in config.PARQUET_DIR.glob("*/dt=*"):
        if part.name > f"dt={T}":
            for f in part.glob("*"):
                f.unlink()
            part.rmdir()
    con2 = dma.connect()
    identity.hull_map(con2, **SMALL)
    identity.identity_intervals(con2)
    assert feat.features(T, con2) == live


def test_features_count_a_flag_change_into_a_convenience_registry(mid_csv):
    assert "GAB" in config.CONVENIENCE_FLAGS
    # same hull (same IMO), MMSI moves from a Danish to a Gabonese MID -> flag + mmsi change
    days = {off: _day(off, DK if off < 4 else GAB, IMO_A, "ALPHA") for off in range(9)}
    _ingest(days)
    con = dma.connect()
    identity.hull_map(con, **SMALL)
    identity.identity_intervals(con)
    f = feat.features((DAY + timedelta(days=8)).date(), con)[0]
    assert f["hull_id"] == IMO_A  # one hull across the MMSI change, which is the point of the vote
    assert f["n_flag_changes"] == 1 and f["n_mmsi_changes"] == 1
    assert f["flag_to_convenience_registry"] is True and f["current_flag"] == "GAB"


def test_run_all_writes_the_report(mid_csv):
    _ingest({off: _day(off, DK, IMO_A, "ALPHA") for off in range(6)})
    out = identity.run_all(**SMALL)
    text = (config.REPORTS_DIR / "phase3.md").read_text()
    assert "Phase 3 report" in text and "What the thresholds cost" in text
    assert out["coverage"]["share_by_imo_of_mapped"] == 1.0
    assert out["silver_set"].get("skipped") and out["fragmentation"].get("skipped")
