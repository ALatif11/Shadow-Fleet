"""Write and read a UI bundle (ADR-18). Used by both the synthetic fixtures and the live exporter."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterable
from pathlib import Path

from shadowfleet.ui_export.contract import (
    Dossier,
    Manifest,
    Watchlist,
    file_key,
    json_schemas,
    watchlist_filename,
)


class BundleError(ValueError):
    pass


def _dump(model, path: Path) -> int:
    text = model.model_dump_json(indent=None)
    path.write_text(text, encoding="utf-8")
    return len(text)


def cross_check(manifest: Manifest, watchlists: list[Watchlist], dossiers: list[Dossier]) -> None:
    """Checks that span files; single-file invariants live in the models."""
    vessels = set(manifest.vessels)
    have = {d.hull_id for d in dossiers}
    if vessels != have:
        raise BundleError(f"manifest.vessels and dossiers differ: {sorted(vessels ^ have)[:5]}")
    feats = {f.name for f in manifest.features}
    cutoffs = {c.cutoff for c in manifest.cutoffs}
    seen = set()
    for w in watchlists:
        if w.origin != manifest.origin:
            raise BundleError("mixed origins in one bundle")
        key = (w.cutoff, w.model, w.label_set)
        if key in seen:
            raise BundleError(f"duplicate watchlist {key}")
        seen.add(key)
        if w.cutoff not in cutoffs or w.model not in manifest.models or w.label_set not in manifest.label_sets:
            raise BundleError(f"watchlist {key} not declared in manifest")
        for r in w.rows:
            if r.has_dossier != (r.hull_id in vessels):
                raise BundleError(f"{r.hull_id}: has_dossier disagrees with manifest.vessels")
            unknown = {d.feature for d in r.drivers} - feats
            if unknown:
                raise BundleError(f"{r.hull_id}: drivers not in feature registry: {sorted(unknown)}")
    for d in dossiers:
        if d.origin != manifest.origin:
            raise BundleError("mixed origins in one bundle")
        for s in d.scores:
            if s.cutoff not in cutoffs or s.model not in manifest.models:
                raise BundleError(f"{d.hull_id}: score for undeclared cutoff/model {s.cutoff}/{s.model}")


def _is_bundle_dir(p: Path) -> bool:
    return not p.exists() or (p / "manifest.json").exists() or not any(p.iterdir())


def write_bundle(out_dir: Path, manifest: Manifest, watchlists: Iterable[Watchlist],
                 dossiers: Iterable[Dossier]) -> dict:
    """Validate, write to a temp dir next to `out_dir`, then swap it in, so the UI never sees half a bundle."""
    watchlists, dossiers = list(watchlists), list(dossiers)
    cross_check(manifest, watchlists, dossiers)
    out_dir = Path(out_dir)
    if not _is_bundle_dir(out_dir):
        raise BundleError(f"{out_dir} exists and is not a UI bundle; refusing to replace it")
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".ui_data_", dir=out_dir.parent))
    try:
        (tmp / "watchlist").mkdir()
        (tmp / "vessels").mkdir()
        size = _dump(manifest, tmp / "manifest.json")
        for w in watchlists:
            size += _dump(w, tmp / "watchlist" / watchlist_filename(w.cutoff, w.model, w.label_set))
        for d in dossiers:
            size += _dump(d, tmp / "vessels" / f"{file_key(d.hull_id)}.json")
        old = out_dir.with_name(out_dir.name + ".old")
        if old.exists():
            shutil.rmtree(old)
        if out_dir.exists():
            out_dir.rename(old)
        tmp.rename(out_dir)
        if old.exists():
            shutil.rmtree(old)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"dir": str(out_dir), "origin": manifest.origin, "watchlists": len(watchlists),
            "dossiers": len(dossiers), "bytes": size}


def read_bundle(root: Path) -> tuple[Manifest, list[Watchlist], list[Dossier]]:
    """Load and re-validate a bundle from disk (tests and `make ui-check`)."""
    root = Path(root)
    m = Manifest.model_validate_json((root / "manifest.json").read_text(encoding="utf-8"))
    ws = [Watchlist.model_validate_json(p.read_text(encoding="utf-8"))
          for p in sorted((root / "watchlist").glob("*.json"))]
    ds = [Dossier.model_validate_json(p.read_text(encoding="utf-8"))
          for p in sorted((root / "vessels").glob("*.json"))]
    cross_check(m, ws, ds)
    return m, ws, ds


def write_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, schema in json_schemas().items():
        p = out_dir / f"{name}.schema.json"
        p.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        paths.append(p)
    return paths
