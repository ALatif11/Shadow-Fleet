from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from shadowfleet import config
from shadowfleet.backtest import harness, metrics
from shadowfleet.models import rules


def test_metrics_self_check():
    metrics.demo()


def test_supervised_training_set_respects_the_horizon():
    """The rule is T' + horizon <= T. Gating on the run's end instead is a leak that looks fine."""
    h = [(date(2025, 1, 31), [{"a": 1}], np.array([0])),
         (date(2025, 6, 30), [{"a": 2}], np.array([1]))]
    # 2025-01-31 + 182 d = 2025-08-01, so at 2025-07-31 nothing is usable yet
    assert harness.usable_history(h, date(2025, 7, 31)) == []
    assert len(harness.usable_history(h, date(2025, 8, 31))) == 1
    assert len(harness.usable_history(h, date(2026, 1, 31))) == 2


def test_b2_uses_the_preregistered_weights():
    text = (config.REPO_ROOT / "PREREG.md").read_text()
    assert "3·B1" in text and "spoof_jump_rate_excess" in text
    assert rules.B2_WEIGHTS == {"b1": 3, "long_gap": 2, "sanctioned_partner": 2, "convenience_flag": 1,
                               "old_vessel": 1, "renamed": 1, "spoof_excess": 1}


def test_b2_scores_a_hull_that_trips_every_term():
    worst = {"n_russian_port_visits": 1, "share_russian_destination": 0.5, "gap_hours_total": 48,
             "n_encounters_with_sanctioned_partner": 2, "flag_to_convenience_registry": True,
             "vessel_age_years": 22, "n_name_changes": 3, "spoof_jump_rate_excess": 0.4}
    clean = dict.fromkeys(worst, 0)
    scores = rules.b2_weighted([worst, clean])
    assert scores[0] == sum(rules.B2_WEIGHTS.values()) == 11
    assert scores[1] == 0


def test_b1_fires_on_either_source():
    got = rules.b1_russia_port([{"n_russian_port_visits": 1, "share_russian_destination": 0},
                                {"n_russian_port_visits": 0, "share_russian_destination": 0.2},
                                {"n_russian_port_visits": 0, "share_russian_destination": 0}])
    assert list(got) == [1.0, 1.0, 0.0]


def test_dead_b2_terms_are_reported_not_reweighted():
    """A term whose source phase has not run must show as dead, with its weight untouched."""
    rows = [{"n_russian_port_visits": 0, "share_russian_destination": 1.0, "n_name_changes": 1}]
    terms = rules.b2_live_terms(rows)
    assert terms["b1"] == 1 and terms["renamed"] == 1
    assert terms["long_gap"] == 0 and terms["sanctioned_partner"] == 0
    assert rules.B2_WEIGHTS["long_gap"] == 2  # still 2, not dropped


def test_design_matrix_drops_the_categorical_flag_and_keeps_column_order():
    rows = [{"hull_id": "9074729", "cutoff": date(2025, 1, 1), "current_flag": "GAB",
             "n_transits": 3, "gap_hours_total": None}]
    x, cols = rules.design_matrix(rows)
    assert cols == ["gap_hours_total", "n_transits"]
    assert x.tolist() == [[0.0, 3.0]]  # null becomes zero, flag is gone


def test_b3_returns_zeros_when_there_is_nothing_to_learn_from():
    rows = [{"n_transits": 1}, {"n_transits": 2}]
    assert list(rules.b3_logistic([], np.array([]), rows)) == [0.0, 0.0]
    one_class = rules.b3_logistic(rows, np.array([0, 0]), rows)
    assert list(one_class) == [0.0, 0.0]


def test_pr_auc_is_none_rather_than_zero_when_a_cutoff_has_no_positives():
    y, score = np.zeros(10), np.random.default_rng(0).random(10)
    assert metrics.pr_auc(y, score) is None
    assert metrics.summary(y, score)["pr_auc"] is None
    assert metrics.summary(y, score)["n_positive"] == 0


def test_alerts_for_recall_is_none_when_unreachable():
    y = np.array([1, 0, 0])
    assert metrics.alerts_for_recall(y, np.array([1.0, 0.0, 0.0]), 0.5) == 1
    assert metrics.alerts_for_recall(np.zeros(3), np.zeros(3)) is None


@pytest.mark.parametrize("k", [1, 50, 1000])
def test_precision_at_k_never_exceeds_one_or_divides_by_zero(k):
    y = np.array([1, 0, 1, 0])
    p = metrics.precision_at_k(y, np.arange(4, dtype=float), k)
    assert p is not None and 0.0 <= p <= 1.0
    assert metrics.precision_at_k(np.array([]), np.array([]), k) is None


def _rows(n: int, signal: np.ndarray) -> list[dict]:
    return [{"n_transits": float(s), "gap_hours_total": 0.0, "n_name_changes": 0.0} for s in signal[:n]]


def test_permutation_check_passes_when_nothing_leaks():
    rng = np.random.default_rng(1)
    tr_y = (rng.random(200) < 0.15).astype(int)
    ev_y = (rng.random(200) < 0.15).astype(int)
    out = harness.permutation_check(_rows(200, rng.random(200)), tr_y,
                                   _rows(200, rng.random(200)), ev_y)
    assert out["passes"] is True
    assert out["base_rate"] == pytest.approx(ev_y.mean(), abs=1e-5)  # judged against the eval cutoff


def test_permutation_check_fires_when_the_score_knows_the_answer(monkeypatch):
    """The guard's arithmetic, tested by handing it a scorer that has the eval labels."""
    rng = np.random.default_rng(3)
    tr_y = (rng.random(100) < 0.2).astype(int)
    ev_y = (rng.random(100) < 0.2).astype(int)
    monkeypatch.setattr(rules, "b3_logistic", lambda *a, **k: ev_y.astype(float))
    out = harness.permutation_check(_rows(100, rng.random(100)), tr_y, _rows(100, rng.random(100)), ev_y)
    assert out["passes"] is False and out["pr_auc_shuffled"] == 1.0


def test_permutation_check_skips_rather_than_lying_when_there_are_no_positives():
    zeros = np.zeros(50, dtype=int)
    out = harness.permutation_check(_rows(50, np.zeros(50)), zeros, _rows(50, np.zeros(50)), zeros)
    assert out.get("skipped")


def test_reverse_time_check_compares_both_directions():
    rng = np.random.default_rng(2)
    a_y = (rng.random(120) < 0.2).astype(int)
    b_y = (rng.random(120) < 0.2).astype(int)
    out = harness.reverse_time_check([(date(2025, 1, 31), _rows(120, rng.random(120)), a_y),
                                      (date(2025, 6, 30), _rows(120, rng.random(120)), b_y)])
    assert out["passes"] is True and out["early"] == "2025-01-31" and out["late"] == "2025-06-30"


def test_reverse_time_check_needs_two_cutoffs_with_positives():
    assert harness.reverse_time_check([]).get("skipped")
    assert harness.reverse_time_check(
        [(date(2025, 1, 31), _rows(10, np.zeros(10)), np.zeros(10, dtype=int))]).get("skipped")


def test_entity_resolution_arm_is_a_declared_no_op_while_no_gfw_merge_exists(tmp_data):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from shadowfleet.resolve.identity import HULL_MAP

    config.PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"method": ["imo_majority", "imo_majority", "syn"]}),
                   config.PARQUET_DIR / HULL_MAP)
    out = harness.entity_resolution_delta()
    assert out["passes"] is True and out["windows_from_a_gfw_merge"] == 0 and out["pr_auc_delta"] == 0.0

    # the moment a GFW merge appears, the arm must stop claiming a zero delta
    pq.write_table(pa.table({"method": ["imo_majority", "gfw_vessel_id"]}),
                   config.PARQUET_DIR / HULL_MAP)
    out = harness.entity_resolution_delta()
    assert out["passes"] is False and out["pr_auc_delta"] is None


def test_lead_time_is_measured_from_the_earliest_flag_not_the_latest():
    """Event-study: the question is how early a hull was first flagged, not the average distance."""
    top = {"LGBM": {date(2025, 1, 31): {"A", "B"}, date(2025, 3, 31): {"A"},
                    date(2025, 5, 31): {"A", "C"}}}
    out = harness.lead_time(top, {"A": date(2025, 6, 30), "C": date(2025, 6, 1)})
    a = next(x for x in out["LGBM"]["examples"] if x["hull_id"] == "A")
    assert a["first_flagged"] == "2025-01-31"  # the earliest, not 2025-05-31
    assert a["weeks"] == pytest.approx(21.4, abs=0.2)
    assert out["LGBM"]["flagged_before_designation"] == 2
    assert out["LGBM"]["designated_in_window"] == 2


def test_a_hull_flagged_only_after_its_designation_does_not_count_as_lead():
    top = {"LGBM": {date(2025, 8, 31): {"A"}}}
    out = harness.lead_time(top, {"A": date(2025, 6, 30)})
    assert out["LGBM"]["flagged_before_designation"] == 0
    assert out["LGBM"]["median_weeks"] is None  # None, not zero: there is no lead to report


def test_lead_time_reports_the_designated_population_so_the_censoring_is_visible():
    top = {"LGBM": {date(2025, 1, 31): {"A"}}}
    out = harness.lead_time(top, {"A": date(2025, 6, 30), "B": date(2025, 7, 1), "C": date(2025, 8, 1)})
    assert out["LGBM"]["flagged_before_designation"] == 1 and out["LGBM"]["designated_in_window"] == 3
