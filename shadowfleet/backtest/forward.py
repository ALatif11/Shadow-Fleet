"""Phase F: a prospective, tamper-evident prediction (ADR-17).

`score` writes a ranked top-50 and its SHA-256, and the git commit timestamp is the evidence that the list
existed before the outcomes did. `evaluate` reads that file and never writes to it: it recomputes the hash
first and refuses to report anything if the list has changed, because a forward test whose list can be
edited after the fact is just a backtest with extra steps.

The committed file carries feature NAMES but no GFW-derived values, so it can live in a public repo
(CLAUDE.md rule 7).
"""

from __future__ import annotations

import csv
import hashlib
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from shadowfleet import config
from shadowfleet.backtest import explain, harness, metrics
from shadowfleet.features import asof
from shadowfleet.features.asof import FEATURES
from shadowfleet.ingest.dma import connect
from shadowfleet.labels import labels as lab
from shadowfleet.models import rules, tabular
from shadowfleet.util.store import rel_path

TOP_K = 50
DIR = "forward"
GFW_FAMILIES = {"gfw_gaps", "gfw_encounters", "gfw_ports"}


def _dir() -> Path:
    d = config.REPORTS_DIR / DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _drivers(hull: str, shap: dict[str, list[dict]]) -> list[str]:
    """Feature names only. A value would leak a GFW-derived number into a public file."""

    return [d["feature"] for d in (shap.get(hull) or [])
            if FEATURES.get(d["feature"], ("", ""))[0] not in GFW_FAMILIES][:5]


def score(T: date | None = None, top_k: int = TOP_K, model: str | None = None) -> dict:
    """Score the population at T with the frozen primary model, or with B2 if no model is fitted yet.

    ADR-17: if Phase 6 is not finished by the scoring date, the list is still committed but labelled as the
    rules baseline, because a dated prediction from a weaker model is worth more than a later one from a
    better model.
    """


    T = T or date.fromisoformat(config.FORWARD_TEST_SCORING_DATE)
    if T < date.fromisoformat(config.FORWARD_TEST_SCORING_DATE):
        raise SystemExit(f"ADR-17 fixes the earliest scoring date at "
                         f"{config.FORWARD_TEST_SCORING_DATE}; {T} is before it")
    con = connect()
    rows = asof.features(T, con)
    if not rows:
        raise SystemExit(f"no population at {T}; ingest through {T} first")

    history = [(t, r, y) for t, r, y in _history_for(T, con)]
    usable = harness.usable_history(history, T)
    scores, used = None, model
    if usable and model != "B2_weighted":
        train_rows = [r for h, _ in usable for r in h]
        train_y = np.concatenate([y for _, y in usable])
        scores, _, _ = tabular.train_and_score(train_rows, train_y, rows)
        used = "LGBM" if scores is not None else None
    if scores is None:
        scores, used = rules.b2_weighted(rows), "B2_weighted (rules baseline; no fitted model at scoring)"

    shap = explain.read_shap(T)
    order = np.argsort(-scores)[:top_k]
    out_path = _dir() / f"top50_{T.isoformat()}.csv"
    with open(out_path, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["rank", "hull_id", "imo", "score", *[f"driver_{i + 1}" for i in range(5)]])
        for rank, i in enumerate(order, start=1):
            hull = rows[i]["hull_id"]
            drivers = _drivers(hull, shap)
            wr.writerow([rank, hull, hull if hull.isdigit() else "", round(float(scores[i]), 6),
                         *drivers, *[""] * (5 - len(drivers))])
    digest = sha256(out_path)
    _append_manifest(T, out_path, digest, used, len(rows))
    return {"cutoff": T.isoformat(), "model": used, "population": len(rows), "top_k": len(order),
            "file": rel_path(out_path), "sha256": digest,
            "next": "commit and push today; the commit timestamp is the evidence (ADR-17)"}


def _history_for(T: date, con) -> list[tuple[date, list[dict], object]]:
    """Past cutoffs with their labels, for training the forward model. Only closed horizons are used, and
    `usable_history` enforces that, so this may safely return everything it can build."""


    out = []
    for t in config.monthly_cutoffs(config.load_window(), T):
        if t >= T:
            continue
        rows = asof.features(t, con)
        if not rows:
            continue
        imos = [int(r["hull_id"]) for r in rows if r["hull_id"].isdigit()]
        by_imo = {r["imo"]: r["label"] for r in lab.labels(t, imos)}
        y = np.array([by_imo.get(int(r["hull_id"]), 0) if r["hull_id"].isdigit() else 0 for r in rows])
        out.append((t, rows, y))
    return out


def _append_manifest(T: date, path: Path, digest: str, model: str, population: int) -> None:
    """Append-only: a manifest that can be rewritten proves nothing."""
    manifest = _dir() / "README.md"
    if not manifest.exists():
        manifest.write_text(
            "# Forward test (Phase F, ADR-17)\n\n"
            "Each row is a ranked top-50 committed before any of its outcomes were known. The SHA-256 is "
            "recorded here and the git commit timestamp is the evidence. Nothing in this directory is ever "
            "edited; `make forward-eval` is read-only and refuses to run if a hash no longer matches.\n\n"
            "| scored at | file | model | population | sha256 |\n|---|---|---|---:|---|\n")
    with open(manifest, "a") as f:
        f.write(f"| {T.isoformat()} | `{path.name}` | {model} | {population} | `{digest}` |\n")


def evaluate(T: date, as_of: date | None = None, ks: tuple[int, ...] = config.TOP_K) -> dict:
    """Score the committed list against designations known by `as_of`. Read-only, hash-checked."""


    as_of = as_of or date.today()
    path = _dir() / f"top50_{T.isoformat()}.csv"
    if not path.exists():
        return {"skipped": f"{rel_path(path)} missing; run `make forward-score` first"}
    digest = sha256(path)
    recorded = _recorded_hashes().get(path.name)
    if recorded is None:
        # fail closed: a list with no recorded hash means the manifest was edited or lost, which is the
        # same evidential problem as an edited list
        raise SystemExit(f"{path.name} exists but {DIR}/README.md records no hash for it. The forward "
                         "test's evidence is the hash plus the commit date; without it there is nothing "
                         "to report.")
    if recorded != digest:
        raise SystemExit(f"{path.name} no longer matches the hash committed in {DIR}/README.md. "
                         "The forward test is void; do not report metrics from an edited list.")
    with open(path) as f:
        listed = list(csv.DictReader(f))
    horizon_end = min(T + timedelta(days=config.HORIZON_DAYS), as_of)
    positives = lab.first_add_in_window(T, horizon_end)
    y = np.array([1 if (r["imo"] and int(r["imo"]) in positives) else 0 for r in listed])
    score = np.array([float(r["score"]) for r in listed])
    hits = [{"rank": int(r["rank"]), "hull_id": r["hull_id"],
             "designation_date": positives[int(r["imo"])].date.isoformat(),
             "lead_days": (positives[int(r["imo"])].date - T).days}
            for r in listed if r["imo"] and int(r["imo"]) in positives]
    out = {"cutoff": T.isoformat(), "evaluated_as_of": as_of.isoformat(),
           "horizon_end": horizon_end.isoformat(), "listed": len(listed),
           "designated_so_far": int(y.sum()), "hits": hits, "sha256_ok": True,
           "median_lead_days": int(np.median([h["lead_days"] for h in hits])) if hits else None}
    for k in ks:
        out[f"precision_at_{k}"] = metrics.precision_at_k(y, score, k)
    out["note"] = ("right-censored: the horizon closes "
                   f"{(T + timedelta(days=config.HORIZON_DAYS)).isoformat()}, so recall is a floor "
                   "until then")
    return out


HEX64 = set("0123456789abcdef")


def _recorded_hashes() -> dict[str, str]:
    """Filename -> sha256 from the manifest, found by shape rather than by column position.

    Indexing the columns was how this check first failed OPEN: the parse returned an empty string, the
    comparison was skipped, and an edited list passed evaluation. A tamper check that can silently find
    nothing is worse than no check, so this matches on what a hash looks like.
    """
    manifest = _dir() / "README.md"
    if not manifest.exists():
        return {}
    out = {}
    for line in manifest.read_text().splitlines():
        cells = [c.strip().strip("`") for c in line.split("|")]
        name = next((c for c in cells if c.endswith(".csv")), None)
        digest = next((c for c in cells if len(c) == 64 and set(c) <= HEX64), None)
        if name and digest:
            out[name] = digest
    return out
