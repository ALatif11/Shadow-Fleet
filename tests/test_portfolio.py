from __future__ import annotations

from shadowfleet import config
from shadowfleet.util import portfolio, probes


def test_an_unmeasured_lead_time_does_not_render_as_nested_bold(tmp_data):
    """`**not measured yet** (...) weeks**` is broken markdown and reads as a number that is not there."""
    probes.write("backtest", {"aggregate": [
        {"label_set": "union", "stratum": "b1", "model": "LGBM", "precision_at_50": 0.4, "pr_auc": 0.3,
         "recall_at_50": 0.2, "cutoffs": 4}], "lead_time": {"LGBM": {"median_weeks": None,
                                                                    "flagged_before_designation": 0,
                                                                    "designated_in_window": 7}}})
    text = portfolio.render_readme()
    assert "weeks**" not in text.split("## What was built")[0].replace("**9.4 weeks**", "")
    assert portfolio.MISSING in text


def test_an_unmeasured_number_renders_as_a_marker_naming_its_command(tmp_data):
    """Rule 4: a half-finished README must read as half-finished, not as a modest result."""
    text = portfolio.render_readme()
    assert portfolio.MISSING in text
    assert "`make backtest`" in text and "`make identity`" in text
    assert "0.0" not in text.split("## Limitations")[0], "no zero may stand in for an unmeasured value"


def test_measured_numbers_come_from_the_probe_files(tmp_data):
    probes.write("backtest", {"aggregate": [
        {"label_set": "union", "stratum": "b1", "model": "LGBM", "precision_at_50": 0.42,
         "pr_auc": 0.31, "recall_at_50": 0.2, "cutoffs": 12},
        {"label_set": "union", "stratum": "b1", "model": "B2_weighted", "precision_at_50": 0.30,
         "pr_auc": 0.2, "recall_at_50": 0.1, "cutoffs": 12}],
        "lead_time": {"LGBM": {"median_weeks": 9.4, "flagged_before_designation": 18,
                               "designated_in_window": 40}}})
    probes.write("identity", {"coverage": {"share_by_imo": 0.87}})
    text = portfolio.render_readme()
    assert "**`LGBM`, reaches precision@50 0.42**" in text
    assert "| `B2_weighted` | 0.3 |" in text  # every model in the comparison table
    assert "**9.4 weeks**" in text and "18 of 40 designated hulls" in text
    assert "0.87 of MMSI-days" in text


def test_the_headline_is_the_preregistered_model_even_when_another_scores_higher(tmp_data):
    # the headline may not be chosen after the fact; the better model still shows, in the table
    row = {"label_set": "union", "stratum": "b1", "pr_auc": 0.1, "recall_at_50": 0.1, "cutoffs": 13}
    probes.write("backtest", {"aggregate_matched": [
        {**row, "model": "ISO_forest", "precision_at_50": 0.19}, {**row, "model": "LGBM", "precision_at_50": 0.18},
        {**row, "model": "B0_random", "precision_at_50": 0.07}],
        "aggregate": [{**row, "model": "ISO_forest", "precision_at_50": 0.25, "cutoffs": 20}]})
    text = portfolio.render_readme()
    assert "**`LGBM`, reaches precision@50 0.18**" in text
    assert "| `ISO_forest` | 0.19 | 2.7x |" in text and "0.25" not in text  # matched cutoffs, not all


def test_the_forward_lists_come_from_the_manifest(tmp_data):
    d = config.REPORTS_DIR / "forward"
    d.mkdir(parents=True)
    (d / "README.md").write_text("| 2026-10-02 | `top50_2026-10-02.csv` | LGBM | 2909 | `" + "a" * 64 + "` |\n")
    assert "| 2026-10-02 | LGBM | 2909 | `aaaaaaaaaaaa...` |" in portfolio.render_readme()


def test_the_wrong_stratum_or_label_set_never_becomes_the_headline(tmp_data):
    probes.write("backtest", {"aggregate": [
        {"label_set": "ofac_only", "stratum": "b1", "model": "CHEAT", "precision_at_50": 0.99,
         "pr_auc": 0.9, "recall_at_50": 0.9, "cutoffs": 3},
        {"label_set": "union", "stratum": "all", "model": "ALSOCHEAT", "precision_at_50": 0.98,
         "pr_auc": 0.9, "recall_at_50": 0.9, "cutoffs": 3}]})
    text = portfolio.render_readme()
    assert "CHEAT" not in text, "PREREG fixes the headline as union label, B1 stratum"


def test_the_limitations_section_always_survives(tmp_data):
    """The parts a reader most needs are the parts most likely to be dropped in a rewrite."""
    text = portfolio.render_readme()
    for must in ("R3", "R12", "R9", "R8", "not blind to the test period", "right-censored"):
        assert must in text, must


def test_portfolio_bullets_refuse_to_be_resume_ready_while_unmeasured(tmp_data):
    out = portfolio.write()
    assert out["unmeasured"] > 0
    text = (config.REPORTS_DIR / "portfolio.md").read_text()
    assert "not ready to put on a resume" in text
    assert "ownership sits behind shell companies" in text  # the limitation leads the spoken version


def test_readme_on_disk_has_no_section_the_generator_would_delete():
    """`make readme` rewrites README.md whole, so a hand-written section is deleted on the next run.

    Not a style rule: the UI console section was added to README.md by hand and vanished the next time
    the suite ran. Anything a reader must keep seeing belongs in `render_readme`.
    """
    import re

    from shadowfleet import config as real_config  # not the tmp_data-redirected REPO_ROOT

    disk = (real_config.REPO_ROOT / "README.md").read_text()
    generated = portfolio.render_readme()
    orphans = [h for h in re.findall(r"^## .*", disk, re.M) if h not in generated]
    assert not orphans, f"{orphans} exist only in README.md; move them into portfolio.render_readme()"


def test_briefs_section_and_rival_sentence_come_from_the_probes(tmp_data):
    """The brief layer's numbers, and an honest line when the primary does not clearly beat a rival."""
    row = {"label_set": "union", "stratum": "b1", "recall_at_50": 0.3, "cutoffs": 13}
    probes.write("backtest", {"aggregate_matched": [
        {**row, "model": "LGBM", "precision_at_50": 0.1846, "pr_auc": 0.1783},
        {**row, "model": "ISO_forest", "precision_at_50": 0.1862, "pr_auc": 0.1739},
        {**row, "model": "B0_random", "precision_at_50": 0.0692, "pr_auc": 0.085}]})
    probes.write("briefs", {"faithfulness": {"briefs": 650, "clean": 515, "share_clean": 0.7923}})
    probes.write("judge", {"judge_model": "/home/someone/models/Qwen3-14B-Q4_K_M.gguf", "findings": 3100,
                           "entailment_rate": 0.8477, "by_severity": {"high": 0.79, "medium": 0.92, "low": 0.82},
                           "kappa": {"skipped": "0 usable rows"}})
    text = portfolio.render_readme()
    assert "does not clearly beat `ISO_forest`" in text and "(0.1846 vs 0.1862" in text
    assert "515 of 650" in text and "**0.8477** entailed" in text and "`Qwen3-14B-Q4_K_M`" in text
    assert "/home/someone" not in text, "a home directory must not reach the README"
    assert "make kappa" in text, "kappa stays a visible gap until Adam fills the audit sheet"
    assert "****" not in text, "an unmeasured value inside bold markers renders as broken markdown"
