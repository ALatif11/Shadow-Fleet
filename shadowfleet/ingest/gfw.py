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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from shadowfleet import config
from shadowfleet.util import net, probes

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
            entries = page.get("entries") or []
            out.extend(entries)
            nxt = page.get("nextOffset")
            if not entries or nxt is None or nxt <= offset:
                break
            offset = int(nxt)
        return out


# ---------------------------------------------------------------------- response helpers
def vessel_ids_from_search(resp: dict, imo: str | None = None) -> list[str]:
    """Vessel ids from selfReportedInfo of entries; if imo is given, keep entries whose identity mentions it."""
    ids: list[str] = []
    for e in resp.get("entries") or []:
        infos = e.get("selfReportedInfo") or []
        if imo is not None:
            imos = {str(i.get("imo")) for i in infos} | {str(i.get("imo")) for i in e.get("registryInfo") or []}
            if imo not in imos:
                continue
        for i in infos:
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
                rec.update({"n_entries": len(resp.get("entries") or []), "vessel_ids": ids,
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
