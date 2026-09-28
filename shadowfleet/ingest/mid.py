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


# Ship-station MIDs live in 201-775 (ITU-R M.585). A 3-digit token outside that is not a MID.
MID_MIN, MID_MAX = 201, 775

# A MID table that cannot flag the largest tanker registries is not a MID table. These are the codes whose
# absence the first two versions of this parser hid: Liberia, Panama, Malta, Marshall Islands, Cyprus,
# Singapore, Bahamas and Russia, which between them fly most of the fleet this project looks at.
REQUIRED_MIDS = {636: "LBR", 352: "PAN", 249: "MLT", 538: "MHL", 209: "CYP", 563: "SGP", 311: "BHS",
                 273: "RUS"}


def parse_itu_html(text: str) -> tuple[list[tuple[int, str]], list[str]]:
    """`(rows, unparsed)`. One row per MID, and every cell it could not read.

    ITU gives several MIDs to one country and puts them all in the first cell, newline-separated:
    `'209\r\n\r\n        210'` is Cyprus. The first version matched a cell that was exactly three digits, so
    every multi-MID country was dropped without a word: Liberia, Panama, Malta, Singapore, Cyprus, Denmark,
    Norway, Sweden, the Netherlands, Greece, France, the UK. 224 rows parsed where there are about 380, and
    the result looked entirely reasonable. Hence the second return value: a cell with digits in it that this
    cannot read is reported, never skipped.
    """
    out: list[tuple[int, str]] = []
    unparsed: list[str] = []
    for row in ROW.findall(text):
        cells = [html.unescape(TAG.sub("", c)).strip() for c in CELL.findall(row)]
        if len(cells) < 2 or not any(ch.isdigit() for ch in cells[0]):
            continue  # header or a row with no MID in it at all
        name = re.sub(r"\s+", " ", cells[1])
        mids = [int(m) for m in re.findall(r"\d{3}", cells[0])]
        if re.fullmatch(r"[\d\s]+", cells[0]) and mids and all(MID_MIN <= m <= MID_MAX for m in mids):
            out += [(m, name) for m in mids]
        else:
            unparsed.append(f"{cells[0]!r} -> {name}")
    return out, unparsed


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
    rows, unparsed = parse_itu_html(r.text)
    # Fail loudly, three ways, because this source has now broken quietly twice: once returning nothing
    # (mid.csv kept its header and every flag stayed null), once returning a plausible 224-row subset with
    # Liberia and Panama missing. mid.csv is left untouched on any of them.
    if not rows:
        raise SystemExit(
            f"parsed 0 MID rows from {config.MID_SOURCE_URL} ({len(r.text)} bytes). The page moved or its "
            "markup changed; fix parse_itu_html or MID_SOURCE_URL. mid.csv left untouched.")
    if unparsed:
        raise SystemExit(f"{len(unparsed)} MID cells could not be read, so the table would be incomplete: "
                         + "; ".join(unparsed[:5]))
    by_mid = dict(rows)
    missing = {m: iso for m, iso in REQUIRED_MIDS.items() if m not in by_mid}
    if missing:
        raise SystemExit(f"parsed {len(rows)} MIDs but the major tanker registries are missing: {missing}. "
                         "The page's layout changed; fix parse_itu_html.")
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


# ITU allocates MID 306 to three Dutch Caribbean territories at once (Bonaire/Sint Eustatius/Saba, Curacao,
# Sint Maarten), so an MMSI cannot tell them apart. Declared here rather than left to whichever row the page
# happens to list last, because Curacao is the only one of the three with a substantial ship registry and the
# only one on `config.ITF_FOC_FLAGS_SENSITIVITY`, so row order would silently flip that Phase 6 arm. Any
# other duplicate MID is a new ambiguity and fails loudly.
SHARED_MIDS = {306: "CUW"}


@lru_cache(maxsize=1)
def load() -> dict[int, str]:
    if not config.MID_CSV.exists():
        return {}
    with open(config.MID_CSV) as f:
        lines = [ln for ln in f if not ln.startswith("#")]
    out: dict[int, str] = {}
    clashes: dict[int, set[str]] = {}
    for r in csv.DictReader(lines):
        if not r["iso3"]:
            continue
        m = int(r["mid"])
        if m in out and out[m] != r["iso3"]:
            clashes.setdefault(m, {out[m]}).add(r["iso3"])
            continue
        out[m] = r["iso3"]
    undeclared = {m: sorted(v) for m, v in clashes.items() if m not in SHARED_MIDS}
    if undeclared:
        raise SystemExit(f"MIDs allocated to more than one territory and not declared in SHARED_MIDS: "
                         f"{undeclared}. Pick one and say why, or the flag depends on row order.")
    # Only for MIDs the table actually carries: unconditionally adding them made an empty mid.csv report one
    # row, which hides the "no flags resolved" finding in reports/phase3.md behind a number that looks real.
    out.update({m: iso for m, iso in SHARED_MIDS.items() if m in out})
    return out


def flag_from_mmsi(mmsi: int) -> str | None:
    """Ship-station MMSIs are MIDxxxxxx (9 digits, first digit 2-7)."""
    s = str(mmsi)
    if len(s) != 9 or s[0] not in "234567":
        return None
    return load().get(int(s[:3]))
