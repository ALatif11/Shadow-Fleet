"""Isolation forest: an unsupervised score reported in its own right (PREREG section 6).

Not a baseline to beat, and not fitted on labels. It is fitted on the cutoff's own feature matrix, so it
answers "how unusual is this hull among the hulls observed in this window", which needs no history and is
therefore available at the early cutoffs where supervised models are not.
"""

from __future__ import annotations

import numpy as np

from shadowfleet.models.rules import design_matrix

SEED = 20260924
N_ESTIMATORS = 200


def score(rows: list[dict], columns: list[str] | None = None) -> np.ndarray:
    """Higher is more anomalous, so it ranks the same direction as every other model here."""
    from sklearn.ensemble import IsolationForest

    x, _ = design_matrix(rows, columns)
    if len(rows) < 10 or x.shape[1] == 0:
        return np.zeros(len(rows))
    forest = IsolationForest(n_estimators=N_ESTIMATORS, random_state=SEED, contamination="auto")
    forest.fit(x)
    return -forest.score_samples(x)  # sklearn's score_samples is higher = more normal
