from __future__ import annotations

import csv
from datetime import date, timedelta

import pytest

from shadowfleet import config
from shadowfleet.backtest import forward
from shadowfleet.labels import labels as lab
from tests.conftest import valid_imos

T = date(2026, 10, 1)
IMOS = valid_imos(4)


def _committed_list(tmp_data) -> None:
    d = config.REPORTS_DIR / forward.DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"top50_{T.isoformat()}.csv"
    with open(path, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["rank", "hull_id", "imo", "score", *[f"driver_{i + 1}" for i in range(5)]])
        for rank, imo in enumerate(IMOS, start=1):
            wr.writerow([rank, imo, imo, round(1 / rank, 6), "n_sts_candidates", "", "", "", ""])
    forward._append_manifest(T, path, forward.sha256(path), "LGBM", 2500)


def test_scoring_before_the_preregistered_date_is_refused(tmp_data):
    with pytest.raises(SystemExit) as e:
        forward.score(date(2026, 9, 30))
    assert config.FORWARD_TEST_SCORING_DATE in str(e.value)


def test_the_manifest_is_append_only_and_records_the_hash(tmp_data):
    _committed_list(tmp_data)
    text = (config.REPORTS_DIR / forward.DIR / "README.md").read_text()
    assert f"top50_{T.isoformat()}.csv" in text and "LGBM" in text
    _committed_list(tmp_data)  # a second scoring run appends rather than replacing
    assert text.count("|") < (config.REPORTS_DIR / forward.DIR / "README.md").read_text().count("|")


def test_evaluate_is_read_only_and_reports_hits_and_lead_time(tmp_data):
    _committed_list(tmp_data)
    before = forward.sha256(config.REPORTS_DIR / forward.DIR / f"top50_{T.isoformat()}.csv")
    lab.write_actions([{"source": "OFAC", "action": "add", "date": T + timedelta(days=40),
                        "imo": int(IMOS[0]), "name": "A", "program": "P", "via": "t", "raw": ""},
                       {"source": "EU", "action": "add", "date": T + timedelta(days=100),
                        "imo": int(IMOS[2]), "name": "C", "program": "P", "via": "t", "raw": ""}])
    out = forward.evaluate(T, as_of=T + timedelta(days=120))
    assert out["designated_so_far"] == 2 and out["sha256_ok"]
    assert [h["rank"] for h in out["hits"]] == [1, 3]
    assert out["median_lead_days"] == 70 and out["precision_at_25"] == pytest.approx(2 / 4)
    assert "right-censored" in out["note"]
    assert forward.sha256(config.REPORTS_DIR / forward.DIR / f"top50_{T.isoformat()}.csv") == before


def test_an_edited_list_voids_the_forward_test(tmp_data):
    _committed_list(tmp_data)
    path = config.REPORTS_DIR / forward.DIR / f"top50_{T.isoformat()}.csv"
    rows = path.read_text().splitlines()
    rows[1] = rows[1].replace(IMOS[0], IMOS[3])  # swap the top pick for one that was designated
    path.write_text("\n".join(rows) + "\n")
    with pytest.raises(SystemExit) as e:
        forward.evaluate(T)
    assert "void" in str(e.value)


def test_a_designation_after_the_horizon_does_not_count(tmp_data):
    _committed_list(tmp_data)
    lab.write_actions([{"source": "OFAC", "action": "add",
                        "date": T + timedelta(days=config.HORIZON_DAYS + 10),
                        "imo": int(IMOS[0]), "name": "A", "program": "P", "via": "t", "raw": ""}])
    out = forward.evaluate(T, as_of=T + timedelta(days=400))
    assert out["designated_so_far"] == 0 and out["hits"] == []


def test_evaluate_skips_cleanly_when_nothing_was_committed(tmp_data):
    assert forward.evaluate(T)["skipped"]


def test_the_committed_list_carries_no_gfw_derived_value(tmp_data):
    """Rule 7: this file can live in a public repo, so it may name a GFW feature but never carry its value."""
    _committed_list(tmp_data)
    text = (config.REPORTS_DIR / forward.DIR / f"top50_{T.isoformat()}.csv").read_text()
    header = text.splitlines()[0].split(",")
    assert header == ["rank", "hull_id", "imo", "score", "driver_1", "driver_2", "driver_3", "driver_4",
                      "driver_5"]
    from shadowfleet.features.asof import FEATURES
    gfw = {n for n, (f, _) in FEATURES.items() if f in forward.GFW_FAMILIES}
    drivers = [c for line in text.splitlines()[1:] for c in line.split(",")[4:] if c]
    assert all("=" not in d for d in drivers), "drivers must be names, never name=value"
    assert forward._drivers("x", {"x": [{"feature": next(iter(gfw))}, {"feature": "n_sts_candidates"}]}) \
        == ["n_sts_candidates"]


def test_a_list_with_no_recorded_hash_is_refused(tmp_data):
    """Fail closed: a missing manifest entry is the same evidential problem as an edited list."""
    _committed_list(tmp_data)
    (config.REPORTS_DIR / forward.DIR / "README.md").unlink()
    with pytest.raises(SystemExit) as e:
        forward.evaluate(T)
    assert "records no hash" in str(e.value)
