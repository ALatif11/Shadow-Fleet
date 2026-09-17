from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from shadowfleet import config
from shadowfleet.briefs import llm_smoke
from shadowfleet.ingest import window
from shadowfleet.util import disk, doctor, probes, report

GB = disk.GB


def fake_probe(earliest="2024-09-17", latest="2026-09-15", per_day=200 * 1024**2, earliest_daily=None):
    return {
        "earliest_day": earliest, "earliest_daily": earliest_daily, "latest_day": latest,
        "zipped_bytes": 800 * 1024**2, "seconds_download": 60,
        "result": {"stage": {"seconds": 120}, "day_stats": [{
            "seconds": 60, "bytes_out": {"ais_dynamic": per_day, "ais_fullres": 10 * GB}}],
            "days_failed": {}},
    }


def test_gate_keeps_full_history_when_it_fits():
    d = window.decide(fake_probe(), free_gb=300, today=date(2026, 9, 17))
    assert d.start == "2024-09-17" and d.end == "2026-09-15"
    assert d.parquet_bytes_per_day == 200 * 1024**2  # fullres excluded
    assert len(d.cutoffs) == 12 and len(d.supervised_cutoffs) == 6
    assert d.seconds_per_day == 240 and d.days_per_wallclock_day == 360


def test_gate_shrinks_for_disk_and_refuses_below_floor():
    with pytest.raises(window.WindowGateError, match="months"):
        window.decide(fake_probe(), free_gb=80, today=date(2026, 9, 17))
    d = window.decide(fake_probe(), free_gb=80, today=date(2026, 9, 17), allow_short=True)
    assert d.start > "2024-09-17" and "disk" in d.reason


def test_gate_no_budget():
    with pytest.raises(window.WindowGateError, match="no disk budget"):
        window.decide(fake_probe(), free_gb=20, today=date(2026, 9, 17))


def test_gate_run_writes_window_and_keeps_previous(tmp_data, monkeypatch):
    probes.write("dma", fake_probe())
    monkeypatch.setattr(disk, "free_gb", lambda p: 300.0)
    window.run(today=date(2026, 9, 17))
    assert config.load_window() == config.Window(date(2024, 9, 17), date(2026, 9, 15))
    probes.write("dma", fake_probe(earliest="2024-10-01"))
    window.run(today=date(2026, 9, 17))
    w = json.loads(config.WINDOW_FILE.read_text())
    assert w["start"] == "2024-10-01" and w["previous"]["start"] == "2024-09-17"


def test_report_renders_not_run_and_window(tmp_data, monkeypatch):
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    text = report.render()
    assert text.count("NOT RUN") >= 6 and "Assumptions to confirm" in text
    probes.write("dma", fake_probe())
    monkeypatch.setattr(disk, "free_gb", lambda p: 300.0)
    window.run(today=date(2026, 9, 17))
    text = report.write()
    assert "2024-09-17 to 2026-09-15" in text and (config.REPORTS_DIR / "phase0.md").exists()


def test_report_renders_full_dma_probe(tmp_data, tmp_path):
    from shadowfleet.ingest import dma
    from tests.conftest import DAY, HEADER_V1, synthetic_rows, write_zip

    config.DMA_RAW_DIR.mkdir(parents=True)
    z = write_zip(config.DMA_RAW_DIR / "aisdk-2025-03-04.zip", synthetic_rows(), HEADER_V1)
    res = dma.ingest_zip(z, [DAY.date()], z.name, diagnostics=True, keep_fullres={DAY.date()})
    from dataclasses import asdict
    probes.write("dma", {"index_url": "http://x/", "n_daily_files": 1, "n_monthly_files": 0,
                         "earliest_day": "2025-03-04", "latest_day": "2025-03-04", "earliest_daily": "2025-03-04",
                         "earliest_monthly": None, "unrecognised_archives": [], "sample_sizes_bytes": {},
                         "benchmark_day": "2025-03-04", "benchmark_file": z.name, "benchmark_kind": "daily",
                         "zipped_bytes": 1000, "seconds_download": 1.0, "result": asdict(res)})
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    text = report.render()
    assert "| ais_dynamic | 151 |" in text and "Unknown columns: none" in text


def test_llm_smoke_against_mock(tmp_data, monkeypatch):
    def handler(req):
        if req.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "gemma-test"}]})
        body = json.loads(req.content)
        assert body["response_format"]["type"] == "json_schema"
        content = json.dumps({"summary": "Gap [E1] and encounter [E2].", "risk_level": "high",
                              "evidence_ids": ["E1", "E2"]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                         "timings": {"predicted_per_second": 42.0}})

    real = httpx.Client
    monkeypatch.setattr(llm_smoke.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(llm_smoke, "vram", lambda: "7000, 12227")
    out = llm_smoke.run("http://llm")
    assert out["go"] and out["tokens_per_second"] == 42.0 and out["cited_only_known_ids"]
    assert probes.read("llm")["models"] == ["gemma-test"]


def test_doctor_offline_runs(tmp_data):
    res = doctor.checks(network=False)
    names = {c.name for c in res}
    assert {"python", "free disk for data/", "gpu", "GFW_TOKEN"} <= names


def test_doctor_network_deadline(monkeypatch):
    import time as _t

    monkeypatch.setattr(doctor, "_probe_url", lambda url, timeout: _t.sleep(5) or "HTTP 200")
    t0 = _t.monotonic()
    res = doctor.network_checks(deadline_s=0.3)
    assert _t.monotonic() - t0 < 2 and all(c.status == doctor.WARN for c in res)


def test_gate_defaults_to_first_daily_file_and_guards_monthly():
    p = fake_probe(earliest="2006-03-01", earliest_daily="2024-03-01", per_day=2_500_000)
    d = window.decide(p, free_gb=86, today=date(2026, 9, 17))
    assert d.start == "2024-03-01" and len(d.cutoffs) == 19 and len(d.supervised_cutoffs) == 12
    with pytest.raises(window.WindowGateError, match="monthly"):
        window.decide(p, free_gb=86, today=date(2026, 9, 17), start=date(2023, 1, 1))
    d = window.decide(p, free_gb=86, today=date(2026, 9, 17), start=date(2023, 1, 1), allow_monthly=True)
    assert d.start == "2023-01-01"
    d = window.decide(p, free_gb=86, today=date(2026, 9, 17), start=date(2024, 6, 1))
    assert d.start == "2024-06-01"
