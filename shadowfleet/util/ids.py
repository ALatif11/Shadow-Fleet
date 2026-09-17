"""Identifier helpers."""

from __future__ import annotations

import re

IMO_RE = re.compile(r"\bIMO\s*(?:number|No\.?|:)?\s*(\d{7})\b", re.IGNORECASE)


def imo_valid(imo: int | str | None) -> bool:
    """IMO check digit: sum(d_i * w_i) for weights 7..2 over the first six digits, mod 10 = last digit."""
    if imo is None:
        return False
    s = str(imo).strip()
    if not (len(s) == 7 and s.isdigit()) or s == "0000000":
        return False
    total = sum(int(d) * w for d, w in zip(s[:6], range(7, 1, -1), strict=True))
    return total % 10 == int(s[6])


def extract_imos(text: str) -> list[int]:
    """Valid IMO numbers mentioned as 'IMO nnnnnnn', in order, de-duplicated."""
    out: list[int] = []
    for m in IMO_RE.finditer(text or ""):
        v = int(m.group(1))
        if imo_valid(v) and v not in out:
            out.append(v)
    return out
