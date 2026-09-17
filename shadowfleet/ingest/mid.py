"""ITU Maritime Identification Digits -> ISO3, vendored to shadowfleet/ingest/mid.csv (Phase 0 task 8)."""

from __future__ import annotations

import csv
import html
import re
from datetime import date
from functools import lru_cache

import pycountry

from shadowfleet import config
from shadowfleet.util import net, probes

ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")

# ITU names that pycountry cannot resolve by fuzzy search; extend when the probe reports misses.
MANUAL_ISO3 = {
    "Bolivia": "BOL", "Iran": "IRN", "Korea (Republic of)": "KOR",
    "Democratic People's Republic of Korea": "PRK", "Russian Federation": "RUS", "Moldova": "MDA",
    "Tanzania": "TZA", "Venezuela": "VEN", "Viet Nam": "VNM", "Syrian Arab Republic": "SYR",
    "Lao People's Democratic Republic": "LAO", "Micronesia": "FSM", "Türkiye": "TUR", "Turkey": "TUR",
    "Vatican": "VAT", "Congo (Republic of the)": "COG", "Democratic Republic of the Congo": "COD",
}


def parse_itu_html(text: str) -> list[tuple[int, str]]:
    out = []
    for row in ROW.findall(text):
        cells = [html.unescape(TAG.sub("", c)).strip() for c in CELL.findall(row)]
        if len(cells) >= 2 and re.fullmatch(r"\d{3}", cells[0]):
            out.append((int(cells[0]), re.sub(r"\s+", " ", cells[1])))
    return out


def to_iso3(name: str) -> str | None:
    base = re.sub(r"\s*\(.*?\)\s*", " ", name).strip(" -–")
    for key in (name, base):
        if key in MANUAL_ISO3:
            return MANUAL_ISO3[key]
    try:
        return pycountry.countries.lookup(base).alpha_3
    except LookupError:
        pass
    try:
        hits = pycountry.countries.search_fuzzy(base)
        return hits[0].alpha_3 if hits else None
    except LookupError:
        return None


def probe() -> dict:
    with net.client() as c:
        r = net.get(c, config.MID_SOURCE_URL)
        r.raise_for_status()
    rows = parse_itu_html(r.text)
    unmatched = []
    with open(config.MID_CSV, "w", newline="") as f:
        f.write(f"# source: {config.MID_SOURCE_URL} fetched {date.today().isoformat()}\n")
        w = csv.writer(f)
        w.writerow(["mid", "itu_name", "iso3"])
        for mid, name in rows:
            iso = to_iso3(name)
            if iso is None:
                unmatched.append(name)
            w.writerow([mid, name, iso or ""])
    out = {"rows": len(rows), "unmatched": unmatched, "csv": str(config.MID_CSV.relative_to(config.REPO_ROOT))}
    probes.write("mid", out)
    return out


@lru_cache(maxsize=1)
def load() -> dict[int, str]:
    if not config.MID_CSV.exists():
        return {}
    with open(config.MID_CSV) as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    return {int(r["mid"]): r["iso3"] for r in csv.DictReader(lines) if r["iso3"]}


def flag_from_mmsi(mmsi: int) -> str | None:
    """Ship-station MMSIs are MIDxxxxxx (9 digits, first digit 2-7)."""
    s = str(mmsi)
    if len(s) != 9 or s[0] not in "234567":
        return None
    return load().get(int(s[:3]))
