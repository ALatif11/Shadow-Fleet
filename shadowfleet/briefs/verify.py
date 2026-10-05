"""Deterministic faithfulness checks on a rendered brief (Phase 9 task 1).

No model involved. Four checks, each of which can only fail for a reason a human would agree with:
citations resolve, the top drivers are covered, every number and date in the prose is in the bundle, and
every capitalised name in the prose is in the bundle. The LLM judge (Phase 9 task 2) grades semantics on
top of this; it never replaces it, because a judge that agrees with a fabrication is worthless.
"""

from __future__ import annotations

import calendar
import re
from collections import Counter

import pycountry

from shadowfleet.features.asof import FEATURES

# A "number" is anything that could be a fact: integers, decimals, dates, durations, coordinates. The
# grounding check normalises before comparing, so 57.70 in the prose matches 57.7 in the bundle.
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Capitalised runs of two or more characters, which is how a vessel, port or company name would appear.
NAME = re.compile(r"\b[A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]{2,})*\b")
# Citation markers are ids, not facts: the renderer writes `[E4]`, and the model writes `(E4)` in its summary.
# Grounding has to ignore both, or `(E4)` reads as an ungrounded 4 (it did: 1-13 were the top "numbers").
CITATION = re.compile(r"\bE\d+\b")
# Words a brief may capitalise without naming anything: prose, headings, our own vocabulary.
NAME_ALLOWLIST = {
    "Risk", "Findings", "Caveats", "Evidence", "Low", "Medium", "High", "The", "This", "Vessel", "Hull",
    "Cited", "Draught", "Moored", "Anchor", "Under", "Way", "Using", "Engine", "None", "Not", "No",
    "Available", "Data", "AIS", "IMO", "MMSI", "STS", "GFW", "OFAC", "EU", "UK", "Russian", "Baltic",
    "Danish", "Denmark", "Skagen", "Phase", "Identity", "Loitering", "Spoof", "Families",
    # the bundle's own section names and source, which the model refers to by name
    "Russia", "Header", "Drivers", "Driver", "Ship", "IDs", "ID", "Flag", "DMA", "SHAP", "LOCODE",
}


# The evidence family whose records back each feature family. `detect` and `ais` mix several detectors, so
# their features are mapped one by one below; a feature with no entry (vessel size, transit counts) has no
# per-record evidence at all and is stated from the header, so coverage cannot require a citation for it.
EVIDENCE_FAMILY = {"identity": "identity", "gfw_gaps": "gfw", "gfw_encounters": "gfw", "gfw_ports": "gfw"}
EVIDENCE_FOR_FEATURE = {"n_sts_candidates": "sts", "n_sts_with_draught_change": "sts",
                        "anchorage_loitering_hours": "loitering", "n_draught_inconsistencies": "draught",
                        "n_mmsi_imo_churn": "churn", "spoof_jump_rate_excess": "spoof",
                        "share_russian_destination": "destination"}


def evidence_family(feature: str) -> str | None:
    return EVIDENCE_FOR_FEATURE.get(feature) or EVIDENCE_FAMILY.get(FEATURES.get(feature, ("", ""))[0])


# Characters after which a capital letter is grammar rather than a name.
SENTENCE_START = set(".?!:\n\u2014-*|#")


def _names(text: str) -> set[str]:
    """Capitalised runs that are plausibly names, with sentence-initial words removed first.

    Without this the check fires on every sentence that starts with a capital, which is every sentence.
    A verifier whose failures are mostly grammar gets switched off, and then it catches nothing.
    """
    found: set[str] = set()
    for m in NAME.finditer(text):
        before = text[:m.start()].rstrip()
        words = m.group().split()
        if not before or before[-1] in SENTENCE_START:
            words = words[1:]  # the first word is where the sentence started, not a name
        if words:
            found.add(" ".join(words))
    return found


def _numbers(text: str) -> set[str]:
    """Normalised numeric tokens. Dates are removed first so their parts are not read as three numbers."""
    return {_norm(m) for m in NUMBER.findall(DATE.sub(" ", text))}


def _norm(value: object) -> str:
    """`57.70`, `57.7` and `57.700` all have to compare equal, and `3` must equal `3.0`."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)
    return f"{f:.4f}".rstrip("0").rstrip(".")


def _grounded(token: str, numbers: set[str]) -> bool:
    """`31.4` is grounded by a bundle value of 31.42: the prose may round, to its own number of decimals."""
    if token in numbers:
        return True
    places = len(token.split(".")[1]) if "." in token else 0
    t = float(token)
    return any(abs(round(float(n), places) - t) < 1e-9 for n in numbers if _is_num(n))


def _is_num(v: str) -> bool:
    try:
        float(v)
    except ValueError:
        return False
    return True


def _country_names(codes: set[str]) -> set[str]:
    """`PAN` in the bundle grounds `Panama` in the prose; the flag is stated, only its spelling changed."""
    out = set()
    for c in codes:
        if len(c) == 3 and c.isalpha() and (country := pycountry.countries.get(alpha_3=c.upper())):
            out |= {country.name, getattr(country, "common_name", country.name)}
    return out


def _bundle_values(bundle: dict) -> tuple[set[str], set[str], set[str]]:
    numbers, dates, names = set(), set(), set()
    for blob in (bundle.get("evidence") or {}).values():
        for v in blob.values():
            if isinstance(v, bool) or v is None:
                continue
            if isinstance(v, (int, float)):
                numbers.add(_norm(v))
            elif isinstance(v, str):
                dates |= set(DATE.findall(v))
                numbers |= _numbers(v)
                names |= set(NAME.findall(v))  # bundle values are data, so every run counts as known
    for d in bundle.get("drivers") or []:
        numbers |= {_norm(d.get("value")), _norm(d.get("contribution"))}
        names |= set(NAME.findall(d.get("description") or ""))
        numbers |= _numbers(d.get("description") or "")
    for v in (bundle.get("header") or {}).values():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            numbers.add(_norm(v))
        elif isinstance(v, str):
            names.add(v)
    names |= _country_names({w for n in names for w in n.split()})
    # "March 2025" names a month of a bundle date. Days are left out on purpose: a bare 19 is more often a
    # duration than a day, and the prompt asks for ISO dates anyway.
    for d in dates - {""}:
        names.add(calendar.month_name[int(d[5:7])])
        numbers.add(_norm(d[:4]))
    dates.add(bundle.get("cutoff", ""))
    numbers.add(_norm(bundle.get("hull_id")))
    names.add(str(bundle.get("hull_id")))
    return numbers, dates, names


def verify(brief: dict, bundle: dict, prose: str, top_drivers: int = 5) -> dict:
    """Every failure is a list of the offending items, so a failing brief can be read, not just counted."""
    ids = set((bundle.get("evidence") or {}).keys())
    cited = [i for f in (brief.get("findings") or []) for i in (f.get("evidence_ids") or [])]

    dangling = sorted({i for i in cited if i not in ids})

    # a driver is covered when some cited record comes from the evidence family it is built from. A driver
    # whose family has no record in the bundle cannot be cited, so it is listed, not failed.
    drivers = [d["feature"] for d in (bundle.get("drivers") or [])[:top_drivers]]
    present = {(r or {}).get("family") for r in (bundle.get("evidence") or {}).values()}
    cited_families = {(bundle["evidence"][i] or {}).get("family") for i in cited if i in ids}
    citable = [d for d in drivers if evidence_family(d) in present]
    uncovered = sorted(d for d in citable if evidence_family(d) not in cited_families)

    numbers, dates, names = _bundle_values(bundle)
    # the appendix is generated from the bundle itself, and the [E#] markers are the renderer's
    prose_body = CITATION.sub(" ", prose.split("## Evidence cited")[0])
    ungrounded_numbers = sorted(n for n in _numbers(prose_body) if not _grounded(n, numbers))
    ungrounded_dates = sorted(d for d in DATE.findall(prose_body) if d not in dates)
    ungrounded_names = sorted(n for n in _names(prose_body)
                              if n not in names and not all(w in NAME_ALLOWLIST for w in n.split()))

    failures = {"dangling_citations": dangling, "uncovered_drivers": uncovered,
                "ungrounded_numbers": ungrounded_numbers, "ungrounded_dates": ungrounded_dates,
                "ungrounded_names": ungrounded_names}
    return {"hull_id": bundle.get("hull_id"), "cutoff": bundle.get("cutoff"),
            "drivers_without_records": sorted(set(drivers) - set(citable)),
            "n_findings": len(brief.get("findings") or []), "n_cited": len(set(cited)),
            **failures, "passes": not any(failures.values())}





def aggregate(results: list[dict]) -> dict:
    """The headline Phase 9 number: the share of briefs with zero deterministic failures."""
    if not results:
        return {"briefs": 0}
    counts = {k: sum(1 for r in results if r.get(k)) for k in
              ("dangling_citations", "uncovered_drivers", "ungrounded_numbers", "ungrounded_dates",
               "ungrounded_names")}
    clean = sum(1 for r in results if r["passes"])
    missing = Counter(f for r in results for f in r.get("drivers_without_records") or [])
    return {"briefs": len(results), "clean": clean, "share_clean": round(clean / len(results), 4),
            "briefs_with": counts, "drivers_without_records": dict(missing.most_common())}
