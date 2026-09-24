"""PSI drift per feature family per cutoff, and the Hormuz split (PREREG section 8).

PSI compares a cutoff's feature distribution against the distribution the model was trained on. Bins come
from the TRAINING side only, because bins fitted on the union would hide exactly the shift being measured.
"""

from __future__ import annotations

import numpy as np

from shadowfleet import config

BINS = 10
EPS = 1e-6  # PSI is undefined when a bin is empty on either side; the usual floor
MODERATE, LARGE = 0.1, 0.25  # the conventional read: <0.1 stable, 0.1-0.25 moderate, >0.25 large


def psi(expected: np.ndarray, actual: np.ndarray, bins: int = BINS) -> float | None:
    """Population Stability Index. None when either side is empty or constant."""
    expected = expected[np.isfinite(expected)]
    actual = actual[np.isfinite(actual)]
    if len(expected) < bins or len(actual) == 0 or np.ptp(expected) == 0:
        return None
    edges = np.unique(np.quantile(expected, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return None
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(expected, edges)[0] / len(expected)
    a = np.histogram(actual, edges)[0] / len(actual)
    e, a = np.clip(e, EPS, None), np.clip(a, EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


def verdict(value: float | None) -> str:
    if value is None:
        return "n/a"
    return "stable" if value < MODERATE else ("moderate" if value < LARGE else "large")


def by_family(train_rows: list[dict], rows: list[dict], features: dict) -> dict[str, float | None]:
    """Mean PSI over the numeric features in each family. Families with no numeric feature return None."""
    from shadowfleet.models.rules import NUMERIC_EXCLUDED

    out: dict[str, float | None] = {}
    for family in sorted({f for f, _ in features.values()}):
        names = [n for n, (f, _) in features.items() if f == family and n not in NUMERIC_EXCLUDED]
        vals = []
        for name in names:
            e = np.array([r[name] for r in train_rows if isinstance(r.get(name), (int, float))], dtype=float)
            a = np.array([r[name] for r in rows if isinstance(r.get(name), (int, float))], dtype=float)
            got = psi(e, a) if len(e) and len(a) else None
            if got is not None:
                vals.append(got)
        out[family] = round(float(np.mean(vals)), 4) if vals else None
    return out


def hormuz_side(cutoff_iso: str) -> str:
    """Before or on-or-after the pre-registered regime break (config.REGIME_BREAKS)."""
    return "post_break" if cutoff_iso >= config.REGIME_BREAKS["hormuz_closure"] else "pre_break"


def demo() -> None:
    rng = np.random.default_rng(0)
    same = rng.normal(size=5000)
    assert psi(same, rng.normal(size=5000)) < MODERATE, "same distribution must read as stable"
    shifted = psi(same, rng.normal(loc=2.0, size=5000))
    assert shifted > LARGE, f"a two-sigma shift must read as large, got {shifted}"
    assert psi(np.zeros(100), np.zeros(100)) is None  # constant, not zero drift
    assert psi(np.array([1.0, 2.0]), np.array([1.0])) is None  # too few to bin
    assert verdict(None) == "n/a" and verdict(0.05) == "stable" and verdict(0.3) == "large"
    assert hormuz_side("2026-03-31") == "post_break" and hormuz_side("2026-01-31") == "pre_break"
    print("drift demo ok")


if __name__ == "__main__":
    demo()
