"""Phase 5a leakage suite. `make backtest` runs this first and refuses to report metrics if it fails.

(a) and (b) run here. (c) permutation, (d) reverse-time and (e) entity-resolution sensitivity need a
fitted model, so they are hooks the Phase 5b harness calls; the two that can be tested without one are
tested for real, not stubbed.
"""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

import pytest

from shadowfleet import config
from shadowfleet.features import asof
from shadowfleet.ingest import dma
from shadowfleet.resolve import identity
from tests.conftest import DAY, HEADER_V1, row, write_zip

SMALL = {"window_days": 2, "min_imo_days": 2}
FEATURES_DIR = Path(__file__).resolve().parent.parent / "shadowfleet" / "features"


# ---------------------------------------------------------------- (b) static analysis, no data needed
def test_no_feature_module_imports_the_label_machinery_except_listed_as_of():
    """PREREG test 2. A feature may ask "was this partner listed at T"; it may not read a label."""
    allowed = {"listed_as_of", "labels", "ACTIONS_FILE"}  # asof imports the module, so names are checked
    for path in FEATURES_DIR.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "labels" in node.module:
                for alias in node.names:
                    assert alias.name in allowed, f"{path.name} imports {alias.name} from labels"
        src = path.read_text()
        for banned in ("first_add_in_window", "positives_table"):
            assert banned not in src, f"{path.name} reaches into label construction: {banned}"


def test_no_feature_name_mentions_sanctions_except_the_partner_feature():
    for name in asof.FEATURES:
        assert "sanction" not in name or name == "n_encounters_with_sanctioned_partner", name


def test_no_gfw_registry_ownership_field_is_read_anywhere_under_features():
    for path in FEATURES_DIR.glob("*.py"):
        src = path.read_text()
        for field in config.GFW_FORBIDDEN_FEATURE_FIELDS:
            assert field not in src, f"{path.name} reads GFW registry field {field}"


def test_registry_families_equal_the_prereg_list():
    """PREREG section 7 freezes the families. If this fails, either PREREG or the registry moved."""
    prereg = {"identity", "ais", "gfw_gaps", "gfw_encounters", "gfw_ports", "detect", "static"}
    assert {f for f, _ in asof.FEATURES.values()} == prereg
    text = (Path(__file__).resolve().parent.parent / "PREREG.md").read_text()
    for family in prereg:
        assert f"`{family}`" in text, f"{family} is not named in PREREG.md"


def test_every_feature_has_a_family_and_a_description():
    for name, (family, desc) in asof.FEATURES.items():
        assert family in asof.SOURCE_OF_FAMILY, name
        assert len(desc) > 15, f"{name} needs a real description for the brief bundler"


# ---------------------------------------------------------------- (a) truncation equality, needs data
@pytest.fixture()
def store(tmp_data):
    """Nine days of two hulls, one of which changes name, so features are not all zero."""
    config.DMA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    for off in range(9):
        d = (DAY + timedelta(days=off)).date()
        t = (DAY + timedelta(days=off)).replace(hour=9)
        rows = []
        for mmsi, imo, name in ((219000111, "9074729", "ALPHA" if off < 5 else "BRAVO"),
                                (219000222, "9176187", "CHARLIE")):
            rows += [row(t + timedelta(minutes=k), "Class A", mmsi, 57.0 + k * 0.001, 10.0,
                         sog=9.0, name=name, imo=imo) for k in range(15)]
        z = write_zip(config.DMA_RAW_DIR / f"{d}.zip", rows, HEADER_V1)
        assert not dma.ingest_zip(z, [d], z.name).days_failed
    con = dma.connect()
    identity.hull_map(con, **SMALL)
    identity.identity_intervals(con)
    return con


def test_features_at_T_ignore_everything_after_T(store):
    """PREREG test 1, the one that matters: the live store and a store physically cut at T must agree."""
    T = (DAY + timedelta(days=5)).date()
    live = asof.features(T, store)
    assert live, "fixture produced no population"

    for part in config.PARQUET_DIR.glob("*/dt=*"):
        if part.name > f"dt={T}":
            for f in part.glob("*"):
                f.unlink()
            part.rmdir()
    con2 = dma.connect()
    identity.hull_map(con2, **SMALL)
    identity.identity_intervals(con2)
    assert asof.features(T, con2) == live


def test_a_hull_listed_before_T_is_not_in_the_population(store):
    """CLAUDE.md rule 3, through the real sanctions_actions table rather than a patched function."""
    from shadowfleet.labels import labels as lab

    T = (DAY + timedelta(days=8)).date()
    assert "9074729" in {r["hull_id"] for r in asof.features(T, store)}
    lab.write_actions([{"source": "OFAC", "action": "add", "date": (DAY + timedelta(days=2)).date(),
                        "imo": 9074729, "name": "ALPHA", "program": "RUSSIA-EO14024",
                        "via": "test", "raw": ""}])
    assert "9074729" not in {r["hull_id"] for r in asof.features(T, store)}
    assert "9176187" in {r["hull_id"] for r in asof.features(T, store)}  # the unlisted hull stays


def test_every_row_carries_every_registered_feature(store):
    T = (DAY + timedelta(days=8)).date()
    for r in asof.features(T, store):
        assert set(r) == {"hull_id", "cutoff", *asof.FEATURES}
        assert r["n_days_observed"] > 0 and r["n_transits"] >= 1


def test_features_cli_writes_a_matrix_and_the_report(store, tmp_path):
    T = (DAY + timedelta(days=8)).date()
    out = asof.build([T])
    assert out["cutoffs"][0]["hulls"] == 2 and out["gfw_present"] is False
    part = config.PARQUET_DIR / "feature_matrix" / f"cutoff={T}" / "part-0.parquet"
    assert part.exists()
    from shadowfleet.util import report

    text = report.render_phase5a(out)
    assert "Phase 5a report" in text and "`gfw_gaps`" in text
    assert "never faked" in text  # the GFW-absent disclosure has to survive refactors
