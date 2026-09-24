"""Rules baselines. B2's weights are PREREG section 6, written here before any label was loaded.

Scores are plain numpy arrays over the feature rows the harness hands in, in the same order. Nothing here
fits anything except B3, which is the point: if LightGBM cannot beat these, that is the finding (rule 8).
"""

from __future__ import annotations

import numpy as np

SEED = 20260924  # B0 is random, so it is seeded; an unseeded baseline is not reproducible

# PREREG section 6, verbatim:
#   3*B1 + 2*[gap_hours_total>24] + 2*[n_encounters_with_sanctioned_partner>0]
#   + 1*[flag_to_convenience_registry] + 1*[vessel_age_years>15] + 1*[n_name_changes>=1]
#   + 1*[spoof_jump_rate_excess>0]
B2_WEIGHTS = {"b1": 3, "long_gap": 2, "sanctioned_partner": 2, "convenience_flag": 1,
              "old_vessel": 1, "renamed": 1, "spoof_excess": 1}


def _col(rows: list[dict], name: str, default: float = 0.0) -> np.ndarray:
    return np.array([default if r.get(name) is None else r[name] for r in rows], dtype=float)


def b0_random(rows: list[dict]) -> np.ndarray:
    return np.random.default_rng(SEED).random(len(rows))


def b1_russia_port(rows: list[dict]) -> np.ndarray:
    """Called at a Russian port in window, by GFW port visit or DMA destination text (PREREG section 6)."""
    return ((_col(rows, "n_russian_port_visits") > 0)
            | (_col(rows, "share_russian_destination") > 0)).astype(float)


def b2_weighted(rows: list[dict]) -> np.ndarray:
    w = B2_WEIGHTS
    return (w["b1"] * b1_russia_port(rows)
            + w["long_gap"] * (_col(rows, "gap_hours_total") > 24)
            + w["sanctioned_partner"] * (_col(rows, "n_encounters_with_sanctioned_partner") > 0)
            + w["convenience_flag"] * _col(rows, "flag_to_convenience_registry")
            + w["old_vessel"] * (_col(rows, "vessel_age_years") > 15)
            + w["renamed"] * (_col(rows, "n_name_changes") >= 1)
            + w["spoof_excess"] * (_col(rows, "spoof_jump_rate_excess") > 0)).astype(float)


def b2_live_terms(rows: list[dict]) -> dict[str, int]:
    """How many hulls each B2 term fires on. A term stuck at zero means its source phase has not run, and
    the report says so instead of leaving the weight looking as if it contributed."""
    return {
        "b1": int(b1_russia_port(rows).sum()),
        "long_gap": int((_col(rows, "gap_hours_total") > 24).sum()),
        "sanctioned_partner": int((_col(rows, "n_encounters_with_sanctioned_partner") > 0).sum()),
        "convenience_flag": int(_col(rows, "flag_to_convenience_registry").sum()),
        "old_vessel": int((_col(rows, "vessel_age_years") > 15).sum()),
        "renamed": int((_col(rows, "n_name_changes") >= 1).sum()),
        "spoof_excess": int((_col(rows, "spoof_jump_rate_excess") > 0).sum()),
    }


NUMERIC_EXCLUDED = {"hull_id", "cutoff", "current_flag"}


def design_matrix(rows: list[dict], columns: list[str] | None = None) -> tuple[np.ndarray, list[str]]:
    """Numeric features only, in a fixed column order, nulls as zero. `current_flag` is categorical and is
    dropped rather than label-encoded: an arbitrary integer order is a worse lie than leaving it out."""
    cols = columns or sorted(k for k in rows[0] if k not in NUMERIC_EXCLUDED) if rows else []
    return np.column_stack([_col(rows, c) for c in cols]) if cols else np.empty((len(rows), 0)), list(cols)


def b3_logistic(train_rows: list[dict], train_y: np.ndarray, rows: list[dict],
                columns: list[str] | None = None) -> np.ndarray:
    """Standardised logistic regression, class_weight balanced (PREREG section 6)."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    x_train, cols = design_matrix(train_rows, columns)
    if len(train_y) == 0 or train_y.sum() == 0 or len(set(train_y.tolist())) < 2:
        return np.zeros(len(rows))  # nothing to learn from yet; the harness reports it as not scored
    model = make_pipeline(StandardScaler(),
                          LogisticRegression(class_weight="balanced", max_iter=1000))
    model.fit(x_train, train_y)
    x, _ = design_matrix(rows, cols)
    return model.predict_proba(x)[:, 1]


RULES = {"B0_random": b0_random, "B1_russia_port": b1_russia_port, "B2_weighted": b2_weighted}
