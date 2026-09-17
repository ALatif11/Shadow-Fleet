from __future__ import annotations

from datetime import timedelta

import pytest

from shadowfleet import config, phase1
from shadowfleet.ingest import dma
from tests.conftest import DAY, HEADER_V1, row, synthetic_rows, write_zip

D = DAY.date()


def _sts_rows():
    """Two tankers 200 m apart in the Skagen box at 0.4 kn for 3 h, plus one 5 km away as a control."""
    t0 = DAY.replace(hour=2)
    out = []
    for s in range(0, 3 * 3600, 30):
        t = t0 + timedelta(seconds=s)
        out.append(row(t, "Class A", 777, 57.70, 10.70, sog=0.4, name="STS-A", imo="9074729"))
        out.append(row(t, "Class A", 888, 57.7018, 10.70, sog=0.3, name="STS-B", imo="9176187"))
        out.append(row(t, "Class A", 999001, 57.75, 10.70, sog=0.2, name="FAR", imo="9179834"))
    return out


@pytest.fixture()
def ingested(tmp_data):
    config.DMA_RAW_DIR.mkdir(parents=True)
    rows = synthetic_rows() + _sts_rows()
    z = write_zip(config.DMA_RAW_DIR / "a.zip", rows, HEADER_V1)
    res = dma.ingest_zip(z, [D], z.name, keep_fullres={D})
    assert not res.days_failed
    return res


def test_population_and_monthly_counts(ingested):
    con = dma.connect()
    out = phase1.population(con)
    assert out["mmsi_ever_tanker_class"] == 5  # 111, 222, 777, 888, 999001
    assert out["months"] == 1
    csv_text = (config.REPORTS_DIR / "phase1_population_by_month.csv").read_text()
    assert "2025-03,5,5," in csv_text
    rows = con.execute(
        f"SELECT mmsi, n_days_observed, ever_kept, modal_imo FROM "
        f"'{(config.PARQUET_DIR / 'population.parquet').as_posix()}' ORDER BY mmsi").fetchall()
    by_mmsi = {r[0]: r for r in rows}
    assert by_mmsi[333][2] is False  # short tanker never kept
    assert by_mmsi[111][3] == 9074729 and by_mmsi[111][1] == 1


def test_type_changes_flags_hull_that_stops_reporting_tanker(tmp_data):
    config.DMA_RAW_DIR.mkdir(parents=True)
    rows = synthetic_rows()
    t = DAY.replace(hour=20)
    rows.append(row(t, "Class A", 111, 57.0, 10.03, sog=0.5, ship="Cargo", cargo="No additional information"))
    z = write_zip(config.DMA_RAW_DIR / "a.zip", rows, HEADER_V1)
    dma.ingest_zip(z, [D], z.name)
    out = phase1.type_changes(dma.connect())
    assert out["changes"] >= 1 and out["mmsi_that_stopped_reporting_tanker"] == 1


def test_gap_evidence_counts_and_plots(ingested):
    out = phase1.gap_evidence(dma.connect(), sample=100)
    # 111 has a 2 h and a 1 h gap -> none over 6 h; 222 stops at 12:00 -> no next row, so no gap either
    assert out["gaps_over_6h"] == 0 and out["figure"] is None


def test_gap_evidence_on_a_two_day_track(tmp_data):
    config.DMA_RAW_DIR.mkdir(parents=True)
    t0 = DAY.replace(hour=1)
    rows = [row(t0, "Class A", 111, 57.0, 10.0, sog=8.0),
            row(t0 + timedelta(hours=9), "Class A", 111, 57.0, 10.5, sog=8.0)]
    # a dense block so the first cell has occupied neighbours, the gap-start cell does not
    for i in range(30):
        rows.append(row(t0 + timedelta(minutes=i), "Class A", 222, 57.0 + 0.1 * (i % 3), 10.5, sog=8.0))
    z = write_zip(config.DMA_RAW_DIR / "a.zip", rows, HEADER_V1)
    dma.ingest_zip(z, [D], z.name)
    out = phase1.gap_evidence(dma.connect(), sample=10)
    assert out["gaps_over_6h"] == 1
    assert out["sampled_at_coverage_edge"] == 1 and out["share_at_coverage_edge"] == 1.0
    assert "coverage artefacts" in out["conclusion"]
    assert (config.REPORTS_DIR / "phase1_gaps.png").exists()


def test_sts_readiness_finds_the_planted_pair(ingested):
    out = phase1.sts_readiness(D, dma.connect())
    assert out["pairs_fullres"] == 1          # 777 with 888; the 5 km control is excluded
    assert out["pairs_downsampled"] == 1      # 30 s downsample keeps it
    assert out["downsample_loses_pairs"] is False


def test_run_all_and_report(ingested):
    from shadowfleet.util import probes, report

    out = phase1.run_all()
    probes.write("phase1", out)
    text = report.write_phase1()
    assert "coverage" in text and "STS readiness" in text
    assert out["sts_readiness"]["pairs_fullres"] == 1


def test_run_all_reports_missing_inputs(tmp_data):
    out = phase1.run_all()
    assert "no vessel_day partitions yet" in out["population"]["error"]
    assert "no ais_fullres" in out["sts_readiness"]["error"]
