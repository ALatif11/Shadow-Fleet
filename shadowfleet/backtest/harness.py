"""Phase 5b: score every cutoff with every baseline and write the metrics tables.

Expanding window: a supervised model at T trains only on cutoffs whose horizon had already closed by T, so
the labels it learns from were fully observable at T. Early cutoffs get rules only, and the tables say
"not scored" rather than carrying a number that came from nothing.
"""

from __future__ import annotations

import csv
import logging
from datetime import date, timedelta

import numpy as np

from shadowfleet import config
from shadowfleet.backtest import metrics
from shadowfleet.features import asof
from shadowfleet.labels import labels as lab
from shadowfleet.models import rules
from shadowfleet.util.store import rel_path

log = logging.getLogger(__name__)

LABEL_SETS = {"union": lab.SOURCES, "ofac_only": ("OFAC",)}


def _label_vector(T: date, rows: list[dict], sources: tuple[str, ...]) -> np.ndarray:
    """1 if the hull's IMO is first listed by `sources` in (T, T+horizon].

    A `syn:` hull has no IMO, so it can never be a positive. That is a real recall ceiling, not a bug, and
    the report counts those hulls so the ceiling is visible.
    """
    imos = [int(r["hull_id"]) for r in rows if r["hull_id"].isdigit()]
    by_imo = {r["imo"]: r["label"] for r in lab.labels(T, imos, sources)}
    return np.array([by_imo.get(int(r["hull_id"]), 0) if r["hull_id"].isdigit() else 0 for r in rows])


def usable_history(history: list[tuple[date, list[dict], np.ndarray]], T: date,
                   horizon_days: int = config.HORIZON_DAYS) -> list[tuple[list[dict], np.ndarray]]:
    """The cutoffs a supervised model at T may train on: those whose horizon had closed by T.

    Gating this against the END of the run instead of against T is a leak, and a quiet one, because every
    metric still looks plausible. T' + horizon <= T is the whole rule.
    """
    return [(rows, y) for t0, rows, y in history if t0 + timedelta(days=horizon_days) <= T]


def _scores(T: date, rows: list[dict],
            history: list[tuple[date, list[dict], np.ndarray]]) -> dict[str, np.ndarray | None]:
    out: dict[str, np.ndarray | None] = {name: fn(rows) for name, fn in rules.RULES.items()}
    usable = usable_history(history, T)
    train_rows = [r for h, _ in usable for r in h]
    train_y = np.concatenate([y for _, y in usable]) if usable else np.array([])
    out["B3_logistic"] = rules.b3_logistic(train_rows, train_y, rows) if usable else None
    return out


def run(cutoffs: list[date] | None = None, label_sets: dict | None = None) -> dict:
    """Score every cutoff for every label set. Returns the whole table; also writes CSVs and the report."""
    w = config.load_window()
    cutoffs = cutoffs or config.monthly_cutoffs(w, date.today())
    label_sets = label_sets or LABEL_SETS
    con = None
    per_cutoff: list[dict] = []
    first_seen: dict[str, date] = {}
    history: dict[str, list[tuple[date, list[dict], np.ndarray]]] = {k: [] for k in label_sets}
    b2_terms: dict[str, int] = {}

    for T in cutoffs:
        rows = asof.features(T, con)
        if not rows:
            log.warning("no population at %s", T)
            continue
        b2_terms = rules.b2_live_terms(rows)
        b1 = rules.b1_russia_port(rows).astype(bool)
        for h in rows:
            first_seen.setdefault(h["hull_id"], T)
        firsts = np.array([first_seen[r["hull_id"]] == T for r in rows])
        n_syn = sum(1 for r in rows if not r["hull_id"].isdigit())

        for label_name, sources in label_sets.items():
            y = _label_vector(T, rows, sources)
            scored = _scores(T, rows, history[label_name])
            for model, score in scored.items():
                base = {"cutoff": T.isoformat(), "label_set": label_name, "model": model,
                        "hulls_without_imo": n_syn}
                if score is None:
                    per_cutoff.append({**base, "stratum": "all", "not_scored": True})
                    continue
                for stratum, mask in (("all", np.ones(len(rows), bool)), ("b1", b1), ("first_seen", firsts)):
                    if mask.sum() == 0:
                        continue
                    per_cutoff.append({**base, "stratum": stratum, "not_scored": False,
                                       **metrics.summary(y[mask], score[mask],
                                                         calibrated=model == "B3_logistic")})
            # every cutoff joins the history; `usable_history` decides which of them a later cutoff may
            # actually train on, because that depends on the cutoff being scored, not on the run's end
            history[label_name].append((T, rows, y))

    out = {"cutoffs_scored": len({r["cutoff"] for r in per_cutoff}), "rows": len(per_cutoff),
           "b2_live_terms": b2_terms, "dead_b2_terms": [k for k, v in b2_terms.items() if v == 0],
           "not_scored": sum(1 for r in per_cutoff if r.get("not_scored")),
           "per_cutoff": per_cutoff, "aggregate": _aggregate(per_cutoff)}
    out["csv"] = _write_csv(per_cutoff)
    from shadowfleet.util import probes, report

    probes.write("backtest", out)
    report.write_phase5b(out)
    return out


def _aggregate(per_cutoff: list[dict]) -> list[dict]:
    """Macro-average over cutoffs, per (label set, model, stratum). Macro, not pooled: a cutoff with more
    hulls must not dominate, and PREREG section 3 fixes the primary endpoint as macro-averaged."""
    keys = sorted({(r["label_set"], r["model"], r.get("stratum", "all")) for r in per_cutoff
                   if not r.get("not_scored")})
    out = []
    for label_set, model, stratum in keys:
        rows = [r for r in per_cutoff if not r.get("not_scored") and r["label_set"] == label_set
                and r["model"] == model and r.get("stratum") == stratum]
        agg = {"label_set": label_set, "model": model, "stratum": stratum, "cutoffs": len(rows)}
        for metric in ("pr_auc", "precision_at_25", "precision_at_50", "precision_at_100",
                       "recall_at_50", "fpr_at_50"):
            vals = [r[metric] for r in rows if r.get(metric) is not None]
            agg[metric] = round(float(np.mean(vals)), 4) if vals else None
        agg["cutoffs_with_a_positive"] = sum(1 for r in rows if (r.get("n_positive") or 0) > 0)
        out.append(agg)
    return out


def _write_csv(per_cutoff: list[dict]) -> str:
    d = config.REPORTS_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / "metrics_by_cutoff.csv"
    fields = sorted({k for r in per_cutoff for k in r})
    with open(path, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=fields)
        wr.writeheader()
        wr.writerows(per_cutoff)
    return rel_path(path)
