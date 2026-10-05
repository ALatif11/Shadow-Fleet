from __future__ import annotations

import csv
import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from shadowfleet import config
from shadowfleet.briefs import bundle as bmod
from shadowfleet.briefs import verify
from shadowfleet.briefs.schema import Brief, render
from shadowfleet.detect import churn, draught, loitering, spoof, sts
from shadowfleet.features import asof
from shadowfleet.ingest import dma
from shadowfleet.resolve import identity
from tests.conftest import DAY, HEADER_V1, row, write_zip

SMALL = {"window_days": 2, "min_imo_days": 2}
IMO_A, IMO_B = "9074729", "9176187"
T = (DAY + timedelta(days=8)).date()


@pytest.fixture()
def store(tmp_data):
    """Two hulls that spend three hours 200 m apart at 0.4 kn, so there is real evidence to bundle."""
    config.DMA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    for off in range(9):
        d = (DAY + timedelta(days=off)).date()
        t0 = (DAY + timedelta(days=off)).replace(hour=2)
        rows = []
        for s in range(0, 3 * 3600, 30):
            t = t0 + timedelta(seconds=s)
            rows += [row(t, "Class A", 777, 57.70, 10.70, sog=0.4, name="ALPHA", imo=IMO_A,
                         draught=9.0 if off < 6 else 14.0),
                     row(t, "Class A", 888, 57.7018, 10.70, sog=0.3, name="BETA", imo=IMO_B)]
        z = write_zip(config.DMA_RAW_DIR / f"{d}.zip", rows, HEADER_V1)
        assert not dma.ingest_zip(z, [d], z.name).days_failed
    con = dma.connect()
    identity.hull_map(con, **SMALL)
    identity.identity_intervals(con)
    for mod in (sts, loitering, draught, spoof, churn):
        mod.run(con)
    return con


def test_bundle_carries_real_records_with_ids_and_no_future_dates(store):
    feat = {r["hull_id"]: r for r in asof.features(T, store)}
    assert IMO_A in feat
    b = bmod.build(IMO_A, T, feat[IMO_A], con=store)
    assert b["hull_id"] == IMO_A and b["cutoff"] == T.isoformat()
    assert b["evidence"], "the fixture has STS candidates, so the bundle cannot be empty"
    assert all(k.startswith("E") for k in b["evidence"])
    assert all(str(r.get("at", ""))[:10] <= T.isoformat() for r in b["evidence"].values())
    families = {r["family"] for r in b["evidence"].values()}
    assert "sts" in families
    sts_rec = next(r for r in b["evidence"].values() if r["family"] == "sts")
    assert sts_rec["partner_hull"] == IMO_B  # the partner, not the hull itself
    assert round(sts_rec["lat"], 2) == sts_rec["lat"]  # positions rounded, so prose cannot invent digits
    # the destinations behind share_russian_destination are evidence too, Russian ones flagged
    dest = next(r for r in b["evidence"].values() if r["family"] == "destination")
    assert dest["destination"] == "PRIMORSK" and dest["russian"] is True


def test_bundle_omits_drivers_rather_than_inventing_them(store):
    feat = {r["hull_id"]: r for r in asof.features(T, store)}
    assert bmod.build(IMO_A, T, feat[IMO_A], shap_top=None, con=store)["drivers"] == []
    with_shap = bmod.build(IMO_A, T, feat[IMO_A], con=store, shap_top=[
        {"feature": "n_sts_candidates", "value": 4.0, "contribution": 1.2}])
    assert with_shap["drivers"][0]["description"], "a driver must carry its registry description"


def test_bundle_truncates_the_least_important_family_first(store):
    feat = {r["hull_id"]: r for r in asof.features(T, store)}
    full = bmod.build(IMO_A, T, feat[IMO_A], con=store)
    tiny = bmod.build(IMO_A, T, feat[IMO_A], con=store, budget=40)
    dropped = tiny["truncated_families"]
    assert dropped, "a 40-token budget must force a trim"
    # whichever families exist, they must be given up in TRUNCATION_ORDER, least important first
    assert dropped == [f for f in bmod.TRUNCATION_ORDER if f in dropped]
    assert "identity" not in dropped or dropped[-1] == "identity"
    assert tiny["approx_tokens"] < full["approx_tokens"]


def _bundle() -> dict:
    return {"hull_id": "9074729", "cutoff": "2025-03-31",
            "header": {"length_m": 250, "n_transits": 4},
            "drivers": [{"feature": "n_sts_candidates", "value": 3.0, "contribution": 1.5,
                         "description": "Self-built STS candidates"}],
            "evidence": {"E1": {"family": "sts", "at": "2025-03-02T04:00:00", "hours": 3.2,
                                "partner_hull": "9176187", "lat": 57.7, "lon": 10.7}},
            "truncated_families": []}


def _brief(**over) -> dict:
    base = {"summary": "The hull shows one ship-to-ship transfer candidate in the window." * 1,
            "risk_level": "medium",
            "findings": [{"claim": "One STS candidate lasting 3.2 hours was detected.",
                          "evidence_ids": ["E1"], "severity": "medium"}],
            "caveats": ["Indicator only."]}
    return {**base, **over}


def test_a_faithful_brief_passes_every_deterministic_check():
    b, bun = _brief(), _bundle()
    out = verify.verify(b, bun, render(b, bun))
    assert out["passes"], out


def test_a_dangling_citation_is_caught():
    b = _brief(findings=[{"claim": "Something happened here.", "evidence_ids": ["E9"],
                          "severity": "low"}])
    out = verify.verify(b, _bundle(), render(b, _bundle()))
    assert out["dangling_citations"] == ["E9"] and not out["passes"]


def test_an_invented_number_is_caught():
    b = _brief(findings=[{"claim": "The transfer lasted 9.9 hours.", "evidence_ids": ["E1"],
                          "severity": "high"}])
    out = verify.verify(b, _bundle(), render(b, _bundle()))
    assert "9.9" in out["ungrounded_numbers"] and not out["passes"]


def test_an_invented_date_is_caught():
    b = _brief(findings=[{"claim": "A transfer occurred on 2025-01-09 near the anchorage.",
                          "evidence_ids": ["E1"], "severity": "high"}])
    out = verify.verify(b, _bundle(), render(b, _bundle()))
    assert out["ungrounded_dates"] == ["2025-01-09"] and not out["passes"]


def test_an_invented_vessel_name_is_caught():
    b = _brief(findings=[{"claim": "It met the tanker Sovcomflot Star at sea.",
                          "evidence_ids": ["E1"], "severity": "high"}])
    out = verify.verify(b, _bundle(), render(b, _bundle()))
    assert out["ungrounded_names"] and not out["passes"]


def test_an_uncovered_top_driver_is_caught():
    """A brief that never cites the record behind its biggest driver is unsupported, not merely terse."""
    bun = _bundle()
    bun["evidence"] = {"E1": {"family": "identity", "at": "2025-03-02T04:00:00", "mmsi": 219000111},
                       "E2": {"family": "sts", "at": "2025-03-02T04:00:00", "hours": 3.2}}
    b = _brief(findings=[{"claim": "The hull reported one identity interval.", "evidence_ids": ["E1"],
                          "severity": "low"}])
    out = verify.verify(b, bun, render(b, bun))
    assert out["uncovered_drivers"] == ["n_sts_candidates"] and not out["passes"]


def test_a_driver_with_no_records_in_the_bundle_is_listed_not_failed():
    """Vessel size has no per-record evidence; a brief cannot cite what the bundle does not contain."""
    bun = _bundle()
    bun["drivers"] = [{"feature": "length_m", "value": 274.0, "contribution": 0.4, "description": ""},
                      {"feature": "n_draught_inconsistencies", "value": 1, "contribution": 0.3,
                       "description": ""}]
    bun["evidence"] = {"E1": {"family": "draught", "at": "2024-12-11T13:58:52", "delta_m": 6.0}}
    b = _brief(findings=[{"claim": "Draught rose by 6.0 m on 2024-12-11.", "evidence_ids": ["E1"],
                          "severity": "medium"}])
    out = verify.verify(b, bun, render(b, bun))
    # the draught driver is backed by the draught record (it used to be mapped to STS and always failed)
    assert out["uncovered_drivers"] == [] and out["drivers_without_records"] == ["length_m"]


def test_citation_ids_country_names_and_bundle_vocabulary_are_not_ungrounded():
    """The first real run's top "ungrounded" items: 1-13 from `(E4)`, Panama for PAN, the word Header."""
    bun = _bundle()
    bun["header"] = {"current_flag": "PAN", "length_m": 274.0}
    b = _brief(summary="Flagged in Panama, length 274.0 m per the Header; see (E1) for March 2025 activity.")
    out = verify.verify(b, bun, render(b, bun))
    assert not out["ungrounded_numbers"] and not out["ungrounded_names"], out


def test_rounding_differences_do_not_count_as_invented_numbers():
    bun = _bundle()
    b = _brief(findings=[{"claim": "The pair closed to 57.70 N and stayed 3.20 hours.",
                          "evidence_ids": ["E1"], "severity": "low"}])
    out = verify.verify(b, bun, render(b, bun))
    assert out["ungrounded_numbers"] == [], out


def test_aggregate_reports_the_share_with_zero_failures():
    good, bad = {"passes": True}, {"passes": False, "ungrounded_numbers": ["9.9"]}
    agg = verify.aggregate([good, good, bad])
    assert agg["briefs"] == 3 and agg["clean"] == 2 and agg["share_clean"] == pytest.approx(0.6667, 1e-3)
    assert agg["briefs_with"]["ungrounded_numbers"] == 1


def test_the_schema_rejects_a_finding_with_no_evidence():
    with pytest.raises(ValidationError):
        Brief(summary="x" * 30, risk_level="low",
              findings=[{"claim": "y" * 20, "evidence_ids": [], "severity": "low"}])


def test_a_mid_sentence_vessel_name_is_still_caught_after_the_grammar_fix():
    """The sentence-initial exemption must not become a hole a fabricated name can walk through."""
    b = _brief(findings=[{"claim": "The hull met Zircon Trader while drifting.",
                          "evidence_ids": ["E1"], "severity": "high"}])
    out = verify.verify(b, _bundle(), render(b, _bundle()))
    assert "Zircon Trader" in out["ungrounded_names"] and not out["passes"]


def test_a_name_that_is_in_the_bundle_is_accepted():
    bun = _bundle()
    bun["evidence"]["E2"] = {"family": "identity", "at": "2025-03-01T00:00:00", "name": "ALPHA STAR"}
    b = _brief(findings=[{"claim": "The hull reported the name ALPHA STAR in the window.",
                          "evidence_ids": ["E2"], "severity": "low"},
                         {"claim": "One STS candidate lasting 3.2 hours was detected.",
                          "evidence_ids": ["E1"], "severity": "medium"}])
    out = verify.verify(b, bun, render(b, bun))
    assert out["ungrounded_names"] == [], out


# ---------------------------------------------------------------- generation, with a stubbed server
def _stub(content: str):
    def completer(messages, schema=None, url=None):
        assert schema and schema["properties"]["findings"], "the schema must be sent for constrained decoding"
        assert messages[0]["role"] == "system" and "only facts that appear" in messages[0]["content"]
        return content, {"completion_tokens": 120}
    return completer


def test_generate_returns_a_validated_brief():
    from shadowfleet.briefs import generate as gen

    good = json.dumps({"summary": "A" * 40, "risk_level": "low",
                       "findings": [{"claim": "B" * 20, "evidence_ids": ["E1"], "severity": "low"}],
                       "caveats": []})
    brief, meta = gen.generate(_bundle(), completer=_stub(good))
    assert brief and brief["risk_level"] == "low" and meta["attempts"] == 1


def test_generate_retries_once_then_gives_up_rather_than_returning_raw_output():
    from shadowfleet.briefs import generate as gen

    brief, meta = gen.generate(_bundle(), completer=_stub('{"summary": "too short"}'))
    assert brief is None and meta["attempts"] == gen.RETRIES + 1 and meta["errors"]


def test_generate_never_returns_output_that_fails_the_schema():
    from shadowfleet.briefs import generate as gen

    # valid JSON, invalid brief: a finding with no evidence ids
    bad = json.dumps({"summary": "A" * 40, "risk_level": "low",
                      "findings": [{"claim": "B" * 20, "evidence_ids": [], "severity": "low"}],
                      "caveats": []})
    brief, _ = gen.generate(_bundle(), completer=_stub(bad))
    assert brief is None


def test_run_batch_writes_briefs_and_verifies_every_one(store):
    from shadowfleet.briefs import generate as gen

    feat = {r["hull_id"]: r for r in asof.features(T, store)}
    # a flagged list is what run_batch reads, so write one the way Phase 6 would
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.REPORTS_DIR / f"flagged_{T.isoformat()}.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["rank", "hull_id", "score", "label", "designation_date"])
        for i, hull in enumerate(feat, start=1):
            wr.writerow([i, hull, 0.9, 0, ""])
    good = json.dumps({"summary": "A" * 40, "risk_level": "medium",
                       "findings": [{"claim": "B" * 20, "evidence_ids": ["E1"], "severity": "low"}],
                       "caveats": []})
    out = gen.run_batch([T], top_k=5, completer=_stub(good))
    assert out["cutoffs"][0]["briefs"] == len(feat)
    assert len(out["verified"]) == len(feat)
    assert out["faithfulness"]["briefs"] == len(feat)
    d = config.REPORTS_DIR / "briefs" / T.isoformat()
    assert (d / f"{IMO_A}.md").exists() and (d / f"{IMO_A}.json").exists()
    assert "Risk level: medium" in (d / f"{IMO_A}.md").read_text()

    # a rerun after a crash resumes: nothing already on disk goes back to the model
    def refuse(*a, **k):
        raise AssertionError("a brief already on disk was regenerated")
    again = gen.run_batch([T], top_k=5, completer=refuse)
    assert again["resumed"] == len(feat) and again["faithfulness"]["briefs"] == len(feat)

    # but a brief built from a different bundle or prompt is stale, and is regenerated rather than resumed
    stale = json.loads((d / f"{IMO_A}.json").read_text())
    stale["meta"]["key"] = "old prompt"
    (d / f"{IMO_A}.json").write_text(json.dumps(stale))
    third = gen.run_batch([T], top_k=5, completer=_stub(good))
    assert third["resumed"] == len(feat) - 1


def test_phase8_report_states_the_deterministic_half_only(store):
    from shadowfleet.util import report

    text = report.render_phase8({"faithfulness": {"briefs": 4, "clean": 3, "share_clean": 0.75,
                                                  "briefs_with": {"ungrounded_numbers": 1}},
                                 "seconds": 12.0, "cutoffs": [{"cutoff": "2025-03-31", "briefs": 4,
                                                               "dir": "x"}],
                                 "failed": []})
    assert "3 of 4 briefs have zero verifier failures" in text
    assert "deterministic half only" in text  # the judge is missing and the report must say so
    assert "audit_sheet.csv" in text
