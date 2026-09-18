"""Identifier helpers."""

from __future__ import annotations

import re

IMO_RE = re.compile(r"\bIMO\s*(?:number|No\.?|:)?\s*(\d{7})\b", re.IGNORECASE)


def imo_valid(imo: int | str | None) -> bool:
    """IMO check digit: sum(d_i * w_i) for weights 7..2 over the first six digits, mod 10 = last digit."""
    if imo is None:
        return False
    s = str(imo).strip()
    if not (len(s) == 7 and s.isdigit()) or s[0] == "0":
        return False  # IMO ship numbers never start with 0 (seen as junk values in OpenSanctions, Sep 2026)
    total = sum(int(d) * w for d, w in zip(s[:6], range(7, 1, -1), strict=True))
    return total % 10 == int(s[6])


def normalize_imo(value: object) -> int | None:
    """Digits of an IMO written any of the ways sources write it, or None if it is not a valid IMO.

    OpenSanctions writes "IMO9402263", OFAC's advanced XML writes a bare "9402263", DMA writes an int.
    """
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits) if imo_valid(digits) else None


def extract_imos(text: str) -> list[int]:
    """Valid IMO numbers mentioned as 'IMO nnnnnnn', in order, de-duplicated."""
    out: list[int] = []
    for m in IMO_RE.finditer(text or ""):
        v = int(m.group(1))
        if imo_valid(v) and v not in out:
            out.append(v)
    return out


def imo_valid_sql(col: str) -> str:
    """The same check digit as `imo_valid`, as a DuckDB predicate over a numeric column."""
    digits = [f"(CAST({col} AS BIGINT) // {10 ** p} % 10)" for p in (6, 5, 4, 3, 2, 1)]
    weighted = " + ".join(f"{w} * {d}" for w, d in zip(range(7, 1, -1), digits, strict=True))
    return (f"({col} IS NOT NULL AND CAST({col} AS BIGINT) BETWEEN 1000000 AND 9999999"
            f" AND ({weighted}) % 10 = CAST({col} AS BIGINT) % 10)")
