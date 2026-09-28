"""Live UI bundle (ADR-18). Filled in after Phase 6; until then it reports what is missing.

Field-by-field source map (the contract in contract.py is fixed; only this file changes when data lands):

  manifest.window_*, cutoffs      config/window.json, config.monthly_cutoffs / supervised_cutoffs   (Phase 0)
  manifest.features               FEATURES registry in features/asof.py, post_hoc flags              (Phase 5a)
  manifest.models                 models scored by the harness                                       (5b, 6)
  watchlist.rows                  reports/flagged_<cutoff>.csv (rank, score, label, designation)     (Phase 6)
  watchlist.rows[].drivers        data/parquet/shap/cutoff=T/ (top-10 per hull)                      (Phase 6)
  watchlist.rows[].b1_stratum     models/rules.py B1 at T                                            (Phase 5b)
  watchlist.rows[].outcome        labels.labels(T, horizon, sources); lead time from the harness     (2, 5b)
  watchlist.metrics               reports/metrics_<label>_<cutoff>.csv, never recomputed here (rule 4) (5b, 6)
  dossier.identity                data/parquet/identity_intervals.parquet                            (Phase 3)
  dossier.track                   ais_dynamic via hull_map, thinned with thin_track(); draught as-of
                                  joined from ais_static                                             (0, 1, 3)
  dossier.events                  gfw_events/ (observed_at = end), detect/ tables, identity changes  (3, 4a, 4b)
  dossier.scores                  harness per-cutoff scores for every model                          (5b, 6)
  dossier.sanctions               data/parquet/sanctions_actions.parquet                             (Phase 2)
  dossier.header                  ais_static modal dimensions; GFW registry build year only          (1, 3)

Registry ownership fields never enter a bundle (ADR-16), same rule as features.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shadowfleet import config


@dataclass(frozen=True)
class Requirement:
    path: Path
    phase: str
    what: str


def requirements() -> list[Requirement]:
    pq, rp = config.PARQUET_DIR, config.REPORTS_DIR
    return [
        Requirement(config.WINDOW_FILE, "0", "window and cutoffs"),
        Requirement(pq / "sanctions_actions.parquet", "2", "designations and outcomes"),
        Requirement(pq / "hull_map.parquet", "3", "hull ids"),
        Requirement(pq / "identity_intervals.parquet", "3", "identity timeline"),
        Requirement(pq / "gfw_events", "4a", "GFW events"),
        Requirement(pq / "feature_matrix", "5a", "features per cutoff"),
        Requirement(pq / "shap", "6", "score drivers"),
        Requirement(rp / "phase6.md", "6", "flagged lists and metrics"),
    ]


def missing() -> list[Requirement]:
    return [r for r in requirements() if not r.path.exists()]


class NotReady(RuntimeError):
    pass


def export_live(out_dir: Path | None = None) -> dict:
    """Build the live bundle. Raises NotReady listing the phases still owed."""
    gaps = missing()
    if gaps:
        lines = [f"  phase {r.phase}: {r.what} ({r.path})" for r in gaps]
        raise NotReady("live UI export needs outputs that do not exist yet:\n" + "\n".join(lines)
                       + "\nuse `make ui-fixtures` to work on the console with synthetic data")
    raise NotImplementedError("live exporter is written after Phase 6 (see module docstring for the field map)")


def thin_track(t: list[int], lon: list[float], lat: list[float], max_points: int = 4000,
               min_gap_s: int = 600) -> list[int]:
    """Indices to keep for display: at most one point per `min_gap_s`, always keeping the first point after a
    gap of more than 1 h (so coverage holes stay visible), then uniform decimation down to `max_points`."""
    keep: list[int] = []
    last = None
    for i, ts in enumerate(t):
        if last is None or ts - last >= min_gap_s:
            keep.append(i)
            last = ts
    if t and keep[-1] != len(t) - 1:
        keep.append(len(t) - 1)
    if len(keep) > max_points:
        step = len(keep) / max_points
        gap_starts = {keep[j] for j in range(1, len(keep)) if t[keep[j]] - t[keep[j - 1]] > 3600}
        sampled = {keep[int(j * step)] for j in range(max_points)}
        keep = sorted(sampled | gap_starts | {keep[-1]})
    return keep
