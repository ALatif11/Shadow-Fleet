"""Backend side of the UI contract (ADR-18).

The UI is generated from `shadowfleet/ui_export/contract.py` and reads only exported files. What breaks
silently is a rename here: Phase 3 writing `hulls.parquet` while export.py still maps `hull_map.parquet`.
These tests fail the moment the two drift. They skip on branches without the ui_export package.
"""

from __future__ import annotations

import subprocess

import pytest

from shadowfleet import config

pytest.importorskip("shadowfleet.ui_export.export", reason="ui_export lives on the ui-shell branch")


def _repo_mentions(name: str) -> bool:
    """Does any tracked .py or .md file outside ui_export name this output?"""
    out = subprocess.run(["git", "grep", "-l", "--", name, "--", "*.py", "*.md"],
                         cwd=config.REPO_ROOT, capture_output=True, text=True).stdout
    return any(line and not line.startswith("shadowfleet/ui_export/") for line in out.splitlines())


def test_every_ui_requirement_is_produced_somewhere_in_the_pipeline():
    from shadowfleet.ui_export import export

    unclaimed = [r.path.name for r in export.requirements() if not _repo_mentions(r.path.name)]
    assert not unclaimed, (
        f"ui_export/export.py expects {unclaimed}, which no phase code or prompt mentions: "
        "either the output was renamed or the field map is stale")


def test_requirements_stay_inside_the_exported_surface():
    from shadowfleet.ui_export import export

    roots = (config.PARQUET_DIR, config.REPORTS_DIR, config.WINDOW_FILE.parent)
    assert all(any(r.path.is_relative_to(root) for root in roots) for r in export.requirements())


def test_phase1_outputs_are_named_in_the_field_map():
    """Phase 1 writes the dossier's track and header inputs; the map must still name them."""
    from shadowfleet.ui_export import export

    doc = export.__doc__ or ""
    for table in ("ais_dynamic", "ais_static", "identity_intervals"):
        assert table in doc, f"{table} feeds a dossier field but is missing from the source map"
