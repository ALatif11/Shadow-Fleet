"""OFAC SDN: current list, advanced XML presence check, and the yearly change archive parser (ADR-15).

The change-archive parser is a tolerant line-based state machine. It was written against the
documented structure (dated sections, "The following ... have been added/removed/changed" headings,
entries ending in "]." or ")."), not against the live 2025 PDF, which the planning sandbox could not
reach. Phase 0 runs it on the real file, saves a sample page, and fixes it with that page as a fixture.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from shadowfleet import config
from shadowfleet.util import net, probes
from shadowfleet.util.ids import extract_imos

log = logging.getLogger(__name__)
NULL = "-0-"


# ---------------------------------------------------------------------- current SDN CSV
def parse_sdn_csv(text: str) -> list[dict]:
    rows = []
    for rec in csv.reader(io.StringIO(text)):
        if len(rec) < len(config.OFAC_SDN_CSV_COLUMNS):
            continue
        d = {k: (v.strip() if v.strip() != NULL else None)
             for k, v in zip(config.OFAC_SDN_CSV_COLUMNS, rec, strict=False)}
        rows.append(d)
    return rows


def sdn_vessels(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        if (r.get("sdn_type") or "").lower() != "vessel":
            continue
        imos = extract_imos(r.get("remarks") or "")
        out.append({"ent_num": r["ent_num"], "name": r["sdn_name"], "program": r["program"],
                    "imo": imos[0] if imos else None, "vess_type": r.get("vess_type"),
                    "vess_flag": r.get("vess_flag")})
    return out


# ---------------------------------------------------------------------- change archive
# Text archives use 2-digit years ("01/05/23:"), PDFs 4-digit ones (checked against sdnnew23.txt, Sep 2026).
DATE_LINE = re.compile(r"^\s*(\d{1,2}/\d{1,2}/(?:\d{4}|\d{2}))\s*:?\s*$")
DATE_LINE_LONG = re.compile(
    r"^\s*((?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},\s+\d{4})\s*:?\s*$"
)
ACTION_PATTERNS = [
    ("remove", re.compile(r"\b(removed|deleted|delisted)\b", re.I)),
    ("modify", re.compile(r"\b(changed|updated|amended|modified)\b", re.I)),
    ("add", re.compile(r"\b(added|designated)\b", re.I)),
]
HEADING = re.compile(r"^\s*(the following|\w+\s*:$)", re.I)
NOISE = [
    re.compile(r"^\s*page\s+\d+(\s+of\s+\d+)?\s*$", re.I),
    re.compile(r"^\s*-?\s*\d+\s*-?\s*$"),
    re.compile(r"^\s*office of foreign assets control\s*$", re.I),
    re.compile(r"^\s*specially designated nationals (list )?update\s*$", re.I),
]
PROGRAM_TAG = re.compile(r"\[([A-Z0-9][A-Z0-9 _./-]*?)\]")
TO_MARK = re.compile(r"^\s*-\s*to\s*-\s*$", re.I)
ENTRY_END = re.compile(r"(\]|\))\.\s*$")


@dataclass
class ChangeRow:
    date: str
    action: str | None
    name: str
    imo: int | None
    programs: list[str]
    is_vessel: bool
    raw: str
    old_raw: str | None = None


@dataclass
class ParseStats:
    dates: int = 0
    entries: int = 0
    vessel_entries: int = 0
    vessel_entries_with_imo: int = 0
    entries_without_action: int = 0
    entries_without_date: int = 0
    actions: dict = field(default_factory=dict)


def _parse_date(s: str) -> str:
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(s)


def _entry_name(text: str) -> str:
    """OFAC names are upper case; the vessel description that follows is not ("ALPHA Crude Oil Tanker")."""
    t = re.sub(r"^\s*-\s*to\s*-\s*", "", text).strip()
    head = re.split(r"\s+\(|,|;|\s+\[", t, maxsplit=1)[0]
    tokens = []
    for tok in head.split():
        if any(ch.islower() for ch in tok):
            break
        tokens.append(tok)
    return " ".join(tokens or head.split()[:1])[:200]


def _is_vessel(text: str) -> bool:
    return bool(re.search(r"\(vessel\)|vessel registration identification", text, re.I))


HEADING_START = re.compile(r"^\s*the following\b", re.I)


def _join_wrapped_headings(text: str) -> str:
    """Headings wrap: "The following [X] entries have been" / "added to OFAC's SDN List:".

    Join each heading onto one line so the action verb is visible to the state machine.
    """
    out: list[str] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if HEADING_START.match(line) and not line.rstrip().endswith(":"):
            parts = [line.strip()]
            for nxt in lines[i + 1:i + 4]:  # a heading never wraps further than this
                i += 1
                parts.append(nxt.strip())
                if nxt.rstrip().endswith(":"):
                    break
            line = " ".join(p for p in parts if p)
        out.append(line)
        i += 1
    return "\n".join(out)


def parse_changes_text(text: str) -> tuple[list[ChangeRow], ParseStats]:
    rows: list[ChangeRow] = []
    st = ParseStats()
    cur_date: str | None = None
    cur_action: str | None = None
    buf: list[str] = []
    pending_old: str | None = None
    expecting_new = False

    def flush() -> None:
        nonlocal buf, pending_old, expecting_new
        entry = " ".join(x.strip() for x in buf if x.strip())
        buf = []
        if not entry:
            return
        if entry.startswith("(") and rows and not expecting_new and pending_old is None:
            rows[-1].raw = (rows[-1].raw + " " + entry)[:2000]  # wrapped "(Linked To: ...)." tail
            return
        if TO_MARK.match(entry):
            expecting_new = True
            return
        if " -to- " in entry:  # old and new on one logical entry
            old, new = entry.split(" -to- ", 1)
            _emit(new, old)
            return
        if expecting_new and pending_old is not None:
            _emit(entry, pending_old)
            pending_old, expecting_new = None, False
            return
        if cur_action == "modify":
            # hold: the next entry may be its "-to-" counterpart
            if pending_old is not None:
                _emit(pending_old, None)
            pending_old = entry
            return
        _emit(entry, None)

    def _emit(entry: str, old: str | None) -> None:
        st.entries += 1
        if cur_action is None:
            st.entries_without_action += 1
        if cur_date is None:
            st.entries_without_date += 1
        both = entry + " " + (old or "")
        imos = extract_imos(entry) or extract_imos(old or "")
        vessel = _is_vessel(both)
        if vessel:
            st.vessel_entries += 1
            if imos:
                st.vessel_entries_with_imo += 1
        rows.append(ChangeRow(
            date=cur_date or "", action=cur_action, name=_entry_name(entry), imo=imos[0] if imos else None,
            programs=sorted(set(PROGRAM_TAG.findall(both))), is_vessel=vessel, raw=entry[:2000],
            old_raw=old[:2000] if old else None,
        ))

    def close_pending() -> None:
        nonlocal pending_old, expecting_new
        flush()
        if pending_old is not None:
            _emit(pending_old, None)
        pending_old, expecting_new = None, False

    for line in _join_wrapped_headings(text.replace("\r", "")).split("\n"):
        if any(p.match(line) for p in NOISE):
            continue
        m = DATE_LINE.match(line) or DATE_LINE_LONG.match(line)
        if m:
            close_pending()
            cur_date, cur_action = _parse_date(m.group(1)), None
            st.dates += 1
            continue
        if HEADING.match(line) and len(line) < 200:
            act = next((a for a, p in ACTION_PATTERNS if p.search(line)), None)
            if act and re.search(r"the following", line, re.I):
                close_pending()
                cur_action = act
                continue
            if line.strip().endswith(":") and not buf:
                continue  # sub-heading such as "VESSELS:" or "ENTITIES:"
        if not line.strip():
            flush()
            continue
        if TO_MARK.match(line):
            flush()
            expecting_new = True
            continue
        if re.match(r"^\s*-\s*to\s*-\s+\S", line, re.I) and not buf:
            flush()
            expecting_new = True
            line = re.sub(r"^\s*-\s*to\s*-\s+", "", line, flags=re.I)
        buf.append(line)
        if ENTRY_END.search(line) and PROGRAM_TAG.search(" ".join(buf)):
            flush()
    close_pending()
    st.actions = dict(Counter(r.action for r in rows if r.is_vessel))
    return rows, st


def pdf_to_text(pdf_path: Path, out_path: Path | None = None) -> str:
    """Extract text page by page with pypdfium2 (installed with pdfplumber).

    pdfplumber keeps parsed pages in memory; on the 2,143-page 2025 archive that exhausted WSL's RAM
    (exit 137, Sep 17 2026). pypdfium2 is fast and frees each page as it goes. Text is streamed to
    `out_path` when given, so a crash leaves a partial file rather than nothing.
    """
    import pypdfium2 as pdfium  # noqa: PLC0415

    pdf = pdfium.PdfDocument(str(pdf_path))
    n = len(pdf)
    chunks: list[str] = []
    sink = open(out_path, "w", encoding="utf-8") if out_path else None
    try:
        for i in range(n):
            page = pdf[i]
            textpage = page.get_textpage()
            text = textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n")
            textpage.close()
            page.close()
            if sink:
                sink.write(text + "\n\n")
            else:
                chunks.append(text)
            if (i + 1) % 250 == 0 or i + 1 == n:
                log.info("pdf text", extra={"file": pdf_path.name, "page": i + 1, "pages": n})
    finally:
        pdf.close()
        if sink:
            sink.close()
    return out_path.read_text(encoding="utf-8") if out_path else "\n\n".join(chunks)


def _cached_download(c, urls: list[str], name: str) -> tuple[str, Path]:
    dest = config.HTTP_CACHE_DIR / "ofac" / name
    if dest.exists() and dest.stat().st_size > 0:
        return "cache", dest
    last = None
    for u in urls:
        try:
            log.info("downloading", extra={"url": u})
            net.download(c, u, dest)
            return u, dest
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("download failed", extra={"url": u, "err": repr(e)})
    raise RuntimeError(f"all candidates failed for {name}: {last!r}")


def changes_for_year(c, year: int) -> tuple[str, str]:
    """Return (source url, text) for a yearly change archive; text format up to 2023, PDF after."""
    yy = f"{year % 100:02d}"
    if year <= config.OFAC_CHANGES_LAST_TEXT_YEAR:
        try:
            src, p = _cached_download(c, [config.OFAC_CHANGES_TXT_URL.format(yy=yy)], f"sdnnew{yy}.txt")
            return src, p.read_bytes().decode("utf-8", errors="replace")
        except RuntimeError:
            log.warning("text archive failed, trying PDF", extra={"year": year})
    src, p = _cached_download(c, [config.OFAC_CHANGES_PDF_URL.format(yy=yy)], f"sdnnew{yy}.pdf")
    txt_cache = p.with_suffix(".extracted.txt")
    if not txt_cache.exists():
        partial = txt_cache.with_suffix(".partial")
        pdf_to_text(p, partial)
        partial.replace(txt_cache)
    return src, txt_cache.read_text(encoding="utf-8")


# ---------------------------------------------------------------------- advanced XML presence check
def advanced_xml_summary(path: Path, max_elements: int = 5_000_000) -> dict:
    counts: Counter = Counter()
    example_event = None
    for i, (_, el) in enumerate(ET.iterparse(path, events=("end",))):
        tag = el.tag.rsplit("}", 1)[-1]
        counts[tag] += 1
        if tag == "EntryEvent" and example_event is None:
            example_event = ET.tostring(el, encoding="unicode")[:800]
        if tag in ("DistinctParty", "SanctionsEntry", "Profile"):
            el.clear()  # clear only whole records, so the example keeps its children
        if i >= max_elements:
            break
    keep = ("DistinctParty", "SanctionsEntry", "EntryEvent", "Date", "Feature", "VersionDetail")
    return {"element_counts": {k: counts.get(k, 0) for k in keep}, "example_entry_event": example_event}


# ---------------------------------------------------------------------- probe (Phase 0 task 6)
def probe(years: tuple[int, ...] = (2023, 2025)) -> dict:
    out: dict = {"years": {}}
    with net.client(timeout=180) as c:
        try:
            src, p = _cached_download(c, config.OFAC_SDN_CSV_URLS, "sdn.csv")
            rows = parse_sdn_csv(p.read_bytes().decode("latin-1"))
            vessels = sdn_vessels(rows)
            out["sdn_csv"] = {"source": src, "entries": len(rows), "vessels": len(vessels),
                              "vessels_with_imo": sum(1 for v in vessels if v["imo"])}
        except Exception as e:  # noqa: BLE001
            out["sdn_csv"] = {"error": repr(e)}
        try:
            src, p = _cached_download(c, config.OFAC_SDN_ADVANCED_XML_URLS, "sdn_advanced.xml")
            log.info("scanning advanced XML", extra={"mb": round(p.stat().st_size / 1e6)})
            out["sdn_advanced_xml"] = {"source": src, "bytes": p.stat().st_size, **advanced_xml_summary(p)}
        except Exception as e:  # noqa: BLE001
            out["sdn_advanced_xml"] = {"error": repr(e)}
        for y in years:
            try:
                log.info("change archive", extra={"year": y})
                src, text = changes_for_year(c, y)
                rows, st = parse_changes_text(text)
                vessel_rows = [r for r in rows if r.is_vessel]
                out["years"][y] = {"source": src, "chars": len(text), "stats": asdict(st),
                                   "example_vessel_rows": [asdict(r) for r in vessel_rows[:3]]}
                if y >= 2024:
                    sample = text[: 6000]
                    (config.REPORTS_DIR / "phase0_ofac_sample.txt").write_text(sample)
            except Exception as e:  # noqa: BLE001
                out["years"][y] = {"error": repr(e)}
    y25 = out["years"].get(2025, {})
    out["go"] = (y25.get("stats") or {}).get("vessel_entries_with_imo", 0) >= 100
    out["checked_on"] = date.today().isoformat()
    probes.write("ofac", out)
    return out
