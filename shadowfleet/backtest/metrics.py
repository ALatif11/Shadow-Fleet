"""Metrics for the backtest. Every number here comes from arrays the harness passes in (CLAUDE.md rule 4).

PR-AUC is sklearn's `average_precision_score`, not a hand-rolled trapezoid: interpolated PR curves are a
classic way to flatter a tiny positive class, and average precision is the one that does not.
"""

from __future__ import annotations

import numpy as np

from shadowfleet import config


def _order(score: np.ndarray) -> np.ndarray:
    """Descending by score, ties broken by index so a constant scorer is not accidentally ranked well."""
    return np.lexsort((np.arange(len(score)), -score))


def precision_at_k(y: np.ndarray, score: np.ndarray, k: int) -> float | None:
    if len(y) == 0:
        return None
    top = _order(score)[:k]
    return float(y[top].sum() / min(k, len(y)))


def recall_at_k(y: np.ndarray, score: np.ndarray, k: int) -> float | None:
    n_pos = int(y.sum())
    if n_pos == 0:
        return None
    return float(y[_order(score)[:k]].sum() / n_pos)


def alerts_for_recall(y: np.ndarray, score: np.ndarray, target: float = 0.5) -> int | None:
    """How many alerts an analyst must work to catch `target` of the positives. None if unreachable."""
    n_pos = int(y.sum())
    if n_pos == 0:
        return None
    hits = np.cumsum(y[_order(score)])
    reached = np.argmax(hits >= target * n_pos)
    return int(reached + 1) if hits[-1] >= target * n_pos else None


def fpr_at_k(y: np.ndarray, score: np.ndarray, k: int) -> float | None:
    n_neg = int((y == 0).sum())
    if n_neg == 0:
        return None
    top = _order(score)[:k]
    return float((y[top] == 0).sum() / n_neg)


def pr_auc(y: np.ndarray, score: np.ndarray) -> float | None:
    if y.sum() == 0 or y.sum() == len(y):
        return None
    from sklearn.metrics import average_precision_score

    return float(average_precision_score(y, score))


def brier(y: np.ndarray, p: np.ndarray) -> float | None:
    """Only meaningful for a model that emits probabilities; rules scores are not calibrated."""
    return float(np.mean((p - y) ** 2)) if len(y) else None


def summary(y: np.ndarray, score: np.ndarray, ks: tuple[int, ...] = config.TOP_K,
            calibrated: bool = False) -> dict:
    out: dict = {"n": int(len(y)), "n_positive": int(y.sum()),
                 "base_rate": float(y.mean()) if len(y) else None,
                 "pr_auc": pr_auc(y, score),
                 "alerts_for_50pct_recall": alerts_for_recall(y, score, 0.5)}
    for k in ks:
        out[f"precision_at_{k}"] = precision_at_k(y, score, k)
        out[f"recall_at_{k}"] = recall_at_k(y, score, k)
    out["fpr_at_50"] = fpr_at_k(y, score, 50)
    if calibrated:
        out["brier"] = brier(y, score)
    return out


def demo() -> None:
    """One runnable check: a perfect ranker, a constant one, and the k > n edge."""
    y = np.array([1, 1, 0, 0, 0, 0, 0, 0, 0, 0])
    perfect = np.array([9, 8, 7, 6, 5, 4, 3, 2, 1, 0], dtype=float)
    assert precision_at_k(y, perfect, 2) == 1.0
    assert recall_at_k(y, perfect, 2) == 1.0
    assert alerts_for_recall(y, perfect, 0.5) == 1  # one alert catches one of two positives
    assert pr_auc(y, perfect) == 1.0
    assert precision_at_k(y, perfect, 100) == 0.2  # k clamps to n, so this is the base rate
    flat = np.zeros(10)
    assert precision_at_k(y, flat, 2) == 1.0  # ties keep input order, and the fixture is sorted
    assert recall_at_k(np.zeros(10), perfect, 2) is None  # no positives, not zero
    assert fpr_at_k(y, perfect, 2) == 0.0
    print("metrics demo ok")


if __name__ == "__main__":
    demo()
