"""Leakage tests (c), (d) and (e) of PREREG section 10, the three that need a fitted model or a rebuilt store.

(a) truncation equality and (b) static analysis live in `tests/test_leakage.py`, which `make backtest` runs
before the harness reports anything. These three run inside the harness for the same reason: a leakage test
that only runs when someone remembers to run it is not a guard.
"""

from __future__ import annotations

from datetime import date

import duckdb
import numpy as np

from shadowfleet import config
from shadowfleet.backtest import metrics
from shadowfleet.models import rules
from shadowfleet.resolve.identity import HULL_MAP

PERMUTATION_TOLERANCE = 3.0  # shuffled PR-AUC may not exceed this multiple of the base rate
REVERSE_TIME_TOLERANCE = 1.5  # backward PR-AUC may not exceed this multiple of forward


def permutation_check(train_rows: list[dict], train_y: np.ndarray, eval_rows: list[dict],
                      eval_y: np.ndarray, seed: int = rules.SEED) -> dict:
    """(c) Shuffle the TRAINING labels, then score and evaluate exactly as the harness does.

    This has to mirror the pipeline to mean anything. Fitting and evaluating on the same rows instead would
    measure how much noise the model can memorise, which is a different question: with enough features
    relative to rows, in-sample PR-AUC stays high on shuffled labels and the test fires on a model that is
    not leaking anything.
    """
    if train_y.sum() == 0 or eval_y.sum() == 0 or len(train_rows) < 20:
        return {"skipped": "need positives in both the training and the evaluation cutoff"}
    shuffled = np.random.default_rng(seed).permutation(train_y)
    score = rules.b3_logistic(train_rows, shuffled, eval_rows)
    got = metrics.pr_auc(eval_y, score)
    base = float(eval_y.mean())
    return {"pr_auc_shuffled": got, "base_rate": round(base, 5),
            "ratio": round(got / base, 3) if got and base else None,
            "passes": got is None or got <= PERMUTATION_TOLERANCE * base}


def reverse_time_verdict(forward: float | None, backward: float | None,
                         base_early: float, base_late: float) -> dict:
    """The pass rule for (d), as amended 2026-10-02 (PREREG section 12, post-results).

    The two directions are scored on different cutoffs, and PR-AUC rises with the base rate, so they are
    compared by lift over each side's own base rate. Leakage makes backward better than forward even after
    that adjustment. The pre-registered raw-PR-AUC rule is still computed and reported as `raw_passes`.
    """
    lift_f = forward / base_late if forward and base_late else None
    lift_b = backward / base_early if backward and base_early else None
    return {"lift_forward": round(lift_f, 3) if lift_f else None,
            "lift_backward": round(lift_b, 3) if lift_b else None,
            "lift_ratio": round(lift_b / lift_f, 3) if lift_f and lift_b else None,
            "ratio": round(backward / forward, 3) if forward and backward else None,
            "raw_passes": not (forward and backward) or backward <= REVERSE_TIME_TOLERANCE * forward,
            "passes": not (lift_f and lift_b) or lift_b <= REVERSE_TIME_TOLERANCE * lift_f}


def reverse_time_check(by_cutoff: list[tuple[date, list[dict], np.ndarray]]) -> dict:
    """(d) Train on later cutoffs and score an earlier one.

    Forward and backward should be comparable. Backward being much better means information is flowing
    from the future into the features, because that is the only thing the reversal adds.
    """
    usable = [(T, r, y) for T, r, y in by_cutoff if y.sum() > 0]
    if len(usable) < 2:
        return {"skipped": "need two cutoffs with positives"}
    (t_early, early, y_early), (t_late, late, y_late) = usable[0], usable[-1]
    forward = metrics.pr_auc(y_late, rules.b3_logistic(early, y_early, late))
    backward = metrics.pr_auc(y_early, rules.b3_logistic(late, y_late, early))
    base_early, base_late = float(y_early.mean()), float(y_late.mean())
    return {"early": t_early.isoformat(), "late": t_late.isoformat(),
            "base_rate_early": round(base_early, 4), "base_rate_late": round(base_late, 4),
            "n_positive_early": int(y_early.sum()), "n_positive_late": int(y_late.sum()),
            "pr_auc_forward": forward, "pr_auc_backward": backward,
            **reverse_time_verdict(forward, backward, base_early, base_late)}


def entity_resolution_delta() -> dict:
    """(e) Recompute with GFW-based hull merges disabled.

    Phase 3 built no GFW merge at all (ADR-21), so this arm is a no-op by construction and the delta is
    exactly zero. That is worth asserting rather than assuming: if a GFW fallback is ever added to
    `hull_map`, this starts reporting a real number instead of silently staying at zero.
    """


    path = config.PARQUET_DIR / HULL_MAP
    if not path.exists():
        return {"skipped": "no hull_map"}
    methods = dict(duckdb.connect().execute(
        f"SELECT method, count(*) FROM '{path.as_posix()}' GROUP BY method").fetchall())
    gfw_merges = sum(v for k, v in methods.items() if "gfw" in k.lower())
    return {"methods": methods, "windows_from_a_gfw_merge": gfw_merges,
            "pr_auc_delta": 0.0 if gfw_merges == 0 else None,
            "passes": gfw_merges == 0,
            "note": ("no hull id depends on a GFW model, so disabling GFW merges changes nothing"
                     if gfw_merges == 0 else
                     "GFW merges exist; this arm must now recompute features with them disabled")}

def run_all(by_cutoff: list[tuple[date, list[dict], np.ndarray]]) -> dict:
    """All three, as the harness calls them. Returns one dict keyed by test."""
    return {"permutation": permutation_check(*_train_eval_pair(by_cutoff)) if by_cutoff
                           else {"skipped": "no cutoffs"},
            "reverse_time": reverse_time_check(by_cutoff),
            "entity_resolution": entity_resolution_delta()}


def _train_eval_pair(by_cutoff: list[tuple[date, list[dict], np.ndarray]]):
    """The two cutoffs with the most positives, earlier one training, later one evaluated. Mirrors the
    harness's own direction; with fewer than two it returns empties and the check reports `skipped`."""
    top = sorted(sorted(by_cutoff, key=lambda x: -int(x[2].sum()))[:2], key=lambda x: x[0])
    if len(top) < 2:
        return ([], np.array([]), [], np.array([]))
    (_, tr, try_), (_, ev, evy) = top
    return (tr, try_, ev, evy)
