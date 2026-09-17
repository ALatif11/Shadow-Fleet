from __future__ import annotations

from datetime import date

import httpx
import pytest

from shadowfleet import config
from shadowfleet.util import disk, net
from shadowfleet.util.ids import extract_imos, imo_valid


def test_cutoffs_24_month_window():
    w = config.Window(date(2024, 9, 17), date(2026, 9, 16))
    cuts = config.monthly_cutoffs(w, date(2026, 9, 17))
    assert cuts[0] == date(2025, 3, 31) and cuts[-1] == date(2026, 2, 28) and len(cuts) == 12
    sup = config.supervised_cutoffs(cuts)
    assert sup[0] == date(2025, 9, 30) and len(sup) == 6
    assert all(c == config.month_end(c) for c in cuts)


def test_cutoffs_15_month_window_has_no_supervised_cutoffs():
    # the reason the old 15-month fallback was removed (ADR-11 addendum)
    w = config.Window(date(2025, 6, 17), date(2026, 9, 16))
    cuts = config.monthly_cutoffs(w, date(2026, 9, 17))
    assert len(cuts) == 3 and config.supervised_cutoffs(cuts) == []


def test_cutoffs_respect_window_end():
    w = config.Window(date(2024, 1, 1), date(2025, 1, 15))
    cuts = config.monthly_cutoffs(w, date(2030, 1, 1))
    assert cuts[-1] == date(2024, 12, 31)


def test_imo_check_digit():
    assert imo_valid(9074729) and imo_valid("9176187")
    assert not imo_valid(9074728) and not imo_valid("123") and not imo_valid(None) and not imo_valid("0000000")
    assert not imo_valid("0023569")  # passes the check digit, but IMO numbers never start with 0
    txt = "Vessel Registration Identification IMO 9074729; IMO 9074728; imo:9176187"
    assert extract_imos(txt) == [9074729, 9176187]


def test_disk_guard_waits_then_releases(tmp_path, monkeypatch):
    frees = iter([10.0, 12.0, 40.0])
    monkeypatch.setattr(disk, "free_gb", lambda p: next(frees))
    sleeps = []
    disk.wait_for_space(tmp_path, min_free_gb=25, resume_free_gb=30, _sleep=sleeps.append, poll_s=1)
    assert len(sleeps) == 1  # first check fails, loop: 12 < 30 -> sleep, 40 >= 30 -> release


def test_disk_guard_fails_fast_when_not_waiting(tmp_path, monkeypatch):
    monkeypatch.setattr(disk, "free_gb", lambda p: 5.0)
    with pytest.raises(disk.DiskFullError):
        disk.wait_for_space(tmp_path, min_free_gb=25, max_wait_s=0, _sleep=lambda s: None)


def test_download_resumes_with_range(tmp_path):
    payload = b"x" * 5000
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        rng = req.headers.get("Range")
        calls.append(rng)
        if rng:
            start = int(rng.split("=")[1].rstrip("-"))
            return httpx.Response(206, content=payload[start:], headers={"Content-Length": str(len(payload) - start)})
        return httpx.Response(200, content=payload, headers={"Content-Length": str(len(payload))})

    dest = tmp_path / "f.zip"
    (tmp_path / "f.zip.part").write_bytes(payload[:1200])
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        n = net.download(c, "http://x/f.zip", dest, _sleep=lambda s: None)
    assert n == 5000 and dest.read_bytes() == payload and calls == ["bytes=1200-"]
    assert not (tmp_path / "f.zip.part").exists()


def test_download_retries_on_503(tmp_path):
    state = {"n": 0}

    def handler(req):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=b"ok", headers={"Content-Length": "2"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        assert net.download(c, "http://x/a", tmp_path / "a", _sleep=lambda s: None) == 2


def test_free_gb_is_capped_by_host_drive(tmp_path, monkeypatch):
    import shutil as _sh
    from collections import namedtuple

    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(config, "HOST_DISK_PATH", "/fake/c")
    monkeypatch.setattr(_sh, "disk_usage",
                        lambda p: Usage(0, 0, 23 * disk.GB) if str(p) == "/fake/c" else Usage(0, 0, 940 * disk.GB))
    assert round(disk.free_gb(tmp_path)) == 23
    monkeypatch.setattr(config, "HOST_DISK_PATH", "")
    assert round(disk.free_gb(tmp_path)) == 940
