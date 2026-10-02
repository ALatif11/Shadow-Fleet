from __future__ import annotations

import csv
from datetime import date

import numpy as np
import pytest

from shadowfleet import config
from shadowfleet.backtest import drift, explain
from shadowfleet.features.asof import FEATURES
from shadowfleet.models import anomaly, tabular


def _rows(n: int, seed: int = 0) -> tuple[list[dict], np.ndarray]:
    """A learnable fixture: the label is a noisy function of one feature, so a model should find it."""
    rng = np.random.default_rng(seed)
    signal = rng.random(n)
    y = ((signal > 0.7) & (rng.random(n) > 0.2)).astype(int)
    rows = [{"hull_id": f"900000{i % 10}", "n_transits": float(signal[i]),
             "gap_hours_total": float(rng.random()), "n_name_changes": float(rng.integers(0, 3))}
            for i in range(n)]
    return rows, y


def test_drift_self_check():
    drift.demo()


def test_lightgbm_finds_a_planted_signal_and_averages_three_seeds():
    train, train_y = _rows(400, 1)
    test, test_y = _rows(400, 2)
    score, boosters, cols = tabular.train_and_score(train, train_y, test)
    assert score is not None and len(score) == 400
    assert len(boosters) == len(tabular.SEEDS) == 3
    # the planted feature must rank above chance
    from shadowfleet.backtest.metrics import pr_auc
    assert pr_auc(test_y, score) > test_y.mean() * 1.5


def test_lightgbm_refuses_to_train_on_too_little_rather_than_returning_noise():
    rows, y = _rows(20, 3)
    score, boosters, _ = tabular.train_and_score(rows, y, rows)
    assert score is None and boosters == []
    rows, _ = _rows(100, 4)
    score, _, _ = tabular.train_and_score(rows, np.zeros(100, dtype=int), rows)
    assert score is None  # one class only


def test_lightgbm_hyperparameters_are_the_preregistered_ones():
    text = (config.REPO_ROOT / "PREREG.md").read_text()
    assert "num_leaves 15" in text and "min_child_samples 20" in text and "feature_fraction 0.8" in text
    assert tabular.PARAMS["num_leaves"] == 15
    assert tabular.PARAMS["min_child_samples"] == 20
    assert tabular.PARAMS["feature_fraction"] == 0.8
    assert "three seeds" in text and len(tabular.SEEDS) == 3


def test_contributions_are_treeshap_and_name_the_driving_feature():
    train, train_y = _rows(400, 5)
    score, boosters, cols = tabular.train_and_score(train, train_y, train)
    contribs = tabular.contributions(boosters, train[:5], cols, top_k=3)
    assert len(contribs) == 5
    assert all(len(c["top"]) == 3 for c in contribs)
    # n_transits carries the signal, so it should dominate the contributions somewhere in the top 5
    assert any(c["top"][0]["feature"] == "n_transits" for c in contribs)
    assert all("contribution" in t and "value" in t for c in contribs for t in c["top"])


def test_isolation_forest_scores_higher_for_an_outlier():
    rng = np.random.default_rng(6)
    rows = [{"n_transits": float(v), "gap_hours_total": 0.0} for v in rng.normal(size=200)]
    rows.append({"n_transits": 40.0, "gap_hours_total": 0.0})  # an obvious outlier
    score = anomaly.score(rows)
    assert score.argmax() == len(rows) - 1


def test_isolation_forest_returns_zeros_rather_than_failing_on_a_tiny_cutoff():
    assert list(anomaly.score([{"n_transits": 1.0}])) == [0.0]


def test_ablation_arms_cover_every_family_plus_the_two_source_arms():
    arms = explain.ablation_arms()
    families = {f for f, _ in FEATURES.values()}
    for family in families:
        assert f"drop_{family}" in arms
        assert not any(FEATURES[c][0] == family for c in arms[f"drop_{family}"])
    assert "self_built_only" in arms and all(FEATURES[c][0] == "detect"
                                            for c in arms["self_built_only"])
    assert "full" in arms and len(arms["full"]) > len(arms["self_built_only"])


def test_flagged_csv_carries_rank_label_and_five_drivers(tmp_data):
    rows = [{"hull_id": f"90000{i}"} for i in range(3)]
    score = np.array([0.9, 0.1, 0.5])
    contribs = [{"hull_id": "900000", "base_value": 0.1,
                 "top": [{"feature": f"f{j}", "value": float(j), "contribution": -0.5 + j}
                         for j in range(6)]}]
    path = explain.write_flagged(date(2025, 3, 31), rows, score, np.array([1, 0, 0]), contribs)
    text = (config.REPORTS_DIR / "flagged_2025-03-31.csv").read_text()
    assert "flagged_2025-03-31.csv" in path
    got = list(csv.DictReader(text.splitlines()))
    assert [r["hull_id"] for r in got] == ["900000", "900002", "900001"]  # ranked by score
    assert got[0]["label"] == "1" and got[0]["driver_5"].startswith("f4")
    assert got[1]["driver_1"] == ""  # a hull with no contributions gets blanks, not invented drivers


def test_fp_review_sheet_leaves_the_reason_blank(tmp_data):
    flagged = [{"cutoff": "2025-03-31", "rank": r, "hull_id": f"9{r:06d}", "score": 1 / r,
                "top_drivers": "a; b"} for r in range(1, 30)]
    explain.write_fp_review("2025Q1", flagged)
    got = list(csv.DictReader((config.REPORTS_DIR / "fp_review_2025Q1.csv").read_text().splitlines()))
    assert len(got) == explain.FP_REVIEW_N == 20
    assert all(r["reason"] == "" and r["notes"] == "" for r in got)
    assert [int(r["rank"]) for r in got] == list(range(1, 21))


@pytest.mark.parametrize("cutoff,side", [("2026-02-28", "post_break"), ("2026-02-27", "pre_break"),
                                         ("2024-08-31", "pre_break")])
def test_hormuz_split_uses_the_preregistered_date(cutoff, side):
    assert drift.hormuz_side(cutoff) == side
    assert config.REGIME_BREAKS["hormuz_closure"] == "2026-02-28"


def test_validation_is_the_most_recent_training_cutoff():
    rows = [{"cutoff": c} for c in ["2025-03-31"] * 3 + ["2025-04-30"] * 2]
    assert tabular.validation_mask(rows).tolist() == [False, False, False, True, True]
    # one training cutoff: nothing to hold out, rather than holding out everything
    assert not tabular.validation_mask([{"cutoff": "2025-03-31"}] * 4).any()


def test_lightgbm_is_reproducible():
    rng = np.random.default_rng(0)
    rows = [{"hull_id": str(i), "cutoff": "2025-03-31" if i < 150 else "2025-04-30",
             "n_transits": float(rng.integers(0, 9)), "length_m": float(rng.normal(200, 30))}
            for i in range(200)]
    y = (rng.random(200) < 0.2).astype(int)
    a, _, _ = tabular.train_and_score(rows, y, rows)
    b, _, _ = tabular.train_and_score(rows, y, rows)
    assert np.array_equal(a, b)
