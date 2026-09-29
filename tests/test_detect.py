from __future__ import annotations

from datetime import timedelta

import pytest

import shadowfleet.detect as det
from shadowfleet import config
from shadowfleet.detect import churn, draught, loitering, spoof, sts
from shadowfleet.ingest import dma
from shadowfleet.resolve import identity
from shadowfleet.util.ids import imo_valid
from shadowfleet.util.store import glob_table
from tests.conftest import DAY, HEADER_V1, row, valid_imos, write_zip

SMALL = {"window_days": 2, "min_imo_days": 2}
IMO_A, IMO_B, IMO_C = valid_imos(3)


def _ingest(days: dict[int, list[dict]], fullres: bool = False) -> None:
    config.DMA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    for off, rows in days.items():
        d = (DAY + timedelta(days=off)).date()
        z = write_zip(config.DMA_RAW_DIR / f"{d}.zip", rows, HEADER_V1)
        res = dma.ingest_zip(z, [d], z.name, keep_fullres={d} if fullres else None)
        assert not res.days_failed


def _pair(off: int, *, hours: float, metres_apart: float, sog: float = 0.4,
          nav: str = "Under way using engine") -> list[dict]:
    """Two tankers `metres_apart` for `hours`, both at `sog`, plus a third 5 km away as a control."""
    t0 = (DAY + timedelta(days=off)).replace(hour=2)
    dlat = metres_apart / 111_320.0
    out = []
    for s in range(0, int(hours * 3600), 30):
        t = t0 + timedelta(seconds=s)
        out += [row(t, "Class A", 777, 57.70, 10.70, sog=sog, name="STS-A", imo=IMO_A),
                row(t, "Class A", 888, 57.70 + dlat, 10.70, sog=sog, name="STS-B", imo=IMO_B),
                row(t, "Class A", 999, 57.75, 10.70, sog=sog, name="FAR", imo=IMO_C)]
    for r in out:
        r["Navigational status"] = nav
    return out


@pytest.fixture()
def resolved(tmp_data):
    """Phase 3 outputs exist, so the detectors can attach a hull id."""
    def _go():
        con = dma.connect()
        identity.hull_map(con, **SMALL)
        identity.identity_intervals(con)
        return con
    return _go


def test_sts_fires_on_a_long_close_slow_pair(resolved):
    _ingest({off: _pair(off, hours=3, metres_apart=200) for off in range(6)})
    con = resolved()
    out = sts.run(con)
    assert out["candidates"] >= 1
    rows = con.execute(f"SELECT mmsi_a, mmsi_b, hours, min_distance_m FROM "
                       f"read_parquet('{glob_table(sts.TABLE)}', hive_partitioning=true) "
                       f"ORDER BY start").fetchall()
    # keyed by transmitter, never by hull (ADR-23): which hulls 777 and 888 are is decided at each cutoff
    assert not any(999 in {r[0], r[1]} for r in rows), "the far transmitter is never paired"
    assert {777, 888} in [{r[0], r[1]} for r in rows]
    assert all(r[2] >= 2.0 and 150 < r[3] < 260 for r in rows)
    assert det.by_cell(sts.TABLE, "count(DISTINCT mmsi_a) AS mmsis", con)[0]["events"] >= 1


def test_sts_does_not_fire_on_the_near_miss(resolved):
    """The case the Phase 4b prompt names: 400 m for 1.5 h. Close enough, not long enough."""
    _ingest({off: _pair(off, hours=1.5, metres_apart=400) for off in range(6)})
    assert sts.run(resolved())["candidates"] == 0


def test_sts_ignores_a_pair_that_is_moored(resolved):
    _ingest({off: _pair(off, hours=3, metres_apart=200, nav="Moored") for off in range(6)})
    assert sts.run(resolved())["candidates"] == 0


def test_sts_ignores_a_pair_that_is_not_slow(resolved):
    _ingest({off: _pair(off, hours=3, metres_apart=200, sog=6.0) for off in range(6)})
    assert sts.run(resolved())["candidates"] == 0


def test_loitering_fires_past_twelve_hours_and_not_before(resolved):
    _ingest({off: _pair(off, hours=13, metres_apart=3000, sog=0.3) for off in range(6)})
    con = resolved()
    out = loitering.run(con)
    # each transmitter-day is its own stretch (the overnight gap exceeds the tolerance): 3 x 6 days. Before
    # ADR-23 the first two days were the resolver warm-up and were dropped (12); attributing hulls at the
    # cutoff instead lets the detector keep them
    assert out["events"] == 18
    assert det.by_cell(loitering.TABLE, "count(DISTINCT mmsi) AS mmsis", con)[0]["hours"] > 12
    # run() rewrites the table, so this has to come last
    assert loitering.run(con, min_hours=20.0)["events"] == 0


def test_draught_change_is_flagged_and_an_sts_between_is_recorded(resolved):
    # days 0-3 light, day 4 an STS, day 5 four metres deeper, with no berth call anywhere
    days = {off: _pair(off, hours=1, metres_apart=3000) for off in range(4)}
    days[4] = _pair(4, hours=3, metres_apart=200)
    days[5] = _pair(5, hours=1, metres_apart=3000)
    for r in days[5]:
        r["Draught"] = "14.0"
    _ingest(days)
    con = resolved()
    sts.run(con)
    out = draught.run(con)
    assert out["changes"] >= 1
    rows = con.execute(f"SELECT mmsi, delta_m, moored_between, sts_between FROM "
                       f"read_parquet('{glob_table(draught.TABLE)}', hive_partitioning=true) "
                       f"WHERE mmsi = 777").fetchall()
    assert rows and rows[0][1] == pytest.approx(4.0)
    assert rows[0][2] is False and rows[0][3] is True
    assert out["coinciding_with_sts"] >= 1


def test_two_draughts_at_one_instant_are_not_a_change(resolved):
    # One MMSI at a steady 10 m, and at one instant a second voice on the MMSI saying 13 m. Tied rows come
    # back in arbitrary order, so this counted as a change a varying way per run (63,726 vs 63,609 on Sep 29).
    days = {}
    for off in range(3):
        t = (DAY + timedelta(days=off)).replace(hour=5)
        days[off] = [row(t + timedelta(hours=h), "Class A", 219000333, 57.0, 10.0, sog=8.0, name="CHARLIE")
                     for h in range(4)]
    _ingest(days)
    con = resolved()
    day1 = (DAY + timedelta(days=1)).date()
    part = config.PARQUET_DIR / "ais_static" / f"dt={day1}"
    first = sorted(part.glob("*.parquet"))[0].as_posix()
    con.execute(f"COPY (SELECT * REPLACE (13.0 AS draught) FROM read_parquet('{first}') WHERE mmsi = 219000333"
                f" ORDER BY observed_at LIMIT 1) TO '{(part / 'tie.parquet').as_posix()}' (FORMAT parquet)")
    assert draught.run(con)["changes"] == 0


def test_spoof_excess_is_an_incidence_difference_not_a_row_rate(resolved):
    """Two 0.5-degree cells, four hulls each, every hull staying inside its own cell all day.

    Cell B: all four jump, so each one is doing exactly what its cell does and the excess is zero.
    Cell A: one of four jumps, so the baseline is 0.25 and the jumper stands out by 0.75 while the three
    quiet hulls sit at -0.25. Those numbers are only reachable if both sides of the subtraction are
    per-vessel incidences; a per-row jump rate cannot produce them.
    """
    imos, v = [], 9300000
    while len(imos) < 8:  # computed, not typed: an invalid check digit would land the hull on a syn id
        if imo_valid(v):
            imos.append(str(v))
        v += 1
    cell_a, cell_b = imos[:4], imos[4:]
    days = {}
    for off in range(6):
        t0 = (DAY + timedelta(days=off)).replace(hour=3)
        rows = []
        for idx, imo in enumerate(imos):
            in_a = imo in cell_a
            mmsi = 219000100 + idx
            lat, lon = (55.10, 12.10) if in_a else (56.10, 13.10)
            jumps = (not in_a) or idx == 0  # everyone in B, only the first hull in A
            # a slow crawl, so nothing jumps by accident; both positions stay in the same 0.5 degree cell
            rows += [row(t0 + timedelta(minutes=k), "Class A", mmsi, lat, lon + k * 0.001, sog=9.0,
                         name=f"S{idx}", imo=imo) for k in range(20)]
            if jumps:  # 19.8 km in 60 s, still inside the cell
                rows += [row(t0 + timedelta(hours=1), "Class A", mmsi, lat, lon, sog=9.0,
                             name=f"S{idx}", imo=imo),
                         row(t0 + timedelta(hours=1, seconds=60), "Class A", mmsi, lat, lon + 0.31,
                             sog=9.0, name=f"S{idx}", imo=imo)]
        days[off] = rows
    _ingest(days)
    con = resolved()
    spoof.run(con)
    by_mmsi = dict(con.execute(f"""
        SELECT mmsi, round(avg(excess), 6) FROM read_parquet('{glob_table(spoof.TABLE)}',
               hive_partitioning=true) GROUP BY mmsi
    """).fetchall())
    got = {imo: by_mmsi[219000100 + idx] for idx, imo in enumerate(imos)}  # per transmitter (ADR-23)
    for imo in cell_b:
        assert got[imo] == pytest.approx(0.0, abs=1e-6), f"{imo} matches its cell exactly"
    assert got[cell_a[0]] == pytest.approx(0.75, abs=1e-6)  # jumps where only a quarter of the cell does
    for imo in cell_a[1:]:
        assert got[imo] == pytest.approx(-0.25, abs=1e-6)  # quiet in a cell where someone else jumps


def test_churn_records_the_mmsi_moving_under_one_hull(resolved):
    days = {}
    for off in range(9):
        mmsi = 219000111 if off < 4 else 626000111
        t = (DAY + timedelta(days=off)).replace(hour=5)
        days[off] = [row(t + timedelta(seconds=s), "Class A", mmsi, 57.0, 10.0 + s * 0.001,
                         sog=8.0, name="ALPHA", imo=IMO_A) for s in range(6)]
    _ingest(days)
    con = resolved()
    churn.run(con)
    # The MMSI moving under one hull is only knowable once hulls are assigned at a cutoff (ADR-23), so it
    # is counted by the feature store, not stored in the churn table. At the last day both transmitters have
    # voted IMO_A through, so they are one hull and it has used one MMSI beyond its first.
    from shadowfleet.features import asof

    T = (DAY + timedelta(days=8)).date()
    row_a = next(r for r in asof.features(T, con) if r["hull_id"] == IMO_A)
    assert row_a["n_mmsi_imo_churn"] == 1


def test_churn_records_the_imo_moving_under_one_mmsi(resolved):
    # IMO_A for 6 days, a one-day IMO_C blip, then IMO_B for 6 days, all on one MMSI. The lifetime vote in
    # hull_map never flips to IMO_B here, which is why churn reads the broadcasts (0 changes on real data).
    days = {}
    for off in range(13):
        imo = IMO_A if off < 6 else IMO_C if off == 6 else IMO_B
        t = (DAY + timedelta(days=off)).replace(hour=5)
        days[off] = [row(t + timedelta(seconds=s), "Class A", 219000222, 57.0, 10.0 + s * 0.001,
                         sog=8.0, name="BRAVO", imo=imo) for s in range(6)]
    _ingest(days)
    con = resolved()
    assert churn.run(con, min_days=5)["changes"] == 1
    old, new, at = con.execute(f"SELECT old_value, new_value, observed_at FROM"
                               f" read_parquet('{glob_table(churn.TABLE)}')").fetchone()
    assert (old, new) == (str(IMO_A), str(IMO_B))
    assert at.date() == (DAY + timedelta(days=11)).date()  # IMO_B's 5th day, not its 1st


def test_run_all_writes_the_report(resolved):
    _ingest({off: _pair(off, hours=3, metres_apart=200) for off in range(6)}, fullres=True)
    resolved()
    out = det.run_all()
    text = (config.REPORTS_DIR / "phase4b.md").read_text()
    assert "Phase 4b report" in text and "STS candidates" in text
    assert out["sts"]["candidates"] >= 1 and "skipped" not in out["spoof"]
