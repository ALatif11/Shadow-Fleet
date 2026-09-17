"""OpenSanctions: maritime export, EU/UK vessel sources, dated-export availability, PSC datasets.

Licence: CC BY-NC 4.0. Bulk files stay under data/cache and are never committed (CLAUDE.md rule 7).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from shadowfleet import config
from shadowfleet.util import net, probes
from shadowfleet.util.ids import imo_valid

log = logging.getLogger(__name__)


def dataset_url(slug: str, file: str, version: str = "latest") -> str:
    return f"{config.OPENSANCTIONS_BASE}/{version}/{slug}/{file}"


def _cache(slug: str, name: str, version: str = "latest") -> Path:
    return config.HTTP_CACHE_DIR / "opensanctions" / version / slug / name


def fetch_index(c, slug: str, version: str = "latest") -> dict:
    r = net.get(c, dataset_url(slug, "index.json", version))
    r.raise_for_status()
    return r.json()


def resource_url(index: dict, suffix: str) -> str | None:
    for res in index.get("resources") or []:
        name = res.get("name") or ""
        if name.endswith(suffix):
            return res.get("url")
    return None


def download_resource(c, slug: str, index: dict, suffix: str) -> Path | None:
    url = resource_url(index, suffix)
    if not url:
        return None
    dest = _cache(slug, url.rsplit("/", 1)[-1], str(index.get("version", "latest")))
    if not dest.exists():
        net.download(c, url, dest)
    return dest


# ---------------------------------------------------------------------- maritime CSV
def _col(header: list[str], *names: str) -> str | None:
    low = {h.lower().strip(): h for h in header}
    for n in names:
        if n in low:
            return low[n]
    return None


def _split(v: str | None) -> list[str]:
    if not v:
        return []
    for sep in (";", "|", ","):
        if sep in v:
            return [x.strip() for x in v.split(sep) if x.strip()]
    return [v.strip()]


def maritime_summary(text: str) -> tuple[dict, list[str]]:
    """Counts from the maritime CSV and sanctioned-vessel IMO candidates (sorted, for the GFW probe)."""
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    c_type = _col(header, "type", "schema")
    c_imo = _col(header, "imo", "imo_number", "imonumber")
    c_risk = _col(header, "risk", "topics")
    c_ds = _col(header, "datasets", "source_datasets")
    n = n_imo = n_vessel = 0
    types, risks, datasets = Counter(), Counter(), Counter()
    candidates: set[str] = set()
    for row in reader:
        n += 1
        t = (row.get(c_type) or "") if c_type else ""
        types[t] += 1
        imo = "".join(ch for ch in (row.get(c_imo) or "") if ch.isdigit()) if c_imo else ""
        if t.lower() in ("vessel", "") and imo:
            n_vessel += 1
        if imo and imo_valid(imo):
            n_imo += 1
        rks = _split(row.get(c_risk)) if c_risk else []
        dss = _split(row.get(c_ds)) if c_ds else []
        risks.update(rks)
        datasets.update(dss)
        if imo and imo_valid(imo) and any("sanction" in r for r in rks) and config.OPENSANCTIONS_OFAC in dss:
            candidates.add(imo)
    summary = {"header": header, "rows": n, "rows_with_valid_imo": n_imo, "vessel_rows_with_imo": n_vessel,
               "type_counts": dict(types.most_common(10)), "risk_counts": dict(risks.most_common(30)),
               "dataset_counts": dict(datasets.most_common(60))}
    return summary, sorted(candidates)


# ---------------------------------------------------------------------- FtM entities
def iter_ftm(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


CELEX = re.compile(r"celex%3A(\d{5}[A-Z]\d{4})", re.IGNORECASE)


def vessel_sanction_summary(path: Path, program_hint: str | None = None) -> dict:
    """Vessels in an FtM export and how many have a Sanction with a listing/start date."""
    vessels: dict[str, dict] = {}
    sanctions: list[dict] = []
    programs = Counter()
    for e in iter_ftm(path):
        schema = e.get("schema")
        props = e.get("properties") or {}
        if schema == "Vessel":
            vessels[e["id"]] = {"imo": (props.get("imoNumber") or [None])[0],
                                "first_seen": e.get("first_seen")}
        elif schema == "Sanction":
            sanctions.append(props)
            for pid in (props.get("programId") or []) + (props.get("program") or []):
                programs[pid] += 1
    dated = hinted = with_celex = 0
    example = None
    vessel_programs: Counter = Counter()
    for s in sanctions:
        ents = s.get("entity") or []
        if not any(x in vessels for x in ents):
            continue
        prog = (s.get("programId") or []) + (s.get("program") or [])
        vessel_programs.update(prog)
        if program_hint and not any(program_hint in p for p in prog):
            continue
        hinted += 1
        if s.get("listingDate") or s.get("startDate"):
            dated += 1
            example = example or {k: s.get(k) for k in ("programId", "program", "listingDate", "startDate",
                                                        "sourceUrl")}
        elif any(CELEX.search(u or "") for u in s.get("sourceUrl") or []):
            with_celex += 1
    return {"vessels": len(vessels),
            "vessels_with_imo": sum(1 for v in vessels.values() if v["imo"]),
            "vessel_sanctions" + (f"_{program_hint}" if program_hint else ""): hinted,
            "vessel_sanctions_with_date": dated,
            "vessel_sanctions_undated_with_celex": with_celex,
            "example_dated_sanction": example,
            "top_vessel_programs": dict(vessel_programs.most_common(10)),
            "top_programs": dict(programs.most_common(15))}


# ---------------------------------------------------------------------- probe (Phase 0 task 7)
def dated_export_exists(c, slug: str, yyyymmdd: str) -> int | None:
    try:
        r = net.get(c, dataset_url(slug, "index.json", yyyymmdd), retries=1)
        return r.status_code
    except Exception:  # noqa: BLE001
        return None


def probe(max_ftm_mb: int = 400) -> tuple[dict, list[str]]:
    out: dict = {"licence": config.OPENSANCTIONS_LICENCE}
    candidates: list[str] = []
    with net.client(timeout=180) as c:
        idx = fetch_index(c, config.OPENSANCTIONS_MARITIME)
        out["maritime_version"] = idx.get("version")
        csv_path = download_resource(c, config.OPENSANCTIONS_MARITIME, idx, ".csv")
        summary, candidates = maritime_summary(csv_path.read_text(encoding="utf-8")) if csv_path else ({}, [])
        out["maritime"] = summary
        out["ofac_sanctioned_imo_candidates"] = len(candidates)

        # Always probe the two confirmed label sources, plus any other eu_* slug the maritime CSV mentions.
        vessel_slugs = [config.OPENSANCTIONS_EU_DATASET, config.OPENSANCTIONS_UK_VESSELS]
        vessel_slugs += [s for s in (summary.get("dataset_counts") or {})
                         if s.startswith("eu_") and s not in vessel_slugs]
        out["label_sources"] = {}
        for slug in vessel_slugs:
            rec: dict = {}
            try:
                si = fetch_index(c, slug)
                rec["version"] = si.get("version")
                rec["title"] = si.get("title")
                ftm = next((r for r in si.get("resources") or [] if (r.get("name") or "").endswith("ftm.json")), None)
                size_mb = (ftm or {}).get("size", 0) / 1e6
                rec["ftm_size_mb"] = round(size_mb, 1)
                if ftm and size_mb <= max_ftm_mb:
                    path = download_resource(c, slug, si, "ftm.json")
                    hint = config.OPENSANCTIONS_EU_PROGRAM if slug.startswith("eu_") else None
                if slug == config.OPENSANCTIONS_UK_VESSELS:
                    hint = None
                    rec.update(vessel_sanction_summary(path, hint))
                rec["dated_exports"] = {d: dated_export_exists(c, slug, d)
                                        for d in ("20240105", "20250103", "20260102")}
            except Exception as e:  # noqa: BLE001
                rec["error"] = repr(e)
            out["label_sources"][slug] = rec
        try:
            allidx = net.get(c, f"{config.OPENSANCTIONS_BASE}/latest/index.json").json()
            names = [d.get("name") for d in allidx.get("datasets") or []]
            out["psc_like_datasets"] = sorted(n for n in names if n and ("mou" in n or "psc" in n))
        except Exception as e:  # noqa: BLE001
            out["psc_like_datasets"] = {"error": repr(e)}
    eu_with_dates = [s for s, r in out["label_sources"].items()
                     if s.startswith("eu_") and r.get("vessel_sanctions_with_date")]
    out["eu_vessel_source_candidates"] = eu_with_dates
    probes.write("opensanctions", out)
    return out, candidates
