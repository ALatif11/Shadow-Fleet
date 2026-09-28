"""Global Fishing Watch API v3 client with a disk cache (ADR-3, ADR-16) and the Phase 0 probe.

Cache key = SHA-256 of (method, path, params, body). The token is never part of the key or the file.
Only 200 responses are cached. Rate-limit headers from every live response are appended to
reports/logs/gfw_ratelimit.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import httpx

from shadowfleet import config
from shadowfleet.resolve.identity import HULL_MAP
from shadowfleet.util import net, probes, report

log = logging.getLogger(__name__)


class GfwError(RuntimeError):
    pass


def indexed(name: str, values: list[str]) -> dict[str, str]:
    """GFW v3 expects array query params as name[0]=a&name[1]=b."""
    return {f"{name}[{i}]": v for i, v in enumerate(values)}


def cache_key(method: str, path: str, params: dict | None, body: Any | None) -> str:
    blob = json.dumps({"m": method.upper(), "p": path, "q": params or {}, "b": body}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class GfwClient:
    token: str = field(default_factory=lambda: config.GFW_TOKEN)
    base_url: str = config.GFW_BASE_URL
    cache_dir: Path = field(default_factory=lambda: config.GFW_CACHE_DIR)
    transport: httpx.BaseTransport | None = None
    sleep: Any = time.sleep
    offline: bool = False  # cache only; raise on miss
    stats: dict = field(default_factory=lambda: {"cache_hits": 0, "live_calls": 0, "retries": 0})
    served: set = field(default_factory=set)  # dataset versions from response metadata, e.g. "...:v4.0"

    def __post_init__(self) -> None:
        kw = {"transport": self.transport} if self.transport else {}
        self._http = net.client(
            timeout=config.GFW_TIMEOUT_S, headers={"Authorization": f"Bearer {self.token}"}, **kw
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ core
    def _cache_file(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def request(self, method: str, path: str, params: dict | None = None, body: Any | None = None) -> dict:
        key = cache_key(method, path, params, body)
        cf = self._cache_file(key)
        if cf.exists():
            self.stats["cache_hits"] += 1
            return json.loads(cf.read_text())["body"]
        if self.offline:
            raise GfwError(f"cache miss in offline mode: {method} {path} {params}")
        if not self.token:
            raise GfwError("GFW_TOKEN is not set (put it in .env)")
        url = self.base_url.rstrip("/") + "/" + path.lstrip("/")
        for attempt in range(config.GFW_MAX_RETRIES + 1):
            self.stats["live_calls"] += 1
            r = self._http.request(method, url, params=params, json=body)
            self._log_rate(path, r)
            if r.status_code == 200:
                payload = r.json()
                cf.parent.mkdir(parents=True, exist_ok=True)
                tmp = cf.with_suffix(".tmp")
                tmp.write_text(json.dumps({
                    "request": {"method": method, "path": path, "params": params, "body": body},
                    "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "body": payload,
                }))
                tmp.replace(cf)
                return payload
            if r.status_code in net.RETRY_STATUS and attempt < config.GFW_MAX_RETRIES:
                self.stats["retries"] += 1
                self.sleep(net.backoff_s(attempt, r.headers.get("Retry-After")))
                continue
            raise GfwError(f"GFW {r.status_code} for {path}: {r.text[:300]}")
        raise GfwError(f"GFW retries exhausted for {path}")

    @staticmethod
    def _log_rate(path: str, r: httpx.Response) -> None:
        hdr = {k: v for k, v in r.headers.items() if "limit" in k.lower() or k.lower() == "retry-after"}
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(config.LOG_DIR / "gfw_ratelimit.jsonl", "a") as f:
            f.write(json.dumps({"ts": datetime.now(UTC).isoformat(timespec="seconds"), "path": path,
                                "status": r.status_code, "headers": hdr}) + "\n")

    # ------------------------------------------------------------------ endpoints
    def search_vessels(self, query: str, where: str | None = None) -> dict:
        params = {"query": query, **indexed("datasets", [config.GFW_IDENTITY_DATASET]),
                  **indexed("includes", ["MATCH_CRITERIA", "OWNERSHIP"])}
        if where:
            params = {"where": where, **indexed("datasets", [config.GFW_IDENTITY_DATASET])}
        return self.request("GET", "vessels/search", params=params)

    def events(self, vessel_ids: list[str], event_type: str, start: str, end: str) -> list[dict]:
        """All events of one type for the given vessel ids, following pagination."""
        dataset = config.GFW_DATASETS[event_type]
        out: list[dict] = []
        offset = 0
        while True:
            params = {**indexed("vessels", vessel_ids), **indexed("datasets", [dataset]),
                      "start-date": start, "end-date": end,
                      "limit": str(config.GFW_PAGE_LIMIT), "offset": str(offset)}
            page = self.request("GET", "events", params=params)
            self.served.update((page.get("metadata") or {}).get("datasets") or [])
            entries = page.get("entries") or []
            out.extend(entries)
            nxt = page.get("nextOffset")
            if not entries or nxt is None or nxt <= offset:
                break
            offset = int(nxt)
        return out


# ---------------------------------------------------------------------- response helpers
def _digits(v) -> str:
    """Digits only; GFW writes IMOs bare, OpenSanctions with an "IMO" prefix."""
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def entry_imos(entry: dict) -> set[str]:
    """IMO values anywhere in a search entry (self-reported, registry, combined), digits only."""
    found = set()
    for key in ("selfReportedInfo", "registryInfo", "combinedSourcesInfo"):
        for rec in entry.get(key) or []:
            if isinstance(rec, dict) and rec.get("imo"):
                found.add(_digits(rec["imo"]))
    return found - {""}


def vessel_ids_from_search(resp: dict, imo: str | None = None) -> list[str]:
    """Vessel ids from selfReportedInfo of entries; if imo is given, keep entries whose identity mentions it."""
    ids: list[str] = []
    for e in resp.get("entries") or []:
        if imo is not None and _digits(imo) not in entry_imos(e):
            continue
        for i in e.get("selfReportedInfo") or []:
            vid = i.get("id")
            if vid and vid not in ids:
                ids.append(vid)
    return ids


def identity_is_dated(resp: dict) -> dict:
    """Does each selfReportedInfo carry transmission date ranges? (decides ADR-5 / R1 feature policy)"""
    n = dated = 0
    for e in resp.get("entries") or []:
        for i in e.get("selfReportedInfo") or []:
            n += 1
            if i.get("transmissionDateFrom") and i.get("transmissionDateTo"):
                dated += 1
    return {"identity_records": n, "with_transmission_dates": dated}


def shiptypes(resp: dict) -> list[str]:
    out: set[str] = set()
    for e in resp.get("entries") or []:
        for c in e.get("combinedSourcesInfo") or []:
            for t in c.get("shiptypes") or []:
                if t.get("name"):
                    out.add(str(t["name"]))
        for r in e.get("registryInfo") or []:
            for t in r.get("shiptypes") or []:
                out.add(str(t))
    return sorted(out)


# ---------------------------------------------------------------------- probe (Phase 0 task 5)
def probe(imos: list[str], start: str = "2025-01-01", end: str = "2025-12-31") -> dict:
    result: dict[str, Any] = {"imos": {}, "start": start, "end": end}
    saved_example = False
    with GfwClient() as c:
        for imo in imos:
            rec: dict[str, Any] = {}
            try:
                resp = c.search_vessels(imo)
                ids = vessel_ids_from_search(resp, imo)
                how = "query"
                if not ids:
                    alt = c.search_vessels(imo, where=f"imo = '{imo}'")
                    if vessel_ids_from_search(alt, imo):
                        resp, ids, how = alt, vessel_ids_from_search(alt, imo), "where"
                entries = resp.get("entries") or []
                rec.update({"n_entries": len(entries), "search_method": how,
                            "imos_in_entries": sorted(set().union(*(entry_imos(e) for e in entries)))[:10]
                            if entries else [],
                            "vessel_ids": ids,
                            "identity": identity_is_dated(resp), "shiptypes": shiptypes(resp)})
                if ids and not saved_example:
                    # GFW-derived: gitignored, local only (CLAUDE.md rule 7)
                    (config.REPORTS_DIR / "phase0_gfw_vessel.json").write_text(json.dumps(resp, indent=1))
                    saved_example = True
                ev: dict[str, Any] = {}
                for et in config.GFW_DATASETS:
                    try:
                        items = c.events(ids, et, start, end) if ids else []
                        ev[et] = {"count": len(items),
                                  "example_keys": sorted(items[0].keys()) if items else [],
                                  "example_type_fields": sorted((items[0].get(et.lower()) or {}).keys())
                                  if items and isinstance(items[0].get(et.lower()), dict) else []}
                    except GfwError as e:
                        ev[et] = {"error": str(e)[:300]}
                rec["events"] = ev
            except GfwError as e:
                rec["error"] = str(e)[:300]
            result["imos"][imo] = rec
        result["client_stats"] = c.stats
    tally = {et: sum(1 for r in result["imos"].values() if (r.get("events") or {}).get(et, {}).get("count"))
             for et in config.GFW_DATASETS}
    result["imos_with_events"] = tally
    result["go"] = bool(tally.get("GAP") or tally.get("ENCOUNTER"))
    probes.write("gfw", result)
    return result


# ---------------------------------------------------------------------- Phase 4a: events for the population
EVENTS_TABLE = "gfw_events"
VESSEL_MAP = "gfw_vessel_map.parquet"
BATCH_VESSELS = 25  # vessel ids per events call; one dataset per call is an API rule, many vessels is not
OFFSHORE_KM = 92.6  # 50 nm, PREREG `n_gaps_offshore`

# One row per event. Keyed by the IMO its vessel ids were fetched for, never by a hull: hulls are assigned at
# each cutoff (ADR-23), and GFW's value is behaviour under MMSIs Danish AIS never saw.
EVENT_COLUMNS = {
    "imo": "BIGINT", "gfw_vessel_id": "VARCHAR", "ssvid": "BIGINT", "event_id": "VARCHAR",
    "event_type": "VARCHAR", "start": "TIMESTAMP", "end": "TIMESTAMP", "observed_at": "TIMESTAMP",
    "lat": "DOUBLE", "lon": "DOUBLE", "duration_h": "DOUBLE", "start_distance_from_shore_km": "DOUBLE",
    "gap_distance_km": "DOUBLE", "gap_implied_speed_knots": "DOUBLE", "gap_intentional": "BOOLEAN",
    "partner_gfw_id": "VARCHAR", "partner_ssvid": "BIGINT", "encounter_median_distance_km": "DOUBLE",
    "encounter_median_speed_knots": "DOUBLE",
    "port_name": "VARCHAR", "port_country": "VARCHAR", "port_confidence": "INTEGER", "port_at_dock": "BOOLEAN",
    "loitering_avg_speed_knots": "DOUBLE", "loitering_distance_from_shore_km": "DOUBLE",
    "dataset": "VARCHAR",
}


def _num(v) -> float | None:
    """GFW sends some numbers as strings ("distanceKm": "1781.09", "confidence": "4") and some as numbers."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    d = _digits(v)
    return int(d) if d else None


def _ts(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.replace("Z", "+00:00")).replace(tzinfo=None) if v else None


def flatten(entry: dict, imo: int, dataset: str | None = None) -> dict:
    """One GFW event as one row of EVENT_COLUMNS. `observed_at` is the event's end: when it became knowable."""
    start, end = _ts(entry.get("start")), _ts(entry.get("end"))
    pos, vessel, dist = entry.get("position") or {}, entry.get("vessel") or {}, entry.get("distances") or {}
    gap, enc = entry.get("gap") or {}, entry.get("encounter") or {}
    pv, loit = entry.get("port_visit") or {}, entry.get("loitering") or {}
    anch = pv.get("startAnchorage") or pv.get("intermediateAnchorage") or {}
    partner = enc.get("vessel") or {}
    row = dict.fromkeys(EVENT_COLUMNS)
    row.update({
        "imo": int(imo), "gfw_vessel_id": vessel.get("id"), "ssvid": _int(vessel.get("ssvid")),
        "event_id": entry.get("id"), "event_type": entry.get("type"), "start": start, "end": end,
        "observed_at": end, "lat": _num(pos.get("lat")), "lon": _num(pos.get("lon")),
        "duration_h": (end - start).total_seconds() / 3600 if start and end else None,
        "start_distance_from_shore_km": _num(dist.get("startDistanceFromShoreKm")),
        "gap_distance_km": _num(gap.get("distanceKm")),
        "gap_implied_speed_knots": _num(gap.get("impliedSpeedKnots")),
        "gap_intentional": gap.get("intentionalDisabling") if gap else None,
        "partner_gfw_id": partner.get("id"), "partner_ssvid": _int(partner.get("ssvid")),
        "encounter_median_distance_km": _num(enc.get("medianDistanceKilometers")),
        "encounter_median_speed_knots": _num(enc.get("medianSpeedKnots")),
        "port_name": anch.get("name"), "port_country": anch.get("flag"),
        "port_confidence": int(_num(pv.get("confidence"))) if _num(pv.get("confidence")) is not None else None,
        "port_at_dock": anch.get("atDock"),
        "loitering_avg_speed_knots": _num(loit.get("averageSpeedKnots")),
        "loitering_distance_from_shore_km": _num(loit.get("averageDistanceFromShoreKm")),
        "dataset": dataset,
    })
    return row


def year_chunks(start: str, end: str) -> list[tuple[str, str]]:
    """Split [start, end] at calendar years: the Phase 0 probe only ever asked for one year at a time, and a
    multi-year request is a shape the live API has not been seen to accept."""
    y0, y1 = int(start[:4]), int(end[:4])
    return [(max(start, f"{y}-01-01"), min(end, f"{y}-12-31")) for y in range(y0, y1 + 1)]


def fetch(imos: list[int], start: str, end: str, client: GfwClient | None = None) -> dict:
    """Vessel ids for each IMO, then every event of every type for them. Cached per call, so a rerun after a
    crash replays the finished part from disk and only goes live where it stopped."""
    own = client is None
    c = client or GfwClient()
    vmap: list[dict] = []
    misses: list[int] = []
    try:
        for i, imo in enumerate(imos, 1):
            ids = vessel_ids_from_search(c.search_vessels(str(imo)), str(imo))
            if not ids:
                misses.append(imo)
            vmap += [{"imo": imo, "gfw_vessel_id": v, "match_method": "imo"} for v in ids]
            if i % 100 == 0:
                log.info("gfw vessel search %d/%d, %d ids, %s", i, len(imos), len(vmap), c.stats)
        imo_of = {r["gfw_vessel_id"]: r["imo"] for r in vmap}
        ids = sorted(imo_of)
        events: dict[str, dict] = {}  # by event id: a year boundary can return one event twice
        for et, dataset in config.GFW_DATASETS.items():
            for lo, hi in year_chunks(start, end):
                for b in range(0, len(ids), BATCH_VESSELS):
                    for e in c.events(ids[b:b + BATCH_VESSELS], et, lo, hi):
                        vid = (e.get("vessel") or {}).get("id")
                        if vid in imo_of and e.get("id"):
                            events[e["id"]] = flatten(e, imo_of[vid], dataset)  # alias; served version in datasets
            log.info("gfw %s: %d events so far", et, len(events))
    finally:
        if own:
            c.close()
    return {"vessel_map": vmap, "events": list(events.values()), "misses": misses,
            "datasets": sorted(c.served), "client_stats": dict(c.stats)}


def write(result: dict) -> dict:
    """`gfw_vessel_map.parquet` and `gfw_events/dt=all/`, through DuckDB so no optional dependency is needed."""
    con = duckdb.connect()
    config.PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    con.execute("CREATE TABLE vm (imo BIGINT, gfw_vessel_id VARCHAR, match_method VARCHAR)")
    if result["vessel_map"]:
        con.executemany("INSERT INTO vm VALUES (?, ?, ?)",
                        [(r["imo"], r["gfw_vessel_id"], r["match_method"]) for r in result["vessel_map"]])
    con.execute(f"COPY vm TO '{(config.PARQUET_DIR / VESSEL_MAP).as_posix()}' (FORMAT parquet)")
    cols = ", ".join(f'"{k}" {v}' for k, v in EVENT_COLUMNS.items())
    con.execute(f"CREATE TABLE ev ({cols})")
    if result["events"]:
        con.executemany(f"INSERT INTO ev VALUES ({', '.join('?' * len(EVENT_COLUMNS))})",
                        [tuple(r[k] for k in EVENT_COLUMNS) for r in result["events"]])
    out = config.PARQUET_DIR / EVENTS_TABLE / "dt=all"
    out.mkdir(parents=True, exist_ok=True)
    con.execute(f"COPY (SELECT * FROM ev ORDER BY imo, observed_at) TO '{(out / 'part-0.parquet').as_posix()}'"
                " (FORMAT parquet, COMPRESSION zstd)")
    return coverage(con, result)


def coverage(con, result: dict) -> dict:
    """Phase 4a task 5: how much of the population GFW can see, and what it sees. Task 4b: encounters."""
    n_imos = len({r["imo"] for r in result["vessel_map"]}) + len(result["misses"])
    by_type = dict(con.execute("SELECT event_type, count(*) FROM ev GROUP BY 1").fetchall())
    imos_with = dict(con.execute("SELECT event_type, count(DISTINCT imo) FROM ev GROUP BY 1").fetchall())
    per_imo = con.execute("""
        SELECT quantile_cont(n, 0.5), quantile_cont(n, 0.9), max(n)
        FROM (SELECT imo, count(*) AS n FROM ev GROUP BY imo)
    """).fetchone()
    regions = config.RUSSIAN_PORT_REGIONS
    in_region = " OR ".join(f"(lat BETWEEN {a} AND {b} AND lon BETWEEN {c} AND {d})"
                            for a, b, c, d in regions.values())
    rus = con.execute(f"""
        SELECT count(*) FILTER (WHERE port_country = 'RUS'),
               count(*) FILTER (WHERE port_country = 'RUS' AND ({in_region})),
               count(*) FILTER (WHERE port_country = 'RUS' AND port_name IS NULL)
        FROM ev WHERE event_type = 'port_visit'
    """).fetchone()
    return {"imos_requested": n_imos, "imos_with_a_gfw_id": n_imos - len(result["misses"]),
            "share_with_a_gfw_id": round((n_imos - len(result["misses"])) / n_imos, 4) if n_imos else None,
            "vessel_ids": len(result["vessel_map"]), "events_by_type": by_type, "imos_with_event_type": imos_with,
            "events_per_imo_p50_p90_max": list(per_imo) if per_imo[0] is not None else None,
            "encounter_share_of_imos": round(imos_with.get("encounter", 0) / n_imos, 4) if n_imos else None,
            "russian_port_visits": {"all_rus": rus[0], "in_b1_regions": rus[1], "without_a_name": rus[2]},
            "datasets": result["datasets"], "client_stats": result["client_stats"],
            "table": f"data/parquet/{EVENTS_TABLE}", "vessel_map": f"data/parquet/{VESSEL_MAP}"}


def run(start: str | None = None, end: str | None = None, limit: int | None = None) -> dict:
    """Phase 4a end to end: every IMO the resolver voted through, the window plus the 180 days before it."""
    hm = config.PARQUET_DIR / HULL_MAP
    if not hm.exists():
        raise SystemExit("no hull_map.parquet; run `make identity` (Phase 3) first")
    w = config.load_window()
    if w is None:
        raise SystemExit("no config/window.json; run `make window-gate` (Phase 0) first")
    start = start or (w.start - timedelta(days=config.FEATURE_WINDOW_DAYS)).isoformat()
    end = end or w.end.isoformat()
    imos = [r[0] for r in duckdb.connect().execute(
        f"SELECT DISTINCT voted_imo FROM '{hm.as_posix()}' WHERE method = 'imo_majority' ORDER BY 1"
    ).fetchall()][:limit]
    log.info("gfw events for %d IMOs, %s to %s", len(imos), start, end)
    out = write(fetch(imos, start, end))
    out.update({"start": start, "end": end})
    probes.write("gfw_events", out)
    report.write_report("phase4a", out)
    return out
