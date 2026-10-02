"""One test that runs every phase in order on a small synthetic store.

This is the guard the project brief asks for: a line written in week 1 must still work with week 8's code.
The unit tests check each phase alone; nothing else checks that Phase 6's flagged CSV is the file Phase 8
reads, or that Phase 3's hull ids are the ones the detectors write, or that the feature registry the report
prints is the one the harness scores. Those are exactly the seams that rot silently.

It runs a short store on purpose, so the supervised models legitimately report "not scored". The long
version (430 days, 300 hulls, a real supervised cutoff) is a manual rehearsal script, not a unit test.
"""

from __future__ import annotations

import csv
import json
from datetime import timedelta

import pytest

from shadowfleet import config, phase1
from shadowfleet.backtest import harness
from shadowfleet.briefs import generate as gen
from shadowfleet.detect import churn, draught, loitering, spoof, sts
from shadowfleet.features import asof
from shadowfleet.ingest import dma
from shadowfleet.labels import labels as lab
from shadowfleet.resolve import identity
from shadowfleet.util import probes
from tests.conftest import DAY, HEADER_V1, row, valid_imos, write_zip

SMALL = {"window_days": 2, "min_imo_days": 2}
DAYS = 12
IMOS = tuple(valid_imos(3))
T = (DAY + timedelta(days=DAYS - 1)).date()


def _stub_completer(messages, schema=None, url=None):
    return json.dumps({"summary": "S" * 40, "risk_level": "low",
                       "findings": [{"claim": "C" * 20, "evidence_ids": ["E1"], "severity": "low"}],
                       "caveats": ["Synthetic."]}), {"completion_tokens": 10}


@pytest.fixture()
def ingested(tmp_data):
    config.DMA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    for off in range(DAYS):
        d = (DAY + timedelta(days=off)).date()
        t0 = (DAY + timedelta(days=off)).replace(hour=2)
        rows = []
        for s in range(0, 3 * 3600, 30):  # three hours of two hulls 200 m apart -> STS candidates
            t = t0 + timedelta(seconds=s)
            rows += [row(t, "Class A", 777, 57.70, 10.70, sog=0.4, name="ALPHA", imo=IMOS[0],
                         draught=9.0 if off < 8 else 14.0),
                     row(t, "Class A", 888, 57.7018, 10.70, sog=0.3, name="BETA", imo=IMOS[1])]
        for m in range(30):  # a third hull moving, so not every hull is an STS participant
            rows.append(row(t0 + timedelta(hours=8, minutes=m), "Class A", 999, 57.0 + m * 0.002, 10.0,
                            sog=11.0, name="GAMMA", imo=IMOS[2]))
        z = write_zip(config.DMA_RAW_DIR / f"{d}.zip", rows, HEADER_V1)
        assert not dma.ingest_zip(z, [d], z.name, keep_fullres={d}).days_failed
    return dma.connect()


def test_every_phase_runs_in_order_and_hands_the_next_one_what_it_expects(ingested):
    con = ingested

    # Phase 1
    pop = phase1.population(con)
    assert pop["mmsi_ever_tanker_class"] == 3
    phase1.type_changes(con)

    # Phase 2: labels, with one hull designated inside the horizon of an early cutoff
    lab.write_actions([{"source": "OFAC", "action": "add", "date": (DAY + timedelta(days=DAYS)).date(),
                        "imo": int(IMOS[0]), "name": "ALPHA", "program": "RUSSIA-EO14024",
                        "via": "test", "raw": ""}])

    # Phase 3
    hm = identity.hull_map(con, **SMALL)
    assert hm["windows_by_imo"] > 0, "the resolver must name hulls or every later phase keys on nothing"
    identity.identity_intervals(con)
    assert identity.coverage(con)["share_by_imo_of_mapped"] == 1.0

    # Phase 4b
    assert sts.run(con)["candidates"] >= 1
    loitering.run(con)
    assert draught.run(con)["changes"] >= 1
    spoof.run(con)
    churn.run(con)

    # Phase 5a: the feature store sees every detector's table
    rows = asof.features(T, con)
    assert rows and set(rows[0]) == {"hull_id", "cutoff", *asof.FEATURES}
    alpha = next(r for r in rows if r["hull_id"] == IMOS[0])
    assert alpha["n_sts_candidates"] > 0, "Phase 4b's STS table must reach the Phase 5a feature row"
    assert alpha["n_draught_inconsistencies"] + alpha["n_sts_with_draught_change"] > 0

    # Phase 5b + 6: the harness scores, the leakage checks run, the artefacts land
    out = harness.run([T], full=True)
    assert out["leakage_failed"] == [], out["leakage"]
    assert out["cutoffs_scored"] == 1
    assert (config.REPORTS_DIR / "phase5b.md").exists()
    assert (config.REPORTS_DIR / "phase6.md").exists()
    assert (config.REPORTS_DIR / "metrics_by_cutoff.csv").exists()

    # PREREG section 10: a failing leakage test blocks every metric, and removes the last run's CSV
    from shadowfleet.backtest import leakage
    real = leakage.run_all
    leakage.run_all = lambda by_cutoff: {**real(by_cutoff), "reverse_time": {"passes": False}}
    try:
        with pytest.raises(SystemExit):
            harness.run([T], full=True)
    finally:
        leakage.run_all = real
    blocked = probes.read("backtest")
    assert blocked["leakage_failed"] == ["reverse_time"] and "aggregate" not in blocked
    assert not (config.REPORTS_DIR / "metrics_by_cutoff.csv").exists()
    assert "BLOCKED" in (config.REPORTS_DIR / "phase5b.md").read_text()

    # Phase 8 reads Phase 6's flagged list, so write one the way Phase 6 does when a model is scored
    with open(config.REPORTS_DIR / f"flagged_{T.isoformat()}.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["rank", "hull_id", "score", "label", "designation_date"])
        for i, r in enumerate(rows, start=1):
            wr.writerow([i, r["hull_id"], 1 / i, 0, ""])

    briefs = gen.run_batch([T], top_k=10, completer=_stub_completer)
    assert briefs["faithfulness"]["briefs"] == len(rows)
    assert briefs["faithfulness"]["clean"] >= 1, "a brief over real evidence must pass the verifier"
    assert (config.REPORTS_DIR / "briefs" / T.isoformat() / f"{IMOS[0]}.md").exists()
    assert (config.REPORTS_DIR / "audit_sheet.csv").exists()


def test_the_population_excludes_a_hull_listed_before_the_cutoff(ingested):
    """Rule 3, end to end: a designation dated before T removes the hull from every later phase."""
    con = ingested
    identity.hull_map(con, **SMALL)
    identity.identity_intervals(con)
    assert IMOS[0] in {r["hull_id"] for r in asof.features(T, con)}

    lab.write_actions([{"source": "EU", "action": "add", "date": (DAY + timedelta(days=3)).date(),
                        "imo": int(IMOS[0]), "name": "ALPHA", "program": "EU-MARE",
                        "via": "test", "raw": ""}])
    after = {r["hull_id"] for r in asof.features(T, con)}
    assert IMOS[0] not in after and IMOS[1] in after
