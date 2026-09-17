from __future__ import annotations

import json
from datetime import date

import duckdb
import pytest

from shadowfleet import config
from shadowfleet.ingest import dma
from tests.conftest import DAY, HEADER_V1, HEADER_V2, synthetic_rows, write_zip

D = DAY.date()


def q(sql: str):
    return duckdb.sql(sql).fetchall()


def part(table: str, day: date = D) -> str:
    return (config.PARQUET_DIR / table / f"dt={day.isoformat()}" / "part-0.parquet").as_posix()


@pytest.fixture()
def ingested(tmp_data):
    config.DMA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    z = write_zip(config.DMA_RAW_DIR / "aisdk-2025-03-04.zip", synthetic_rows(), HEADER_V1)
    res = dma.ingest_zip(z, [D], z.name, keep_fullres={D}, diagnostics=True)
    return z, res


def test_header_mapping_variants():
    names, unknown, missing = dma.map_header(HEADER_V1)
    assert not unknown and not missing
    names, unknown, missing = dma.map_header(HEADER_V2)
    assert unknown == ["Unexpected Column"] and not missing
    assert names[0] == "ts_raw" and names[1] == "mmsi"
    _, _, missing = dma.map_header(["Timestamp", "MMSI", "Latitude"])
    assert "lon" in missing


def test_parse_index_daily_monthly_unknown():
    html = ('<a href="aisdk-2024-09-01.zip">x</a><a href="aisdk-2006-03.zip">m</a>'
            '<a href="aisdk_20060302.rar">old</a><a href="../">up</a>')
    files, unknown = dma.parse_index(html, "http://web.ais.dk/aisdata/")
    assert [f.kind for f in files] == ["monthly", "daily"]
    assert files[1].url == "http://web.ais.dk/aisdata/aisdk-2024-09-01.zip"
    assert unknown == ["aisdk_20060302.rar"]
    assert len(files[0].dates()) == 31


def test_daily_file_wins_over_monthly():
    daily = dma.DmaFile("aisdk-2024-03-02.zip", "u1", "daily", "2024-03-02")
    monthly = dma.DmaFile("aisdk-2024-03.zip", "u2", "monthly", "2024-03")
    m = dma.files_for_dates([monthly, daily], date(2024, 3, 1), date(2024, 3, 3))
    assert m[date(2024, 3, 2)] is daily and m[date(2024, 3, 1)] is monthly and len(m) == 3


def test_stage1_counts(ingested):
    _, res = ingested
    assert res.stage["malformed_rows"] == 1
    assert res.stage["rows_in"] == len(synthetic_rows())
    assert res.days_done == [D.isoformat()] and not res.days_failed


def test_dynamic_downsample_dedupe_and_bad_positions(ingested):
    rows = dict(q(f"SELECT mmsi, count(*) FROM '{part('ais_dynamic')}' GROUP BY 1"))
    # 111: 10 fast 60 s buckets + 20 slow 30 s buckets + 1 row at 12:00; bad positions and dup dropped
    assert rows == {111: 31, 222: 120}
    assert q(f"SELECT count(*) FROM '{part('ais_dynamic')}' WHERE lat > 90 OR (lat = 0 AND lon = 0)")[0][0] == 0
    types = dict(q(f"DESCRIBE SELECT observed_at, mmsi FROM '{part('ais_dynamic')}'")[i][:2] for i in range(2))
    assert types["observed_at"].startswith("TIMESTAMP") and types["mmsi"] == "BIGINT"


def test_static_change_points(ingested):
    rows = q(f"SELECT mmsi, draught FROM '{part('ais_static')}' ORDER BY mmsi, observed_at")
    assert rows == [(111, 10.0), (111, 14.5), (222, 10.0)]


def test_filter_excludes_short_tanker_and_plain_cargo(ingested):
    kept = {r[0] for r in q(f"SELECT DISTINCT mmsi FROM '{part('ais_dynamic')}'")}
    assert kept == {111, 222}
    vd = dict(q(f"SELECT mmsi, kept FROM '{part('vessel_day')}'"))
    assert vd == {111: True, 222: True, 333: False, 444: False, 555: False, 666: False}  # 999 is not a vessel


def test_artifacts_and_baseline(ingested):
    art = {r[0]: r[1:] for r in q(f"SELECT mmsi, n_jumps, n_bad_positions FROM '{part('ais_artifacts')}'")}
    assert art == {111: (0, 2), 222: (0, 0)}
    base = {r[0]: r[1:] for r in q(
        f"SELECT cell_id, n_mmsi_observed, n_mmsi_jumped, frac_jumped FROM '{part('jump_baseline')}'")}
    assert base["110_24"] == (2, 1, 0.5)  # 555 jumped, 666 did not
    assert base["110_27"] == (1, 1, 1.0)
    assert base["114_20"] == (1, 0, 0.0)  # 111 near 57.0N 10.0E
    obs = q(f"SELECT DISTINCT observed_at FROM '{part('jump_baseline')}'")
    assert str(obs[0][0]) == "2025-03-04 23:59:59"


def test_day_stats_and_marker(ingested):
    _, res = ingested
    ds = res.day_stats[0]
    assert ds["rows_bad_timestamp"] == 1
    assert ds["mmsi_all"] == 6 and ds["mmsi_tanker_today"] == 2 and ds["mmsi_kept"] == 2
    assert ds["mmsi_unknown_type_class_a_100m"] == 1
    assert ds["rows_out"]["ais_fullres"] > ds["rows_out"]["ais_dynamic"]
    assert ds["diagnostics"]["jumps_total"] == 2
    marker = json.loads(dma.marker_path(D).read_text())
    assert marker["day"]["mmsi_kept"] == 2 and marker["malformed_flag"] is False
    assert dma.load_registry() == {111: D.isoformat(), 222: D.isoformat()}
    assert (config.LOG_DIR / "dma_ingest.csv").exists()


def test_zip_and_temp_deleted(ingested):
    z, _ = ingested
    assert not z.exists()
    assert not list(config.TMP_DIR.glob("*.parquet"))


def test_idempotent_rerun(tmp_data):
    config.DMA_RAW_DIR.mkdir(parents=True)
    z = write_zip(config.DMA_RAW_DIR / "a.zip", synthetic_rows(), HEADER_V1)
    dma.ingest_zip(z, [D], z.name, delete_zip=False)
    first = q(f"SELECT count(*) FROM '{part('ais_dynamic')}'")
    dma.ingest_zip(z, [D], z.name)
    assert q(f"SELECT count(*) FROM '{part('ais_dynamic')}'") == first
    files = list((config.PARQUET_DIR / "ais_dynamic").rglob("*.parquet"))
    assert len(files) == 1


def test_registry_keeps_mmsi_that_is_not_tanker_today(tmp_data):
    config.DMA_RAW_DIR.mkdir(parents=True)
    dma.save_registry({444: "2025-01-01"})
    z = write_zip(config.DMA_RAW_DIR / "a.zip", synthetic_rows(), HEADER_V1)
    res = dma.ingest_zip(z, [D], z.name)
    ds = res.day_stats[0]
    assert ds["mmsi_kept"] == 3 and ds["mmsi_kept_from_registry"] == 1
    assert dma.load_registry()[444] == "2025-01-01"


def test_header_variant_two_and_semicolons(tmp_data):
    config.DMA_RAW_DIR.mkdir(parents=True)
    z = write_zip(config.DMA_RAW_DIR / "b.zip", synthetic_rows(), HEADER_V2, delimiter=";")
    res = dma.ingest_zip(z, [D], z.name)
    assert res.stage["unknown_columns"] == ["Unexpected Column"]
    assert dict(q(f"SELECT mmsi, count(*) FROM '{part('ais_dynamic')}' GROUP BY 1")) == {111: 31, 222: 120}


def test_monthly_file_splits_days(tmp_data):
    config.DMA_RAW_DIR.mkdir(parents=True)
    z = write_zip(config.DMA_RAW_DIR / "aisdk-2025-03.zip", synthetic_rows(), HEADER_V1)
    nxt = date(2025, 3, 5)
    res = dma.ingest_zip(z, [D, nxt], z.name)
    assert res.days_done == [D.isoformat(), nxt.isoformat()]
    assert q(f"SELECT count(*) FROM '{part('ais_dynamic', nxt)}'")[0][0] == 1
    assert dma.check(D, nxt)["missing"] == []


def test_missing_required_column_fails_day_and_keeps_zip(tmp_data):
    config.DMA_RAW_DIR.mkdir(parents=True)
    hdr = [h for h in HEADER_V1 if h != "Longitude"]
    z = write_zip(config.DMA_RAW_DIR / "c.zip", synthetic_rows(), hdr)
    res = dma.ingest_zip(z, [D], z.name)
    assert "required columns missing" in res.days_failed[D.isoformat()]
    assert z.exists()
    assert "required columns missing" in dma.check(D, D)["failed"][D.isoformat()]
    assert not dma.is_done(D)


S3_PAGE1 = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Name>aisdata.ais.dk</Name>
<KeyCount>2</KeyCount><MaxKeys>2</MaxKeys><IsTruncated>true</IsTruncated>
<NextContinuationToken>tok1</NextContinuationToken>
<Contents><Key>aisdk-2024-09-01.zip</Key><Size>650000000</Size></Contents>
<Contents><Key>aisdk-2006-03.zip</Key><Size>900</Size></Contents></ListBucketResult>"""
S3_PAGE2 = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>false</IsTruncated>
<Contents><Key>aisdk-2026-09-14.zip</Key><Size>700000000</Size></Contents>
<Contents><Key>readme.txt</Key><Size>10</Size></Contents></ListBucketResult>"""


def test_s3_listing_paginates_and_falls_back(monkeypatch):
    import httpx


    calls = []

    def handler(req):
        calls.append(str(req.url))
        if "web.ais.dk" in req.url.host or req.url.host == "aisdata.ais.dk":
            return httpx.Response(504)
        if req.url.params.get("continuation-token") == "tok1":
            return httpx.Response(200, text=S3_PAGE2)
        return httpx.Response(200, text=S3_PAGE1)

    monkeypatch.setattr(config, "DMA_INDEX_URLS", ["http://web.ais.dk/aisdata/",
                                                   "http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/"])
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        base, files, unknown = dma.list_available(c)
    assert base.startswith("http://aisdata.ais.dk.s3")
    assert [(f.name, f.kind, f.size) for f in files] == [
        ("aisdk-2006-03.zip", "monthly", 900), ("aisdk-2024-09-01.zip", "daily", 650000000),
        ("aisdk-2026-09-14.zip", "daily", 700000000)]
    assert files[-1].url == "http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/aisdk-2026-09-14.zip"
    assert unknown == []
    assert any("continuation-token=tok1" in u for u in calls)


def test_registry_keeps_earliest_day_when_ingested_out_of_order(tmp_data):
    config.DMA_RAW_DIR.mkdir(parents=True)
    dma.save_registry({111: "2026-09-03"})
    z = write_zip(config.DMA_RAW_DIR / "a.zip", synthetic_rows(), HEADER_V1)
    dma.ingest_zip(z, [D], z.name)
    assert dma.load_registry()[111] == D.isoformat()
