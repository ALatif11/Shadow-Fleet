"""UI contract (ADR-18): schema drift, sample freshness, invariants, bundle safety."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from shadowfleet import config
from shadowfleet.ui_export import bundle, contract, export, fixtures
from shadowfleet.ui_export.contract import Event, Outcome, Track, Watchlist, WatchlistRow


@pytest.fixture(scope="module")
def full():
    return fixtures.build("full")


def _row(rank, score, designated=None, hull="fixture:0001"):
    return WatchlistRow(rank=rank, hull_id=hull, imo=None, name="SYN X", flag_iso3="PAN", score=score,
                        b1_stratum=False, drivers=[], has_dossier=False,
                        outcome=Outcome(label=0, designation_date=designated))


def test_committed_schema_matches_models():
    """Fails when contract.py changed without `make ui-schema` (the UI would build against stale types)."""
    for name, schema in contract.json_schemas().items():
        path = config.UI_SCHEMA_DIR / f"{name}.schema.json"
        assert path.exists(), f"missing {path}; run make ui-schema"
        assert json.loads(path.read_text()) == json.loads(json.dumps(schema)), f"{path} is stale; run make ui-schema"


def test_committed_sample_is_fresh(tmp_path):
    m, w, d = fixtures.build("tiny")
    bundle.write_bundle(tmp_path / "s", m, w, d)
    fresh = sorted(p.relative_to(tmp_path / "s") for p in (tmp_path / "s").rglob("*.json"))
    committed = sorted(p.relative_to(config.UI_SAMPLE_DIR) for p in config.UI_SAMPLE_DIR.rglob("*.json"))
    assert fresh == committed, "sample file set changed; run make ui-schema"
    for rel in fresh:
        assert (tmp_path / "s" / rel).read_text() == (config.UI_SAMPLE_DIR / rel).read_text(), rel


def test_sample_round_trips():
    m, ws, ds = bundle.read_bundle(config.UI_SAMPLE_DIR)
    assert m.origin == "synthetic" and ws and ds


def test_fixture_is_deterministic_and_labelled(full):
    m, ws, ds = full
    a, b = fixtures.build("tiny"), fixtures.build("tiny")
    assert a == b
    assert m.origin == "synthetic" and any("SYNTHETIC" in n for n in m.notes)
    assert all(d.imo is None and d.hull_id.startswith("fixture:") for d in ds)
    assert all(i.mmsi >= 999_000_000 for d in ds for i in d.identity)
    assert all(r.name.startswith("SYN ") for w in ws for r in w.rows)


def test_fixture_respects_population_and_training_rules(full):
    m, ws, _ = full
    supervised = {c.cutoff for c in m.cutoffs if c.supervised}
    for w in ws:
        if w.model in fixtures.SUPERVISED_MODELS:
            assert w.cutoff in supervised, "supervised model scored before any horizon closed"
        for r in w.rows:
            if r.outcome.label == 1:
                assert w.cutoff < r.outcome.designation_date <= w.cutoff + timedelta(days=config.HORIZON_DAYS)
    assert len({(w.cutoff, w.model, w.label_set) for w in ws}) == len(ws)
    assert all(len(w.rows) == 100 for w in ws)


def test_fixture_features_are_point_in_time():
    """Truncating every hull's history at T must not change features(T) (same contract as the 5a suite)."""
    g = fixtures._Gen(3, date(2024, 1, 1), date(2025, 6, 30), 25, 1)
    g.make_hulls()
    for h in g.hulls:
        g.build_identity(h)
        g.build_voyages(h)
        g.build_other(h)
    T = date(2025, 1, 31)
    hi = datetime(2025, 2, 1, tzinfo=UTC)
    for h in g.hulls:
        full_f = g.features(h, T)
        saved = (h.events, h.transits)
        h.events = [e for e in h.events if e.observed_at < hi]
        h.transits = [x for x in h.transits if x[0] < hi]
        assert g.features(h, T) == full_f, h.hull_id
        h.events, h.transits = saved


def test_watchlist_rejects_hull_listed_before_cutoff():
    with pytest.raises(ValidationError, match="rule 3"):
        Watchlist(origin="synthetic", cutoff=date(2025, 1, 31), model="m", label_set="ofac_eu_uk",
                  population_size=10, rows=[_row(1, 0.9, designated=date(2025, 1, 31))])


def test_watchlist_rank_and_score_order():
    with pytest.raises(ValidationError, match="ranks"):
        Watchlist(origin="synthetic", cutoff=date(2025, 1, 31), model="m", label_set="ofac_eu_uk",
                  population_size=10, rows=[_row(2, 0.9)])
    with pytest.raises(ValidationError, match="non-increasing"):
        Watchlist(origin="synthetic", cutoff=date(2025, 1, 31), model="m", label_set="ofac_eu_uk",
                  population_size=10, rows=[_row(1, 0.1), _row(2, 0.9, hull="fixture:0002")])


def test_event_time_rules():
    t = datetime(2025, 1, 1, tzinfo=UTC)
    base = dict(id="E1", type="gap", source="gfw", start=t, end=t + timedelta(hours=5), summary="x")
    Event(**base, observed_at=t + timedelta(hours=5))
    with pytest.raises(ValidationError, match="rule 1"):
        Event(**base, observed_at=t - timedelta(hours=1))
    with pytest.raises(ValidationError, match="UTC"):
        Event(**base | {"start": datetime(2025, 1, 1, tzinfo=UTC).astimezone(
            __import__("datetime").timezone(timedelta(hours=1)))}, observed_at=t + timedelta(hours=5))
    with pytest.raises(ValidationError):
        Event(**base | {"start": datetime(2025, 1, 1)}, observed_at=t)  # naive


def test_track_shape():
    with pytest.raises(ValidationError, match="equal length"):
        Track(t=[1, 2], lon=[1.0], lat=[1.0, 2.0], sog=[None, None], draught=[None, None])
    with pytest.raises(ValidationError, match="time-ordered"):
        Track(t=[2, 1], lon=[1.0, 1.0], lat=[1.0, 2.0], sog=[None, None], draught=[None, None])


def test_cross_check_catches_dossier_mismatch():
    m, ws, ds = fixtures.build("tiny")
    with pytest.raises(bundle.BundleError, match="manifest.vessels"):
        bundle.cross_check(m, ws, ds[1:])
    used = next(dr.feature for w in ws for r in w.rows for dr in r.drivers)
    bad = m.model_copy(update={"features": [f for f in m.features if f.name != used]})
    with pytest.raises(bundle.BundleError, match="feature registry"):
        bundle.cross_check(bad, ws, ds)


def test_write_bundle_refuses_foreign_dir_and_swaps_atomically(tmp_path):
    m, ws, ds = fixtures.build("tiny")
    foreign = tmp_path / "notabundle"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("x")
    with pytest.raises(bundle.BundleError, match="refusing"):
        bundle.write_bundle(foreign, m, ws, ds)
    assert (foreign / "keep.txt").exists()
    out = tmp_path / "ui_data"
    bundle.write_bundle(out, m, ws, ds)
    (out / "vessels" / "stale.json").write_text("{}")
    bundle.write_bundle(out, m, ws, ds)
    assert not (out / "vessels" / "stale.json").exists()
    assert not list(tmp_path.glob(".ui_data_*")) and not (tmp_path / "ui_data.old").exists()


def test_live_export_reports_missing_phases(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PARQUET_DIR", tmp_path / "pq")
    monkeypatch.setattr(config, "WINDOW_FILE", tmp_path / "window.json")
    with pytest.raises(export.NotReady, match="phase 2"):
        export.export_live(tmp_path / "out")


def test_thin_track_keeps_gaps_and_caps_size():
    t = list(range(0, 200_000, 60)) + list(range(300_000, 400_000, 60))
    idx = export.thin_track(t, [0.0] * len(t), [0.0] * len(t), max_points=500)
    assert len(idx) <= 510
    assert t.index(300_000) in idx and idx[0] == 0 and idx[-1] == len(t) - 1
    assert all(b > a for a, b in zip(idx, idx[1:], strict=False))
