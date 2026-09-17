"""Single source of truth for paths, windows, filters and source endpoints.

Every value carries a comment saying where it came from. Values marked PLACEHOLDER are
not facts; the phase named next to them replaces them with a sourced value.
"""

from __future__ import annotations

import calendar
import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

try:  # .env is optional; tests run without it
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

# --------------------------------------------------------------------------- paths
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("SHADOWFLEET_DATA", REPO_ROOT / "data"))
RAW_DIR = DATA_DIR / "raw"
DMA_RAW_DIR = RAW_DIR / "dma"
PARQUET_DIR = DATA_DIR / "parquet"
CACHE_DIR = DATA_DIR / "cache"
GFW_CACHE_DIR = CACHE_DIR / "gfw"
HTTP_CACHE_DIR = CACHE_DIR / "http"
STATE_DIR = DATA_DIR / "state"
TMP_DIR = DATA_DIR / "tmp"
REPORTS_DIR = REPO_ROOT / "reports"
LOG_DIR = REPORTS_DIR / "logs"
PROBE_DIR = REPORTS_DIR / "probes"
WINDOW_FILE = REPO_ROOT / "config" / "window.json"

# --------------------------------------------------------------------------- backtest (ADR-11)
FEATURE_WINDOW_DAYS = 180  # plan ADR-11
HORIZON_DAYS = 182  # plan ADR-11
# ADR-11 addendum: below this the backtest has too few supervised cutoffs; shrinking further needs Adam.
MIN_WINDOW_MONTHS_WITHOUT_SIGNOFF = 21
TOP_K = (25, 50, 100)  # plan section 4
# ADR-17 (Adam, Sep 17 2026): pre-registered regime break. Strait of Hormuz closed after the Feb 28 2026 strikes
# (straits.live Day 1; Al Jazeera 2026-08-27). Metrics are reported before and after it; it is not a window bound.
REGIME_BREAKS = {"hormuz_closure": "2026-02-28"}
FORWARD_TEST_SCORING_DATE = "2026-10-01"  # ADR-17: top-50 committed to git on or after this date

# --------------------------------------------------------------------------- disk (ADR-13)
MIN_FREE_GB = float(os.environ.get("SHADOWFLEET_MIN_FREE_GB", "25"))  # pause ingest below this
RESUME_FREE_GB = MIN_FREE_GB + 5  # hysteresis so the guard does not flap
RECOMMENDED_FREE_GB = 100  # SETUP.md; doctor warns below this
# WSL2 stores Linux files in a sparse virtual disk (ext4.vhdx) on a Windows drive, so `df` inside WSL
# reports the virtual size (about 1 TB), not the real free space. The guard also checks the Windows drive
# that holds the virtual disk: C: by default; set SHADOWFLEET_HOST_DISK=/mnt/d if the distro was moved.
_default_host = "/mnt/c" if Path("/mnt/c").is_dir() and "microsoft" in os.uname().release.lower() else ""
HOST_DISK_PATH = os.environ.get("SHADOWFLEET_HOST_DISK", _default_host)
# WSL gets ~15 GB by default on a 32 GB host; leave room for probes running beside the ingest (OOM, Sep 17 2026).
DUCKDB_MEMORY_LIMIT = os.environ.get("SHADOWFLEET_DUCKDB_MEMORY", "8GB")
DUCKDB_THREADS = int(os.environ.get("SHADOWFLEET_DUCKDB_THREADS", "8"))

# --------------------------------------------------------------------------- DMA (ADR-2, ADR-14)
# Tried in order. Sep 17 2026: web.ais.dk timed out from Adam's machine; DMA serves the daily files from an S3
# bucket (http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/aisdk-YYYY-MM-DD.zip, per github.com/Luke3520/
# ais-pipeline; ~590-750 MB zipped, ~17M rows per day). S3 answers with an XML listing, the old site with HTML.
DMA_INDEX_URLS = [
    "http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/",
    "http://aisdata.ais.dk/",
    "http://web.ais.dk/aisdata/",
]
DMA_DAILY_RE = r"aisdk[-_](\d{4}-\d{2}-\d{2})\.zip"
DMA_MONTHLY_RE = r"aisdk[-_](\d{4}-\d{2})\.zip"
DMA_MAX_WORKERS = 2  # phase1-prompt: never more than two concurrent downloads
DMA_HTTP_TIMEOUT_S = 120
DMA_CSV_BLOCK_BYTES = 64 * 1024 * 1024
DMA_MALFORMED_FLAG_SHARE = 0.05  # phase1-prompt task 1
# Timestamp format in DMA CSVs ("dd/mm/yyyy HH:MM:SS"); Phase 0 verifies and extends if needed.
DMA_TIMESTAMP_FORMATS = ["%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"]

# Canonical column -> accepted header spellings (compared after lower-casing and stripping "#", spaces).
# From the DMA CSV header as documented on web.ais.dk; Phase 0 logs any header that does not map.
DMA_COLUMN_ALIASES: dict[str, list[str]] = {
    "ts_raw": ["timestamp", "# timestamp"],
    "mobile_type": ["type of mobile", "mobile type"],
    "mmsi": ["mmsi"],
    "lat": ["latitude", "lat"],
    "lon": ["longitude", "lon", "long"],
    "nav_status": ["navigational status", "nav status", "navigationalstatus"],
    "rot": ["rot"],
    "sog": ["sog"],
    "cog": ["cog"],
    "heading": ["heading"],
    "imo": ["imo"],
    "callsign": ["callsign", "call sign"],
    "name": ["name", "ship name"],
    "ship_type": ["ship type", "shiptype"],
    "cargo_type": ["cargo type", "cargotype"],
    "width": ["width"],
    "length": ["length"],
    "pos_fix_type": ["type of position fixing device", "position fixing device"],
    "draught": ["draught", "draft"],
    "destination": ["destination"],
    "eta": ["eta"],
    "data_source": ["data source type", "data source"],
    "dim_a": ["a", "size a"],
    "dim_b": ["b", "size b"],
    "dim_c": ["c", "size c"],
    "dim_d": ["d", "size d"],
}
DMA_REQUIRED_COLUMNS = ["ts_raw", "mmsi", "lat", "lon"]
DMA_STATIC_COLUMNS = [
    "imo", "callsign", "name", "ship_type", "cargo_type", "length", "width", "draught",
    "destination", "eta", "dim_a", "dim_b", "dim_c", "dim_d",
]
DMA_DYNAMIC_COLUMNS = ["lat", "lon", "sog", "cog", "heading", "rot", "nav_status", "mobile_type",
                       "pos_fix_type", "data_source"]

# Tanker-class filter (plan R5, phase1-prompt task 1). Values compared lower-case.
TANKER_SHIP_TYPES = {"tanker"}
CARGO_SHIP_TYPES = {"cargo"}
# AIS type 8x/7x second digit = hazard category; MARPOL Annex II categories X/Y/Z/OS = noxious liquids.
# Substring match; Phase 0 prints the observed cargo_type values so this list can be checked.
HAZARDOUS_CARGO_SUBSTRINGS = ["hazard", "category x", "category y", "category z", "category os"]
MIN_TANKER_LENGTH_M = 100
EXTRA_MMSI_ALLOWLIST: set[int] = set()  # Phase 3 may add hulls that mis-report type
# Non-vessel AIS stations excluded from jump baselines and vessel_day counts.
# Observed DMA values on 2026-09-03: Class A, Class B, Base Station, AtoN, SAR Airborne,
# Search and Rescue Transponder, Man Overboard Device.
NON_VESSEL_MOBILE_TYPES = {"base station", "aton", "sar airborne", "search and rescue transmitter",
                           "search and rescue transponder", "man overboard device"}

# Downsampling (ADR-14): irreversible, so the finer interval is the default.
DOWNSAMPLE_S = 60
SLOW_DOWNSAMPLE_S = 30
SLOW_SOG_KN = 3.0

# Spoof artefacts (phase1-prompt task 7)
JUMP_SPEED_KN = 50.0
JUMP_MIN_DIST_KM = 1.0  # ignore sub-km jitter between near-simultaneous reports from different stations
JUMP_CELL_DEG = 0.5
# Plausible-position box for the DMA footprint (Baltic, Danish straits, North Sea). Generous on purpose:
# only positions outside it (or 0,0, or AIS "not available" 91/181) count as bad.
BBOX_LAT = (48.0, 72.0)
BBOX_LON = (-12.0, 35.0)

# PLACEHOLDER (refined in Phase 4b from observed anchoring clusters): rough box around the Skagen
# anchorage east of Grenen, used only for the Phase 1 STS-readiness count.
SKAGEN_ANCHORAGE_BBOX = {"lat": (57.55, 57.90), "lon": (10.45, 11.00)}

# --------------------------------------------------------------------------- GFW (ADR-3, ADR-16)
GFW_BASE_URL = "https://gateway.api.globalfishingwatch.org/v3"
GFW_TOKEN = os.environ.get("GFW_TOKEN", "")
GFW_DATASETS = {  # GFW API docs, Sep 2026; ':latest' resolves server-side, version logged per response
    "GAP": "public-global-gaps-events:latest",
    "ENCOUNTER": "public-global-encounters-events:latest",
    "LOITERING": "public-global-loitering-events:latest",
    "PORT_VISIT": "public-global-port-visits-events:latest",
}
GFW_IDENTITY_DATASET = "public-global-vessel-identity:latest"
GFW_PAGE_LIMIT = 100
GFW_MAX_RETRIES = 6
GFW_TIMEOUT_S = 60
# Registry fields that are as-of-now (S&P ownership since Jun 2026). Never features (leakage test 2).
GFW_FORBIDDEN_FEATURE_FIELDS = {"registryOwners", "registryPublicAuthorizations", "owner", "operator"}
NO_GFW = os.environ.get("SHADOWFLEET_NO_GFW", "0") == "1"

# --------------------------------------------------------------------------- OFAC (ADR-4, ADR-15)
# Candidate URLs, tried in order; Phase 0 records which one answered.
OFAC_SDN_CSV_URLS = [
    "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV",
    "https://www.treasury.gov/ofac/downloads/sdn.csv",
]
OFAC_SDN_ADVANCED_XML_URLS = [
    "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN_ADVANCED.XML",
    "https://www.treasury.gov/ofac/downloads/sanctions/1.0/sdn_advanced.xml",
]
# ofac.treasury.gov "Archive of Changes to the SDN List" (checked Sep 17 2026):
# text files 1994-2023, PDF 2001-present. yy = two-digit year.
OFAC_CHANGES_TXT_URL = "https://www.treasury.gov/ofac/downloads/sdnnew{yy}.txt"
OFAC_CHANGES_PDF_URL = "https://www.treasury.gov/ofac/downloads/sdnnew{yy}.pdf"
OFAC_CHANGES_LAST_TEXT_YEAR = 2023
# sdn.csv has no header row; column order per OFAC SDN data specification.
OFAC_SDN_CSV_COLUMNS = ["ent_num", "sdn_name", "sdn_type", "program", "title", "call_sign", "vess_type",
                        "tonnage", "grt", "vess_flag", "vess_owner", "remarks"]

# --------------------------------------------------------------------------- OpenSanctions (ADR-15)
# opensanctions.org/docs/bulk/updates (Sep 2026): latest and dated (YYYYMMDD) exports, ~Jul 2021 onward.
OPENSANCTIONS_BASE = "https://data.opensanctions.org/datasets"
OPENSANCTIONS_MARITIME = "maritime"
OPENSANCTIONS_UK_VESSELS = "gb_fcdo_sanctions"  # UK FCDO list; vessel entities carry listingDate
OPENSANCTIONS_OFAC = "us_ofac_sdn"
OPENSANCTIONS_EU_PROGRAM = "EU-MARE"  # Annex XLII to Reg 833/2014; dataset slug located in Phase 0
OPENSANCTIONS_LICENCE = "CC BY-NC 4.0 (non-commercial)"

# --------------------------------------------------------------------------- MID (Phase 0 task 8)
MID_SOURCE_URL = "https://www.itu.int/en/ITU-R/terrestrial/fmd/Pages/mid.aspx"
MID_CSV = REPO_ROOT / "shadowfleet" / "ingest" / "mid.csv"

# --------------------------------------------------------------------------- domain lists
# PLACEHOLDER (sourced in Phase 3): list from the Sep 10 plan; each entry needs a citation before 5a freezes.
CONVENIENCE_FLAGS = ["GAB", "CMR", "COM", "SLE", "SWZ", "GUY", "COK", "PLW", "VUT"]
# Plan R3 / Phase 4a. Polygons are added in Phase 4a with sources.
RUSSIAN_PORTS = ["Primorsk", "Ust-Luga", "Vysotsk", "St Petersburg", "Novorossiysk", "Tuapse", "Taman",
                 "Murmansk", "Kozmino"]

# --------------------------------------------------------------------------- LLM (ADR-9)
LLAMA_SERVER_URL = os.environ.get("LLAMA_SERVER_URL", "http://127.0.0.1:8080")
LLM_CONTEXT = 8192
LLM_PRIMARY = "Gemma 4 12B instruct, Q4_K_M GGUF"  # verify exact file on Hugging Face at build time
LLM_FALLBACK = "Qwen3-14B instruct, Q4_K_M GGUF"


# --------------------------------------------------------------------------- window and cutoffs
@dataclass(frozen=True)
class Window:
    start: date
    end: date


def load_window(path: Path | None = None) -> Window | None:
    """Window written by `make window-gate`; None until Phase 0 has run the gate."""
    path = path or WINDOW_FILE
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    return Window(date.fromisoformat(raw["start"]), date.fromisoformat(raw["end"]))


def month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def evaluation_limit(today: date) -> date:
    """Last day whose 182-day horizon has closed."""
    return today - timedelta(days=HORIZON_DAYS)


def monthly_cutoffs(window: Window, today: date) -> list[date]:
    """Cutoff rule (PREREG): last day of every calendar month T with
    window.start + feature window <= T <= min(window.end, today - horizon)."""
    first_allowed = window.start + timedelta(days=FEATURE_WINDOW_DAYS)
    last_allowed = min(window.end, evaluation_limit(today))
    out: list[date] = []
    t = month_end(first_allowed)
    while t <= last_allowed:
        out.append(t)
        nxt = t + timedelta(days=1)
        t = month_end(nxt)
    return out


def supervised_cutoffs(cutoffs: list[date]) -> list[date]:
    """Cutoffs that have at least one earlier cutoff whose horizon closed on or before them."""
    if not cutoffs:
        return []
    first = cutoffs[0]
    return [t for t in cutoffs if first + timedelta(days=HORIZON_DAYS) <= t]
