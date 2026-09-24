from __future__ import annotations

from datetime import timedelta

import pytest

from shadowfleet import config
from shadowfleet.detect import churn, draught, loitering, spoof, sts
from shadowfleet.ingest import dma
from shadowfleet.resolve import identity
from shadowfleet.util.store import glob_table
from tests.conftest import DAY, HEADER_V1, row, write_zip

SMALL = {"window_days": 2, "min_imo_days": 2}
IMO_A, IMO_B, IMO_C = "9074729", "9176187", "9179834"


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
    rows = con.execute(f"SELECT hull_a, hull_b, hours, min_distance_m FROM "
                       f"read_parquet('{glob_table(sts.TABLE)}', hive_partitioning=true) "
                       f"ORDER BY start").fetchall()
    # the far hull is never paired with anything, whichever id it is known by
    assert not any({"9179834", "mmsi:999"} & {r[0], r[1]} for r in rows)
    # once the first 2-day window has closed the pair is named by IMO, not by MMSI
    assert {IMO_A, IMO_B} in [{r[0], r[1]} for r in rows]
    assert all(r[2] >= 2.0 and 150 < r[3] < 260 for r in rows)
    assert sts.by_cell(con)[0]["candidates"] >= 1


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
    # each hull-day is its own stretch (the overnight gap exceeds the tolerance), and the first two days
    # are the resolver warm-up, so 3 hulls x 4 resolved days
    assert out["events"] == 12
    assert loitering.by_cell(con)[0]["hours"] > 12
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
    rows = con.execute(f"SELECT hull_id, delta_m, moored_between, sts_between FROM "
                       f"read_parquet('{glob_table(draught.TABLE)}', hive_partitioning=true) "
                       f"WHERE hull_id = '{IMO_A}'").fetchall()
    assert rows and rows[0][1] == pytest.approx(4.0)
    assert rows[0][2] is False and rows[0][3] is True
    assert out["coinciding_with_sts"] >= 1


def test_spoof_excess_washes_out_when_the_whole_cell_jumps(resolved):
    """A hull that jumps on a day when its cell's baseline is just as high scores no excess."""
    imos = {777: IMO_A, 888: IMO_B, 999: IMO_C}
    days = {}
    for off in range(6):
        t0 = (DAY + timedelta(days=off)).replace(hour=3)
        rows = []
        for mmsi, imo in imos.items():  # every vessel in the cell teleports once, every day
            rows += [row(t0, "Class A", mmsi, 55.10, 12.10, sog=8.0, name=f"J{mmsi}", imo=imo),
                     row(t0 + timedelta(seconds=60), "Class A", mmsi, 55.10, 13.90, sog=8.0,
                         name=f"J{mmsi}", imo=imo)]
        days[off] = rows
    _ingest(days, fullres=True)
    con = resolved()
    out = spoof.run(con)
    assert out["hull_days"] >= 1
    assert out["with_any_jump"] >= 1
    worst = con.execute(f"SELECT max(excess) FROM read_parquet('{glob_table(spoof.TABLE)}',"
                        f" hive_partitioning=true)").fetchone()[0]
    assert worst == pytest.approx(0.0, abs=1e-9)  # jump rate equals the cell baseline


def test_churn_records_the_mmsi_moving_under_one_hull(resolved):
    days = {}
    for off in range(9):
        mmsi = 219000111 if off < 4 else 626000111
        t = (DAY + timedelta(days=off)).replace(hour=5)
        days[off] = [row(t + timedelta(seconds=s), "Class A", mmsi, 57.0, 10.0 + s * 0.001,
                         sog=8.0, name="ALPHA", imo=IMO_A) for s in range(6)]
    _ingest(days)
    con = resolved()
    out = churn.run(con)
    assert out["by_kind"].get("mmsi_under_hull") == 1


def test_run_all_writes_the_report(resolved):
    _ingest({off: _pair(off, hours=3, metres_apart=200) for off in range(6)}, fullres=True)
    resolved()
    import shadowfleet.detect as det

    out = det.run_all()
    text = (config.REPORTS_DIR / "phase4b.md").read_text()
    assert "Phase 4b report" in text and "STS candidates" in text
    assert out["sts"]["candidates"] >= 1 and "skipped" not in out["spoof"]
