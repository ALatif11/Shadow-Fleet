"""Phase 6 artefacts: ablation arms, SHAP tables, flagged lists and the false-positive review sheet.

Kept out of `harness.py` so the scoring loop stays a scoring loop. Nothing here changes a metric; it all
either restricts the columns a model sees (ablations, which PREREG section 7 says are reporting only and
never drive feature removal) or writes down what the primary model did.
"""

from __future__ import annotations

import csv
from datetime import date

import numpy as np

from shadowfleet import config
from shadowfleet.features.asof import FEATURES, SOURCE_OF_FAMILY
from shadowfleet.models.rules import NUMERIC_EXCLUDED
from shadowfleet.util.store import rel_path

TOP_FLAGGED = 100  # ADR-18: the console reads the top 100 per cutoff
FP_REVIEW_N = 20  # phase-prompts Phase 6 task 7


def numeric_columns(families: set[str] | None = None) -> list[str]:
    """Numeric feature names, optionally restricted to some families, in the fixed design-matrix order."""
    return sorted(n for n, (f, _) in FEATURES.items()
                  if n not in NUMERIC_EXCLUDED and (families is None or f in families))


def ablation_arms() -> dict[str, list[str]]:
    """One arm per dropped family, plus the two source arms the whole project turns on (R14).

    GFW-only vs self-built-only is the arm that answers "is this a detector or a classifier over someone
    else's detections", so it is not optional even when the GFW families are empty.
    """
    families = {f for f, _ in FEATURES.values()}
    arms = {"full": numeric_columns()}
    for family in sorted(families):
        arms[f"drop_{family}"] = numeric_columns(families - {family})
    arms["gfw_only"] = numeric_columns({f for f in families if SOURCE_OF_FAMILY[f] == "gfw"})
    arms["self_built_only"] = numeric_columns({f for f in families if SOURCE_OF_FAMILY[f] == "self_built"})
    return {k: v for k, v in arms.items() if v}


def write_shap(T: date, contribs: list[dict]) -> str | None:
    """`data/parquet/shap/cutoff=T/` with one row per hull and its top contributions."""
    if not contribs:
        return None
    import pyarrow as pa
    import pyarrow.parquet as pq

    d = config.PARQUET_DIR / "shap" / f"cutoff={T.isoformat()}"
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"hull_id": c["hull_id"], "base_value": c["base_value"],
             "features": [t["feature"] for t in c["top"]],
             "values": [t["value"] for t in c["top"]],
             "contributions": [t["contribution"] for t in c["top"]]} for c in contribs]
    pq.write_table(pa.Table.from_pylist(rows), d / "part-0.parquet", compression="zstd")
    return rel_path(d)


def read_shap(T: date) -> dict[str, list[dict]]:
    """The SHAP table `write_shap` produced, as hull_id -> top contributions.

    Lives next to the writer rather than in `briefs/`, where it started as a private function that
    `backtest/forward.py` then imported through the underscore. Two callers reaching across packages for
    a private name is how a module boundary stops meaning anything.
    """
    import duckdb

    part = config.PARQUET_DIR / "shap" / f"cutoff={T.isoformat()}" / "part-0.parquet"
    if not part.exists():
        return {}
    rows = duckdb.connect().execute(
        f"SELECT hull_id, features, values, contributions FROM '{part.as_posix()}'").fetchall()
    return {r[0]: [{"feature": f, "value": v, "contribution": c}
                   for f, v, c in zip(r[1], r[2], r[3], strict=True)] for r in rows}


def write_flagged(T: date, rows: list[dict], score: np.ndarray, y: np.ndarray,
                  contribs: list[dict], designations: dict[str, str] | None = None) -> str:
    """`reports/flagged_<cutoff>.csv`: rank, score, label and the top-5 drivers per hull."""
    by_hull = {c["hull_id"]: c for c in contribs}
    order = np.argsort(-score)[:TOP_FLAGGED]
    out = config.REPORTS_DIR / f"flagged_{T.isoformat()}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["rank", "hull_id", "score", "label", "designation_date",
                     *[f"driver_{i + 1}" for i in range(5)]])
        for rank, i in enumerate(order, start=1):
            hull = rows[i]["hull_id"]
            drivers = [f"{d['feature']}={d['value']:g} ({d['contribution']:+.3f})"
                       for d in (by_hull.get(hull) or {}).get("top", [])[:5]]
            # padded to five: a hull with no contributions must still produce a full-width row, or every
            # column after it shifts left and a reader silently mis-parses the file
            wr.writerow([rank, hull, round(float(score[i]), 6), int(y[i]),
                         (designations or {}).get(hull, ""), *drivers, *[""] * (5 - len(drivers))])
    return rel_path(out)


def write_fp_review(quarter: str, flagged: list[dict]) -> str:
    """`reports/fp_review_<quarter>.csv`: the top non-listed flags, with a blank reason for Adam.

    Reason codes come from the plan: lightering, CPC crude, ice-class, unknown. Left blank on purpose --
    a pre-filled guess would be a fabricated review.
    """
    out = config.REPORTS_DIR / f"fp_review_{quarter}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["quarter", "cutoff", "rank", "hull_id", "score",
                                           "top_drivers", "reason", "notes"])
        wr.writeheader()
        for r in sorted(flagged, key=lambda r: r["rank"])[:FP_REVIEW_N]:
            wr.writerow({**r, "quarter": quarter, "reason": "", "notes": ""})
    return rel_path(out)


def quarter_of(T: date) -> str:
    return f"{T.year}Q{(T.month - 1) // 3 + 1}"
