from __future__ import annotations

import csv
import json

import pytest

from shadowfleet import config
from shadowfleet.briefs import judge


def _bundle() -> dict:
    return {"hull_id": "9074729", "cutoff": "2025-03-31",
            "evidence": {"E1": {"family": "sts", "hours": 3.2, "partner_hull": "9176187"},
                         "E2": {"family": "identity", "mmsi": 219000111}}}


def _finding(ids=("E1",), severity="medium") -> dict:
    return {"claim": "One STS candidate lasting 3.2 hours.", "evidence_ids": list(ids),
            "severity": severity}


def _stub(verdict="entailed", capture=None):
    def completer(messages, schema=None, url=None):
        if capture is not None:
            capture.append(json.loads(messages[1]["content"]))
        return json.dumps({"verdict": verdict, "reason": "hours field matches"}), {}
    return completer


def test_the_judge_only_sees_the_records_the_finding_cited():
    """Given the whole bundle a judge can justify a claim from evidence the brief never pointed at."""
    seen: list[dict] = []
    judge.judge_finding(_finding(("E1",)), _bundle(), _stub(capture=seen))
    assert list(seen[0]["evidence"]) == ["E1"], "E2 must not be visible to the judge"
    assert seen[0]["claim"].startswith("One STS")


def test_a_dangling_citation_is_not_entailed_without_asking_the_model():
    out = judge.judge_finding(_finding(("E9",)), _bundle(), _stub())
    assert out["verdict"] == "not_entailed" and out["judged_by"] == "deterministic"


def test_a_model_error_is_recorded_rather_than_counted_as_entailed():
    def boom(messages, schema=None, url=None):
        raise RuntimeError("server down")
    out = judge.judge_finding(_finding(), _bundle(), boom)
    assert out["verdict"] is None and out["judged_by"] == "error"


def test_invalid_judge_output_is_an_error_not_a_verdict():
    def bad(messages, schema=None, url=None):
        return json.dumps({"verdict": "probably", "reason": "hmm"}), {}
    assert judge.judge_finding(_finding(), _bundle(), bad)["judged_by"] == "error"


def test_same_family_judge_is_refused():
    briefs = [{"brief": {"findings": [_finding()]}, "bundle": _bundle()}]
    with pytest.raises(SystemExit) as e:
        judge.judge_all(briefs, completer=_stub(), judge_model="Gemma 4 12B", generator_model="Gemma 4 12B")
    assert "cross-family" in str(e.value)


def test_cross_family_judge_runs_and_reports_rates_by_severity():
    briefs = [{"brief": {"findings": [_finding(severity="high"), _finding(("E9",), severity="low")]},
               "bundle": _bundle()}]
    out = judge.judge_all(briefs, completer=_stub("entailed"),
                          judge_model="Qwen3-14B", generator_model="Gemma 4 12B")
    assert out["findings"] == 2
    assert out["by_severity"]["high"] == 1.0
    assert out["by_severity"]["low"] == 0.0  # the dangling one is graded not_entailed
    assert out["entailment_rate"] == 0.5


def test_the_default_judge_and_generator_are_different_families():
    """The config defaults must not quietly make the judge grade its own output."""
    assert judge._family(config.LLM_PRIMARY) != judge._family(config.LLM_FALLBACK)


def _sheet(rows: list[dict]) -> None:
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.REPORTS_DIR / "audit_sheet.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["cutoff", "hull_id", "verifier_passed", "verifier_failures",
                                           "human_verdict", "error_type", "notes"])
        wr.writeheader()
        wr.writerows(rows)


def test_kappa_skips_an_unfilled_sheet_rather_than_returning_zero(tmp_data):
    assert judge.kappa_from_sheet()["skipped"]
    _sheet([{"cutoff": "c", "hull_id": "h", "verifier_passed": "True", "verifier_failures": "{}",
             "human_verdict": "", "error_type": "", "notes": ""}])
    assert judge.kappa_from_sheet()["skipped"], "a sheet with no filled rows has no kappa"


def test_kappa_is_one_when_the_human_and_the_machine_agree(tmp_data):
    _sheet([{"cutoff": "c", "hull_id": f"h{i}", "verifier_passed": str(i % 2 == 0),
             "verifier_failures": "{}", "human_verdict": "pass" if i % 2 == 0 else "fabricated_fact",
             "error_type": "", "notes": ""} for i in range(10)])
    out = judge.kappa_from_sheet()
    assert out["n"] == 10 and out["raw_agreement"] == 1.0 and out["kappa"] == 1.0


def test_kappa_is_undefined_rather_than_misleading_when_one_rater_never_varies(tmp_data):
    _sheet([{"cutoff": "c", "hull_id": f"h{i}", "verifier_passed": "True", "verifier_failures": "{}",
             "human_verdict": "pass", "error_type": "", "notes": ""} for i in range(6)])
    out = judge.kappa_from_sheet()
    assert out["kappa"] is None and out["raw_agreement"] == 1.0 and "undefined" in out["note"]


def test_phase9_report_says_the_number_is_unverified_until_the_sheet_is_filled(tmp_data):
    from shadowfleet.util import report

    text = report.render_phase9({"generator_model": "Gemma 4 12B", "judge_model": "Qwen3-14B",
                                 "findings": 40, "entailment_rate": 0.9, "errors": 0,
                                 "by_severity": {"high": 0.8}, "by_cutoff": {"2025-03-31": 0.9},
                                 "kappa": {"skipped": "audit_sheet.csv missing"}})
    assert "Entailment rate 0.9" in text
    assert "one model's opinion of another's" in text  # the caveat must survive refactors
    filled = report.render_phase9({"generator_model": "Gemma 4 12B", "judge_model": "Qwen3-14B",
                                   "findings": 40, "entailment_rate": 0.9, "errors": 0,
                                   "kappa": {"n": 30, "raw_agreement": 0.9, "kappa": 0.78}})
    assert "Cohen's kappa 0.78" in filled
