from __future__ import annotations

import io
from datetime import date

import httpx

from shadowfleet import config
from shadowfleet.ingest import dma
from shadowfleet.util import net
from tests.conftest import HEADER_V1, synthetic_rows, write_zip


def _zip_bytes(tmp_path, name):
    return write_zip(tmp_path / name, synthetic_rows(), HEADER_V1).read_bytes()


def test_run_window_end_to_end(tmp_data, monkeypatch, tmp_path):
    good = _zip_bytes(tmp_path, "g.zip")
    index = ('<a href="aisdk-2025-03-04.zip">a</a><a href="aisdk-2025-03-05.zip">b</a>'
             '<a href="aisdk-2025-03-06.zip">c</a>')
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        if req.url.path == "/aisdata/":
            return httpx.Response(200, text=index)
        if req.url.path.endswith("2025-03-06.zip"):
            return httpx.Response(404)
        return httpx.Response(200, content=good, headers={"Content-Length": str(len(good))})

    real_client = net.client
    monkeypatch.setattr(net, "client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    summary = dma.run_window(date(2025, 3, 4), date(2025, 3, 7), max_consecutive_failures=5)
    # 03-04 has data; 03-05's file holds only 03-04/03-05 rows -> 03-05 gets the one next-day row
    assert summary["done"] == 2
    assert set(summary["failed"]) == {"2025-03-06"}
    assert summary["not_in_index"] == ["2025-03-07"]
    assert not list(config.DMA_RAW_DIR.glob("*.zip"))
    chk = dma.check(date(2025, 3, 4), date(2025, 3, 7))
    assert chk["missing"] == ["2025-03-06", "2025-03-07"] and "2025-03-06" in chk["failed"]
    # rerun skips done days: only the failed one is attempted again
    seen.clear()
    summary2 = dma.run_window(date(2025, 3, 4), date(2025, 3, 7), max_consecutive_failures=5)
    assert summary2["planned_days"] == 1
    assert not any(p.endswith("03-04.zip") for m, p in seen if m == "GET")


def test_run_window_stops_on_systematic_failures(tmp_data, monkeypatch, tmp_path):
    bad = io.BytesIO(b"not a zip").getvalue()
    index = "".join(f'<a href="aisdk-2025-03-0{i}.zip">x</a>' for i in range(1, 8))

    def handler(req):
        if req.url.path == "/aisdata/":
            return httpx.Response(200, text=index)
        return httpx.Response(200, content=bad, headers={"Content-Length": str(len(bad))})

    real_client = net.client
    monkeypatch.setattr(net, "client", lambda **kw: real_client(transport=httpx.MockTransport(handler)))
    s = dma.run_window(date(2025, 3, 1), date(2025, 3, 7), max_consecutive_failures=3)
    assert s.get("stopped_early") and len(s["failed"]) == 3
    # corrupt zips are kept for inspection
    assert len(list(config.DMA_RAW_DIR.glob("*.zip"))) >= 3
