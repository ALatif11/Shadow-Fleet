"""Dated sanctions actions and point-in-time labels (Phase 2, ADR-4/ADR-15).

`sanctions_actions.parquet` is the only dated sanctions table in the project. Rows come from:
  OFAC  add dates from SDN advanced XML `SanctionsEntry/EntryEvent/Date` (every current entry is dated),
        plus the yearly change archive for removals, modifications and vessels no longer listed.
  EU    `eu_sanctions_map` Sanction entities with programId EU-MARE (Annex XLII to Reg 833/2014),
        date from `startDate`, else the CELEX id in `sourceUrl` mapped to its Official Journal date.
  UK    `gb_fcdo_sanctions` vessels, date from `startDate` (or `listingDate` when present).

Labels are derived only from this table, never from a current-snapshot flag (CLAUDE.md rule 1).
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from shadowfleet import config
from shadowfleet.ingest import ofac, opensanctions
from shadowfleet.util.ids import extract_imos, imo_valid

log = logging.getLogger(__name__)

ACTIONS_FILE = "sanctions_actions.parquet"
SOURCES = ("OFAC", "EU", "UK")
# EntryEventTypeID in the SDN advanced XML; 1 is the original entry. Verified against the live file in
# Phase 0 (21,749 dated events); other ids are kept as `modify` rather than guessed at.
ENTRY_EVENT_ADDED = "1"

SCHEMA = pa.schema([
    ("source", pa.string()), ("action", pa.string()), ("date", pa.date32()), ("imo", pa.int64()),
    ("name", pa.string()), ("program", pa.string()), ("via", pa.string()), ("raw", pa.string()),
])


# --------------------------------------------------------------------------- OFAC advanced XML
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_advanced_xml(path: Path) -> list[dict]:
    """Vessel entries with an IMO and their dated entry events.

    Two passes over one streamed parse: `DistinctParty` gives profile id -> (name, imo), `SanctionsEntry`
    gives profile id -> events. Only entries currently on the list appear here (ADR-4 amended).
    """
    parties: dict[str, dict] = {}
    rows: list[dict] = []
    for _, el in ET.iterparse(path, events=("end",)):
        tag = _local(el.tag)
        if tag == "DistinctParty":
            text = " ".join(t or "" for t in el.itertext())
            imos = extract_imos(text)
            pid = el.get("FixedRef") or el.get("ID") or ""
            if pid and imos:
                name = next((t.strip() for t in el.itertext() if t and t.strip()), "")
                parties[pid] = {"imo": imos[0], "name": name[:200]}
            el.clear()
        elif tag == "SanctionsEntry":
            pid = el.get("ProfileID") or ""
            programs = [c.text for c in el if _local(c.tag) == "SanctionsMeasure"]
            for ev in (c for c in el if _local(c.tag) == "EntryEvent"):
                d = _event_date(ev)
                if d is None:
                    continue
                rows.append({"profile_id": pid, "date": d,
                             "action": "add" if ev.get("EntryEventTypeID") == ENTRY_EVENT_ADDED else "modify",
                             "program": ";".join(p for p in programs if p) or None,
                             "event_type_id": ev.get("EntryEventTypeID")})
            el.clear()
    out = []
    for r in rows:
        party = parties.get(r.pop("profile_id"))
        if not party:
            continue  # not a vessel, or no IMO in its features
        out.append({"source": "OFAC", "action": r["action"], "date": r["date"], "imo": party["imo"],
                    "name": party["name"], "program": r["program"], "via": "advanced_xml",
                    "raw": f"EntryEventTypeID={r['event_type_id']}"})
    return out


def _event_date(ev: ET.Element) -> date | None:
    for child in ev:
        if _local(child.tag) != "Date":
            continue
        parts = {_local(g.tag): (g.text or "").strip() for g in child}
        try:
            return date(int(parts["Year"]), int(parts["Month"]), int(parts["Day"]))
        except (KeyError, ValueError):
            return None
    return None


def archive_rows(years: range) -> list[dict]:
    """Change-archive rows for vessels: removals, modifications and adds (cross-check for the XML)."""
    from shadowfleet.util import net

    out: list[dict] = []
    with net.client(timeout=180) as c:
        for year in years:
            try:
                _src, text = ofac.changes_for_year(c, year)
            except Exception as e:  # noqa: BLE001 - a missing year is a finding, not a failure
                log.warning("archive year unavailable", extra={"year": year, "err": repr(e)})
                continue
            rows, _stats = ofac.parse_changes_text(text)
            for r in rows:
                if not (r.is_vessel and r.imo and r.date and r.action):
                    continue
                out.append({"source": "OFAC", "action": r.action, "date": date.fromisoformat(r.date),
                            "imo": r.imo, "name": r.name, "program": ";".join(r.programs) or None,
                            "via": "change_archive", "raw": r.raw[:500]})
    return out


# --------------------------------------------------------------------------- EU and UK
def _first_date(props: dict, *keys: str) -> date | None:
    for k in keys:
        for v in props.get(k) or []:
            try:
                return date.fromisoformat(str(v)[:10])
            except ValueError:
                continue
    return None


def opensanctions_rows(path: Path, source: str, program: str | None, celex_dates: dict[str, str]) -> list[dict]:
    """Vessel sanctions from an FtM export; CELEX fallback when the entity carries no date."""
    vessels: dict[str, dict] = {}
    sanctions: list[dict] = []
    for e in opensanctions.iter_ftm(path):
        props = e.get("properties") or {}
        if e.get("schema") == "Vessel":
            imos = [i for i in (str(x) for x in (props.get("imoNumber") or [])) if imo_valid(i)]
            if imos:
                vessels[e["id"]] = {"imo": int(imos[0]),
                                    "name": (props.get("name") or [None])[0]}
        elif e.get("schema") == "Sanction":
            sanctions.append(props)
    out: list[dict] = []
    undated = 0
    for s in sanctions:
        progs = (s.get("programId") or []) + (s.get("program") or [])
        if program and not any(program in p for p in progs):
            continue
        d = _first_date(s, "startDate", "listingDate")
        via = "startDate"
        if d is None:
            celex = next((m.group(1) for u in (s.get("sourceUrl") or [])
                          if (m := opensanctions.CELEX.search(u or ""))), None)
            iso = celex_dates.get((celex or "").upper())
            if iso:
                d, via = date.fromisoformat(iso), f"celex:{celex}"
        for ent in s.get("entity") or []:
            v = vessels.get(ent)
            if not v:
                continue
            if d is None:
                undated += 1
                continue
            out.append({"source": source, "action": "add", "date": d, "imo": v["imo"], "name": v["name"],
                        "program": ";".join(progs) or None, "via": via, "raw": None})
    if undated:
        log.warning("vessel sanctions without a usable date", extra={"source": source, "n": undated})
    return out


def load_celex_dates() -> dict[str, str]:
    """CELEX id -> Official Journal date, from config/celex_dates.json (each entry cited by hand)."""
    p = config.REPO_ROOT / "config" / "celex_dates.json"
    if not p.exists():
        return {}
    return {k.upper(): v for k, v in json.loads(p.read_text()).items() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)}


# --------------------------------------------------------------------------- table and labels
def write_actions(rows: list[dict], out: Path | None = None) -> dict:
    out = out or config.PARQUET_DIR / ACTIONS_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [r for r in rows if r.get("imo") and r.get("date") and r.get("action")]
    table = pa.Table.from_pylist(rows, schema=SCHEMA).sort_by([("imo", "ascending"), ("date", "ascending")])
    tmp = out.with_suffix(".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(out)
    con = duckdb.connect()
    per_source = con.execute(f"""
        SELECT source, action, count(*), count(DISTINCT imo), min(date), max(date)
        FROM '{out.as_posix()}' GROUP BY source, action ORDER BY source, action
    """).fetchall()
    con.close()
    return {"rows": table.num_rows, "by_source_action": per_source, "file": str(out)}


@dataclass(frozen=True)
class Listing:
    imo: int
    date: date
    source: str


def _actions(path: Path | None = None) -> list[dict]:
    path = path or config.PARQUET_DIR / ACTIONS_FILE
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run `make labels` (Phase 2) first")
    return pq.read_table(path).to_pylist()


def listed_as_of(T: date, sources: tuple[str, ...] = SOURCES, path: Path | None = None) -> dict[int, Listing]:
    """IMOs with an unrevoked add on or before T, and the date of that add.

    A remove after the latest add clears the listing again (ADR-4: removals are replayed too).
    """
    state: dict[int, Listing] = {}
    for r in sorted(_actions(path), key=lambda r: (r["imo"], r["date"])):
        if r["source"] not in sources or r["date"] > T:
            continue
        if r["action"] == "add":
            state.setdefault(r["imo"], Listing(r["imo"], r["date"], r["source"]))
        elif r["action"] == "remove":
            state.pop(r["imo"], None)
    return state


def first_add_in_window(start: date, end: date, sources: tuple[str, ...] = SOURCES,
                        path: Path | None = None) -> dict[int, Listing]:
    """IMOs whose first add by any of `sources` falls in (start, end]."""
    out: dict[int, Listing] = {}
    for r in sorted(_actions(path), key=lambda r: (r["imo"], r["date"])):
        if r["source"] not in sources or r["action"] != "add":
            continue
        if r["imo"] in out or not (start < r["date"] <= end):
            continue
        out[r["imo"]] = Listing(r["imo"], r["date"], r["source"])
    return out


def labels(T: date, population_imos: list[int], sources: tuple[str, ...] = SOURCES,
           horizon_days: int = config.HORIZON_DAYS, path: Path | None = None) -> list[dict]:
    """Label every population IMO at cutoff T.

    Population exclusion always uses all three lists regardless of `sources` (CLAUDE.md rule 3), so the
    OFAC-only sensitivity table cannot smuggle in hulls the EU or UK had already listed.
    """
    excluded = listed_as_of(T, SOURCES, path)
    positives = first_add_in_window(T, T + timedelta(days=horizon_days), sources, path)
    out = []
    for imo in population_imos:
        if imo in excluded:
            continue
        hit = positives.get(imo)
        out.append({"cutoff": T, "imo": imo, "label": 1 if hit else 0,
                    "designation_date": hit.date if hit else None,
                    "source": hit.source if hit else None})
    return out


def positives_table(cutoffs: list[date], population_by_cutoff: dict[date, list[int]],
                    path: Path | None = None) -> list[dict]:
    """Per cutoff: population size and positives under both label sets, plus the R12 exclusion count."""
    rows = []
    for T in cutoffs:
        pop = population_by_cutoff.get(T, [])
        union = labels(T, pop, SOURCES, path=path)
        ofac_only = labels(T, pop, ("OFAC",), path=path)
        eu_uk_listed = listed_as_of(T, ("EU", "UK"), path)
        rows.append({
            "cutoff": T.isoformat(), "population_observed": len(pop),
            "population_in_scope": len(union),
            "excluded_already_listed": len(pop) - len(union),
            "positives_union": sum(r["label"] for r in union),
            "positives_ofac_only": sum(r["label"] for r in ofac_only),
            "ofac_positives_already_eu_uk_listed": sum(
                1 for r in labels(T, pop, ("OFAC",), path=path) if r["label"] and r["imo"] in eu_uk_listed),
        })
    return rows


def population_imos_by_cutoff(cutoffs: list[date], con: duckdb.DuckDBPyConnection | None = None,
                              feature_window_days: int = config.FEATURE_WINDOW_DAYS) -> dict[date, list[int]]:
    """Interim population keyed on IMO (Phase 3 replaces this with hull_id).

    Observed = a kept MMSI with a `vessel_day` row in [T - window, T]. IMO = the modal valid IMO from
    `ais_static` messages with `observed_at <= T`, so the mapping itself is point-in-time.
    """
    from shadowfleet.ingest.dma import connect
    from shadowfleet.phase1 import _glob

    con = con or connect()
    out: dict[date, list[int]] = {}
    for T in cutoffs:
        start = T - timedelta(days=feature_window_days)
        rows = con.execute(f"""
            WITH seen AS (
              SELECT DISTINCT mmsi FROM read_parquet('{_glob('vessel_day')}', hive_partitioning=true)
              WHERE kept AND day BETWEEN DATE '{start}' AND DATE '{T}'
            ), imo AS (
              SELECT mmsi, mode(imo) AS imo
              FROM read_parquet('{_glob('ais_static')}', hive_partitioning=true)
              WHERE imo IS NOT NULL AND imo > 0 AND observed_at <= TIMESTAMP '{T} 23:59:59'
              GROUP BY mmsi
            )
            SELECT DISTINCT imo.imo FROM seen JOIN imo USING (mmsi) WHERE imo.imo IS NOT NULL
        """).fetchall()
        out[T] = [int(r[0]) for r in rows if imo_valid(r[0])]
    return out


def build(years: range | None = None) -> dict:
    """Phase 2 end to end: OFAC XML + archive, EU, UK -> sanctions_actions.parquet + the positives table."""
    from shadowfleet.util import net

    rows: list[dict] = []
    stats: dict = {}
    xml_path = config.HTTP_CACHE_DIR / "ofac" / "sdn_advanced.xml"
    if not xml_path.exists():
        with net.client(timeout=300) as c:
            ofac._cached_download(c, config.OFAC_SDN_ADVANCED_XML_URLS, "sdn_advanced.xml")
    xml_rows = parse_advanced_xml(xml_path)
    rows += xml_rows
    stats["ofac_xml"] = {"rows": len(xml_rows), "imos": len({r["imo"] for r in xml_rows})}

    arch = archive_rows(years or range(2022, date.today().year + 1))
    rows += arch
    stats["ofac_archive"] = {"rows": len(arch), "imos": len({r["imo"] for r in arch})}

    celex = load_celex_dates()
    stats["celex_entries"] = len(celex)
    with net.client(timeout=300) as c:
        for source, slug, program in (("EU", config.OPENSANCTIONS_EU_DATASET, config.OPENSANCTIONS_EU_PROGRAM),
                                      ("UK", config.OPENSANCTIONS_UK_VESSELS, None)):
            idx = opensanctions.fetch_index(c, slug)
            path = opensanctions.download_resource(c, slug, idx, "ftm.json")
            src_rows = opensanctions_rows(path, source, program, celex) if path else []
            rows += src_rows
            stats[source.lower()] = {"slug": slug, "rows": len(src_rows),
                                     "imos": len({r["imo"] for r in src_rows})}
    stats["table"] = write_actions(rows)

    # cross-check: add dates from the XML vs the archive for the same IMO
    xml_adds = {r["imo"]: r["date"] for r in xml_rows if r["action"] == "add"}
    arch_adds = {r["imo"]: r["date"] for r in arch if r["action"] == "add"}
    both = set(xml_adds) & set(arch_adds)
    deltas = sorted((abs((xml_adds[i] - arch_adds[i]).days), i) for i in both)
    stats["xml_vs_archive"] = {
        "imos_in_both": len(both),
        "agree_within_7_days": sum(1 for d, _ in deltas if d <= 7),
        "worst": [{"imo": i, "days_apart": d, "xml": xml_adds[i].isoformat(),
                   "archive": arch_adds[i].isoformat()} for d, i in deltas[-5:]],
    }

    w = config.load_window()
    if w:
        cutoffs = config.monthly_cutoffs(w, date.today())
        pops = population_imos_by_cutoff(cutoffs)
        stats["positives_by_cutoff"] = positives_table(cutoffs, pops)
        csv_path = config.REPORTS_DIR / "phase2_positives.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        keys = list(stats["positives_by_cutoff"][0])
        with open(csv_path, "w", newline="") as f:
            import csv as _csv

            wr = _csv.DictWriter(f, fieldnames=keys)
            wr.writeheader()
            wr.writerows(stats["positives_by_cutoff"])
        stats["positives_csv"] = str(csv_path)
    return stats
