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
from shadowfleet.util.store import rel_path

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
    # Dependencies with their own MID but no ISO3 of their own: they take the parent's code, because the
    # feature asks which registry a hull flies under and these are not separate registries.
    "Azores": "PRT", "Madeira": "PRT", "Alaska (State of)": "USA", "Alaska": "USA",
    # French southern and antarctic territories share one ISO3.
    "Adelie Land": "ATF", "Saint Paul and Amsterdam Islands": "ATF", "Crozet Archipelago": "ATF",
    "Kerguelen Islands": "ATF",
    # Ascension and Tristan da Cunha sit under Saint Helena in ISO 3166.
    "Ascension Island": "SHN", "Tristan da Cunha": "SHN",
    "Republic of Naoero": "NRU",  # ITU's spelling of Nauru
    # Territories pycountry will not match under ITU's spelling. Each is a registry in its own right, and
    # each one silently resolved to its parent (VIR->USA, PCN->GBR, WLF/REU/GUF->FRA, SHN->GBR) while the
    # parent was still a fallback. That is why it no longer is.
    "United States Virgin Islands": "VIR", "Pitcairn Island": "PCN",
    "Wallis and Futuna Islands": "WLF", "Reunion": "REU", "Guiana": "GUF", "Saint Helena": "SHN",
}


def parse_itu_html(text: str) -> list[tuple[int, str]]:
    out = []
    for row in ROW.findall(text):
        cells = [html.unescape(TAG.sub("", c)).strip() for c in CELL.findall(row)]
        if len(cells) >= 2 and re.fullmatch(r"\d{3}", cells[0]):
            out.append((int(cells[0]), re.sub(r"\s+", " ", cells[1])))
    return out


def _strip_parens(name: str) -> str:
    return re.sub(r"\s*\(.*?\)\s*", " ", name).strip(" -–")


def to_iso3(name: str) -> str | None:
    """ISO3 for one ITU allocation name.

    ITU writes a dependency as "Parent - Territory" ("Denmark - Faroe Islands",
    "China (People's Republic of) - Hong Kong (Special Administrative Region of China)"). Matching the whole
    string fails, which left 44 of 224 MIDs with no flag, among them Hong Kong, Gibraltar, Bermuda, the
    Cayman Islands, Madeira and the Faroe Islands: major tanker registries, six of them on the ITF list. The
    territory is the registry a hull flies under, so it is tried first and the parent only as a fallback.
    """
    territory = name.split(" - ")[-1].strip()  # the parent is NOT a fallback: see the note in MANUAL_ISO3
    candidates = [territory, _strip_parens(territory)]

    for key in candidates:
        if key in MANUAL_ISO3:
            return MANUAL_ISO3[key]
    for key in candidates:
        if not key:
            continue
        try:
            return pycountry.countries.lookup(key).alpha_3
        except LookupError:
            pass
    for key in candidates:
        if not key:
            continue
        try:
            hits = pycountry.countries.search_fuzzy(key)
            if hits:
                return hits[0].alpha_3
        except LookupError:
            pass
    return None


def probe() -> dict:
    with net.client() as c:
        r = net.get(c, config.MID_SOURCE_URL)
        r.raise_for_status()
    rows = parse_itu_html(r.text)
    # Fail loudly. The first version wrote the header, found nothing, and reported rows: 0 as success, which
    # left a mid.csv that exists and looks vendored while every flag stayed null for eleven days. A source
    # that parses to nothing is a broken source, not an empty one.
    if not rows:
        raise SystemExit(
            f"parsed 0 MID rows from {config.MID_SOURCE_URL} ({len(r.text)} bytes). The page moved or its "
            "markup changed; fix parse_itu_html or MID_SOURCE_URL. mid.csv left untouched.")
    unmatched = []
    tmp = config.MID_CSV.with_name(config.MID_CSV.name + ".part")  # swap in only once it parsed
    with open(tmp, "w", newline="") as f:
        f.write(f"# source: {config.MID_SOURCE_URL} fetched {date.today().isoformat()}\n")
        w = csv.writer(f)
        w.writerow(["mid", "itu_name", "iso3"])
        for mid, name in rows:
            iso = to_iso3(name)
            if iso is None:
                unmatched.append(name)
            w.writerow([mid, name, iso or ""])
    tmp.replace(config.MID_CSV)
    load.cache_clear()
    out = {"rows": len(rows), "unmatched": unmatched, "csv": rel_path(config.MID_CSV)}
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
