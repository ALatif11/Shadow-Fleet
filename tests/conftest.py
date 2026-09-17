from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from shadowfleet import config

HEADER_V1 = ["# Timestamp", "Type of mobile", "MMSI", "Latitude", "Longitude", "Navigational status", "ROT",
             "SOG", "COG", "Heading", "IMO", "Callsign", "Name", "Ship type", "Cargo type", "Width", "Length",
             "Type of position fixing device", "Draught", "Destination", "ETA", "Data source type",
             "A", "B", "C", "D"]
# Variant: no '#', different spacing/case, an extra unknown column, columns reordered.
HEADER_V2 = ["Timestamp", "MMSI", "Type of Mobile", "Latitude", "Longitude", "Navigational Status", "ROT",
             "SOG", "COG", "Heading", "IMO", "CallSign", "Name", "Ship Type", "Cargo Type", "Width", "Length",
             "Type of position fixing device", "Draught", "Destination", "ETA", "Data source type",
             "A", "B", "C", "D", "Unexpected Column"]

DAY = datetime(2025, 3, 4)


def ts(dt: datetime) -> str:
    return dt.strftime("%d/%m/%Y %H:%M:%S")


def row(t, mobile, mmsi, lat, lon, sog=10.0, ship="Tanker", cargo="", length=250, name="ALPHA", imo="9074729",
        draught=10.0, source="AIS", cog=90.0):
    return {"# Timestamp": ts(t), "Type of mobile": mobile, "MMSI": str(mmsi), "Latitude": f"{lat:.6f}",
            "Longitude": f"{lon:.6f}", "Navigational status": "Under way using engine", "ROT": "0",
            "SOG": str(sog), "COG": str(cog), "Heading": "90", "IMO": imo, "Callsign": "ABCD", "Name": name,
            "Ship type": ship, "Cargo type": cargo, "Width": "40", "Length": str(length),
            "Type of position fixing device": "GPS", "Draught": str(draught), "Destination": "PRIMORSK",
            "ETA": "", "Data source type": source, "A": "200", "B": "50", "C": "20", "D": "20"}


def synthetic_rows() -> list[dict]:
    rows: list[dict] = []
    t0 = DAY.replace(hour=10)
    # 111: tanker, fast for 10 min at 1 s, then slow for 10 min at 1 s
    for s in range(600):
        rows.append(row(t0 + timedelta(seconds=s), "Class A", 111, 57.0, 10.0 + s * 0.00005, sog=12.0))
    for s in range(600):
        rows.append(row(t0 + timedelta(seconds=600 + s), "Class A", 111, 57.0, 10.03, sog=0.5))
    # static change mid-day for 111 (draught change) -> exactly one extra static row
    rows.append(row(t0 + timedelta(hours=2), "Class A", 111, 57.0, 10.03, sog=0.5, draught=14.5))
    # bad positions for 111
    rows.append(row(t0 + timedelta(hours=3), "Class A", 111, 91.0, 181.0, sog=0.5, draught=14.5))
    rows.append(row(t0 + timedelta(hours=3, seconds=1), "Class A", 111, 0.0, 0.0, sog=0.5, draught=14.5))
    # duplicate heard by a second station
    rows.append(row(t0, "Class A", 111, 57.0, 10.0, sog=12.0, source="AIS-2"))
    # 222: hazardous cargo ship, 180 m
    for m in range(0, 120, 1):
        rows.append(row(t0 + timedelta(minutes=m), "Class A", 222, 56.0, 11.0, ship="Cargo",
                        cargo="Hazard A (Major)", length=180, name="BETA", imo="9176187"))
    # 333: tanker but 50 m -> excluded
    for m in range(10):
        rows.append(row(t0 + timedelta(minutes=m), "Class A", 333, 56.5, 11.5, length=50, name="SMALL"))
    # 444: plain cargo -> excluded unless in registry
    for m in range(10):
        rows.append(row(t0 + timedelta(minutes=m), "Class A", 444, 55.5, 12.0, ship="Cargo", cargo="",
                        length=150, name="GAMMA"))
    # 555: other vessel with a jump (teleport ~110 km in 60 s) inside cell 110_24 (55.x N, 12.x E)
    rows.append(row(t0, "Class A", 555, 55.1, 12.1, ship="Passenger", length=120, name="DELTA"))
    rows.append(row(t0 + timedelta(seconds=60), "Class A", 555, 55.1, 12.4, ship="Passenger", length=120,
                    name="DELTA"))
    rows.append(row(t0 + timedelta(seconds=120), "Class A", 555, 55.12, 13.9, ship="Passenger", length=120,
                    name="DELTA"))
    # 666: unknown type, Class A, 200 m (diagnostic count only)
    rows.append(row(t0, "Class A", 666, 55.0, 12.0, ship="Undefined", length=200, name="EPS"))
    # 999: base station -> not a vessel
    rows.append(row(t0, "Base Station", 999, 55.0, 12.0, ship="", length=0, name="", imo=""))
    # next day row (must not leak into DAY)
    rows.append(row(DAY + timedelta(days=1, hours=1), "Class A", 111, 57.0, 10.0))
    # bad timestamp
    bad = row(t0, "Class A", 111, 57.0, 10.0)
    bad["# Timestamp"] = "not a time"
    rows.append(bad)
    return rows


def write_zip(path: Path, rows: list[dict], header: list[str], member: str = "aisdk-2025-03-04.csv",
              delimiter: str = ",", malformed: int = 1) -> Path:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    w.writerow(header)
    v1_to_v2 = {h.lower().lstrip("# ").replace(" ", ""): h for h in HEADER_V1}
    for r in rows:
        vals = []
        for h in header:
            key = v1_to_v2.get(h.lower().lstrip("# ").replace(" ", ""))
            vals.append(r.get(key, "") if key else "x")
        w.writerow(vals)
    for _ in range(malformed):
        buf.write(delimiter.join(["too", "few"]) + "\n")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, buf.getvalue())
    return path


@pytest.fixture()
def tmp_data(tmp_path, monkeypatch):
    """Point every data/report path at a temp dir."""
    data = tmp_path / "data"
    reports = tmp_path / "reports"
    for name, val in {
        "DATA_DIR": data, "RAW_DIR": data / "raw", "DMA_RAW_DIR": data / "raw" / "dma",
        "PARQUET_DIR": data / "parquet", "CACHE_DIR": data / "cache", "GFW_CACHE_DIR": data / "cache" / "gfw",
        "HTTP_CACHE_DIR": data / "cache" / "http", "STATE_DIR": data / "state", "TMP_DIR": data / "tmp",
        "REPORTS_DIR": reports, "LOG_DIR": reports / "logs", "PROBE_DIR": reports / "probes",
        "WINDOW_FILE": tmp_path / "config" / "window.json",
        "DUCKDB_MEMORY_LIMIT": "1GB", "DUCKDB_THREADS": 2,
    }.items():
        monkeypatch.setattr(config, name, val)
    return tmp_path
