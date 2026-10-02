"""LightGBM, the PREREG primary model, with its hyperparameters fixed in PREREG section 4.

SHAP comes from LightGBM's own `pred_contrib=True`, which is TreeSHAP. The `shap` package is not used: it
would be a second implementation of the same algorithm plus a numba toolchain, for identical numbers.
"""

from __future__ import annotations

import numpy as np

from shadowfleet.models.rules import design_matrix

# PREREG section 4, verbatim: num_leaves 15, min_child_samples 20, feature_fraction 0.8, scale_pos_weight
# from the training base rate, early stopping on the most recent training cutoff held out as validation,
# three seeds averaged, no hyperparameter search beyond those seeds.
PARAMS = {"objective": "binary", "num_leaves": 15, "min_child_samples": 20, "feature_fraction": 0.8,
          "learning_rate": 0.05, "verbose": -1,
          # Reproducibility, not tuning: multi-threaded histogram sums are summed in a varying order, so
          # identical data gave precision@50 0.2077 one run and 0.1954 the next (Oct 2 2026).
          "deterministic": True, "force_row_wise": True, "num_threads": 1,
          # Early stopping watches ranking quality, because ranking is what is evaluated. With logloss it
          # watched calibration, and scale_pos_weight deliberately miscalibrates: on a low-base-rate
          # validation cutoff every round looked worse, training stopped at the first tree, and the Oct 1
          # 2026 forward list came out as 50 identical scores.
          "metric": "average_precision"}
SEEDS = (1, 2, 3)
MAX_ROUNDS = 400
EARLY_STOPPING = 30


def validation_mask(train_rows: list[dict], valid_frac: float = 0.2) -> np.ndarray:
    """The rows held out for early stopping: the most recent training cutoff (PREREG section 4).

    Until Oct 2 2026 this was `max(int(n * 0.8), n - 1)` rows into the tail, which is always n - 1: one row
    held out, almost never a positive, so early stopping never ran and every model trained 400 rounds.
    With a single training cutoff there is nothing to hold out and nothing is. Rows without a cutoff (unit
    tests) fall back to the last `valid_frac` of rows.
    """
    n = len(train_rows)
    cutoffs = [r.get("cutoff") for r in train_rows]
    if all(c is not None for c in cutoffs):
        last = max(cutoffs)
        mask = np.array([c == last for c in cutoffs])
        return mask if 0 < mask.sum() < n else np.zeros(n, dtype=bool)
    mask = np.zeros(n, dtype=bool)
    mask[int(n * (1 - valid_frac)):] = True
    return mask


def train_and_score(train_rows: list[dict], train_y: np.ndarray, rows: list[dict],
                    columns: list[str] | None = None, valid_frac: float = 0.2):
    """Average of three seeds. Returns (score, boosters, columns); score is None if nothing is learnable.

    Early stopping holds out the most recent training cutoff (`validation_mask`), not a random sample: a
    random split would put rows from the same cutoff on both sides and flatter early stopping.
    """
    import lightgbm as lgb

    x_train, cols = design_matrix(train_rows, columns)
    if len(train_y) < 40 or train_y.sum() < 3 or len(set(train_y.tolist())) < 2:
        return None, [], cols
    val = validation_mask(train_rows, valid_frac)
    x_fit, y_fit, x_val, y_val = x_train[~val], train_y[~val], x_train[val], train_y[val]
    if y_fit.sum() == 0 or len(set(y_fit.tolist())) < 2:
        return None, [], cols
    pos_weight = float((len(y_fit) - y_fit.sum()) / max(y_fit.sum(), 1))
    x, _ = design_matrix(rows, cols)
    scores, boosters = [], []
    for seed in SEEDS:
        params = {**PARAMS, "scale_pos_weight": pos_weight, "seed": seed,
                  "bagging_seed": seed, "feature_fraction_seed": seed}
        callbacks = []
        valid_sets = []
        if len(y_val) and y_val.sum() and len(set(y_val.tolist())) > 1:
            valid_sets = [lgb.Dataset(x_val, y_val)]
            callbacks = [lgb.early_stopping(EARLY_STOPPING, first_metric_only=True, verbose=False)]
        booster = lgb.train(params, lgb.Dataset(x_fit, y_fit), num_boost_round=MAX_ROUNDS,
                            valid_sets=valid_sets, callbacks=callbacks)
        boosters.append(booster)
        scores.append(booster.predict(x))
    return np.mean(scores, axis=0), boosters, cols


def contributions(boosters: list, rows: list[dict], cols: list[str], top_k: int = 10) -> list[dict]:
    """Per-row top-k TreeSHAP contributions, averaged over the seeds. One dict per row."""
    x, _ = design_matrix(rows, cols)
    if not boosters or not len(x):
        return []
    stacked = np.mean([b.predict(x, pred_contrib=True) for b in boosters], axis=0)
    values, bias = stacked[:, :-1], stacked[:, -1]
    out = []
    for i, row in enumerate(rows):
        order = np.argsort(-np.abs(values[i]))[:top_k]
        out.append({"hull_id": row.get("hull_id"), "base_value": float(bias[i]),
                    "top": [{"feature": cols[j], "value": float(x[i, j]),
                             "contribution": float(values[i, j])} for j in order]})
    return out
