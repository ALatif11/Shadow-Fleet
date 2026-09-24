from __future__ import annotations

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
    bun["evidence"] = {"E1": {"family": "identity", "at": "2025-03-02T04:00:00", "mmsi": 219000111}}
    b = _brief(findings=[{"claim": "The hull reported one identity interval.", "evidence_ids": ["E1"],
                          "severity": "low"}])
    out = verify.verify(b, bun, render(b, bun))
    assert out["uncovered_drivers"] == ["n_sts_candidates"] and not out["passes"]


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
