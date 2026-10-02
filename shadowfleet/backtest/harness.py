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
from shadowfleet.backtest import drift, explain, leakage, metrics
from shadowfleet.features import asof
from shadowfleet.labels import labels as lab
from shadowfleet.models import anomaly, rules, tabular
from shadowfleet.util import probes, report
from shadowfleet.util.store import rel_path

log = logging.getLogger(__name__)

LABEL_SETS = {"union": lab.SOURCES, "ofac_only": ("OFAC",)}
LEAD_TIME_K = 50  # the operating point PREREG section 3 fixes for the primary endpoint
PRIMARY_LABEL_SET = "union"  # PREREG section 3; the Phase 6 artefacts follow the primary endpoint only


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


def _scores(T: date, rows: list[dict], history: list[tuple[date, list[dict], np.ndarray]],
            supervised: bool = True) -> tuple[dict[str, np.ndarray | None], dict]:
    """Every model's score at T, plus the extras the Phase 6 artefacts need (boosters, columns, training)."""
    out: dict[str, np.ndarray | None] = {name: fn(rows) for name, fn in rules.RULES.items()}
    out["ISO_forest"] = anomaly.score(rows)  # unsupervised, so available at every cutoff
    usable = usable_history(history, T)
    train_rows = [r for h, _ in usable for r in h]
    train_y = np.concatenate([y for _, y in usable]) if usable else np.array([])
    extra: dict = {"train_rows": train_rows, "train_y": train_y}
    if not (usable and supervised):
        out["B3_logistic"] = None
        out["LGBM"] = None
        return out, extra
    out["B3_logistic"] = rules.b3_logistic(train_rows, train_y, rows)
    score, boosters, cols = tabular.train_and_score(train_rows, train_y, rows,
                                                    explain.numeric_columns())
    out["LGBM"] = score
    extra.update(boosters=boosters, columns=cols)
    return out, extra


def run(cutoffs: list[date] | None = None, label_sets: dict | None = None, full: bool = False) -> dict:
    """Score every cutoff for every label set. Returns the whole table; also writes CSVs and the report.

    `full` adds the Phase 6 passes: ablation arms, PSI drift, SHAP tables, flagged lists and the
    false-positive review sheets. They are separate because they cost several times the base loop.
    """
    w = config.load_window()
    cutoffs = cutoffs or config.monthly_cutoffs(w, date.today())
    label_sets = label_sets or LABEL_SETS
    con = None
    per_cutoff: list[dict] = []
    first_seen: dict[str, date] = {}
    history: dict[str, list[tuple[date, list[dict], np.ndarray]]] = {k: [] for k in label_sets}
    b2_terms: dict[str, int] = {}
    artefacts: list[dict] = []
    # for the event-study lead time: which hulls each model ranked in the top k at each cutoff
    top_k_seen: dict[str, dict[date, set[str]]] = {}

    label_history: dict[str, list[tuple[date, list[dict], np.ndarray]]] = {}

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
            scored, extra = _scores(T, rows, history[label_name])
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
            if label_name == PRIMARY_LABEL_SET:
                for model, score in scored.items():
                    if score is None:
                        continue
                    top = np.argsort(-score)[:LEAD_TIME_K]
                    top_k_seen.setdefault(model, {})[T] = {rows[i]["hull_id"] for i in top}
            if full and label_name == PRIMARY_LABEL_SET:
                artefacts.append(_phase6(T, rows, y, scored, extra, per_cutoff))

            # every cutoff joins the history; `usable_history` decides which of them a later cutoff may
            # actually train on, because that depends on the cutoff being scored, not on the run's end
            history[label_name].append((T, rows, y))
            label_history.setdefault(label_name, []).append((T, rows, y))

    union = label_history.get("union") or label_history.get(next(iter(label_sets)))
    leak = leakage.run_all(union or [])
    failed = [k for k, v in leak.items() if v.get("passes") is False]
    if failed:
        # PREREG section 10: no metric is reported while a leakage test fails. Only the leakage section is
        # written, and the per-cutoff CSV of an earlier run is removed so nothing stale can be quoted. Until
        # Oct 2 2026 this branch only logged and the metrics were written anyway (disclosed in phase5b.md).
        log.error("leakage tests failed: %s", failed)
        (config.REPORTS_DIR / "metrics_by_cutoff.csv").unlink(missing_ok=True)
        blocked = {"leakage": leak, "leakage_failed": failed, "cutoffs_scored": len(cutoffs)}
        probes.write("backtest", blocked)
        report.write_report("phase5b", blocked)
        raise SystemExit(f"leakage tests failed: {failed}; no metrics reported (PREREG section 10)")
    out = {"leakage": leak, "leakage_failed": failed,
           "cutoffs_scored": len({r["cutoff"] for r in per_cutoff}), "rows": len(per_cutoff),
           "b2_live_terms": b2_terms, "dead_b2_terms": [k for k, v in b2_terms.items() if v == 0],
           "not_scored": sum(1 for r in per_cutoff if r.get("not_scored")),
           "per_cutoff": per_cutoff, "aggregate": _aggregate(per_cutoff),
           "aggregate_matched": _aggregate(_matched(per_cutoff))}
    if full:
        out["phase6"] = {"per_cutoff": artefacts, "arms": sorted(explain.ablation_arms()),
                         "top_flagged": explain.TOP_FLAGGED,
                         "fp_review": _fp_sheets(artefacts),
                         "drift_by_side": _drift_sides(artefacts)}
    out["lead_time"] = lead_time(top_k_seen, _designations_in_window(cutoffs))
    out["csv"] = _write_csv(per_cutoff)
    probes.write("backtest", out)
    report.write_report("phase5b", out)
    if full:
        report.write_report("phase6", out)
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


def _matched(per_cutoff: list[dict]) -> list[dict]:
    """Rows from the cutoffs at which EVERY model was scored. The supervised models need a closed training
    horizon, so they skip the earliest cutoffs, which have the highest base rates; comparing their macro
    average with a baseline's over all cutoffs flatters the baseline. Added Oct 2 2026, after results."""
    skipped = {(r["cutoff"], r["label_set"]) for r in per_cutoff if r.get("not_scored")}
    return [r for r in per_cutoff if (r["cutoff"], r["label_set"]) not in skipped]


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


# ------------------------------------------------------------------------------------- Phase 6 passes
def _phase6(T: date, rows: list[dict], y: np.ndarray, scored: dict, extra: dict,
            per_cutoff: list[dict]) -> dict:
    """Ablations, drift, SHAP, flagged list. Only for the primary label set (PREREG section 3)."""
    out: dict = {"cutoff": T.isoformat(), "hormuz_side": drift.hormuz_side(T.isoformat())}
    train_rows, train_y = extra["train_rows"], extra["train_y"]

    if len(train_y) and train_y.sum():
        arms = {}
        for arm, cols in explain.ablation_arms().items():
            score, _, _ = tabular.train_and_score(train_rows, train_y, rows, cols)
            arms[arm] = ({"pr_auc": None, "precision_at_50": None} if score is None else
                         {"pr_auc": metrics.pr_auc(y, score),
                          "precision_at_50": metrics.precision_at_k(y, score, 50)})
        out["ablations"] = arms
        out["drift_psi"] = drift.by_family(train_rows, rows, asof.FEATURES)
        # an arm whose columns are all zero produces the base rate, which is not the same as "this source
        # adds nothing"; the report has to be able to tell those two apart
        gfw_cols = explain.numeric_columns({f for f, src in explain.SOURCE_OF_FAMILY.items()
                                            if src == "gfw"})
        out["gfw_features_all_zero"] = not any(r.get(c) for r in rows for c in gfw_cols)

    if scored.get("LGBM") is not None and extra.get("boosters"):
        # trees kept by early stopping, per seed: 1 means the model is a constant in disguise
        out["lgbm_rounds"] = [b.best_iteration or b.current_iteration() for b in extra["boosters"]]
        contribs = tabular.contributions(extra["boosters"], rows, extra["columns"])
        out["shap"] = explain.write_shap(T, contribs)
        out["flagged"] = explain.write_flagged(T, rows, scored["LGBM"], y, contribs)
        order = np.argsort(-scored["LGBM"])
        out["top_non_listed"] = [
            {"cutoff": T.isoformat(), "rank": rank, "hull_id": rows[i]["hull_id"],
             "score": round(float(scored["LGBM"][i]), 6),
             "top_drivers": "; ".join(d["feature"] for d in
                                      (next((c for c in contribs if c["hull_id"] == rows[i]["hull_id"]),
                                            {}) or {}).get("top", [])[:3])}
            for rank, i in enumerate(order, start=1) if y[i] == 0][:explain.FP_REVIEW_N]
    # calibration: Brier is only meaningful for the probability models, and metrics.summary already carries
    # it for those; nothing extra to compute here, which is why there is no reliability-plot writer yet
    out["primary_model_scored"] = scored.get("LGBM") is not None
    return out


def _fp_sheets(artefacts: list[dict]) -> list[str]:
    """One review sheet per quarter, from the top non-listed flags of that quarter's cutoffs."""
    by_quarter: dict[str, list[dict]] = {}
    for a in artefacts:
        q = explain.quarter_of(date.fromisoformat(a["cutoff"]))
        by_quarter.setdefault(q, []).extend(a.get("top_non_listed") or [])
    return [explain.write_fp_review(q, rows) for q, rows in sorted(by_quarter.items()) if rows]


def _drift_sides(artefacts: list[dict]) -> dict:
    """Mean PSI per family, split at the pre-registered Hormuz break (PREREG section 8)."""
    out: dict = {}
    for side in ("pre_break", "post_break"):
        rows = [a["drift_psi"] for a in artefacts if a.get("drift_psi") and a["hormuz_side"] == side]
        families = sorted({f for r in rows for f in r})
        out[side] = {"cutoffs": len(rows),
                     "psi": {f: round(float(np.mean([r[f] for r in rows if r.get(f) is not None])), 4)
                             for f in families if any(r.get(f) is not None for r in rows)}}
    return out


# ------------------------------------------------------------------------------- event-study lead time
def lead_time(top_k_by_cutoff: dict[str, dict[date, set[str]]],
              designations: dict[str, date]) -> dict:
    """Weeks between a hull's designation and the EARLIEST cutoff at which it entered the top k.

    Event-study, not per-cutoff (PREREG section 5 and ADR-11): asking "how early did we first flag this
    hull" is the question an analyst has; averaging per-cutoff distances instead mostly measures how far
    each cutoff sat from the next designation wave.

    Right-censored by construction: a hull flagged at the first cutoff cannot show a longer lead than the
    window allows, and one designated after the last horizon is not here at all. Both are reported.
    """
    out: dict = {}
    for model, by_cutoff in top_k_by_cutoff.items():
        leads = []
        for hull, designated in designations.items():
            seen = sorted(T for T, hulls in by_cutoff.items() if hull in hulls and T < designated)
            if seen:
                leads.append({"hull_id": hull, "first_flagged": seen[0].isoformat(),
                              "designated": designated.isoformat(),
                              "weeks": round((designated - seen[0]).days / 7, 1)})
        weeks = sorted(x["weeks"] for x in leads)
        out[model] = {
            "flagged_before_designation": len(leads),
            "designated_in_window": len(designations),
            "median_weeks": weeks[len(weeks) // 2] if weeks else None,
            "min_weeks": weeks[0] if weeks else None, "max_weeks": weeks[-1] if weeks else None,
            "examples": sorted(leads, key=lambda x: -x["weeks"])[:5],
        }
    return out


def _designations_in_window(cutoffs: list[date]) -> dict[str, date]:
    """hull_id -> first designation date, for designations inside the scored span. Keyed on hull_id so it
    joins to the top-k sets directly; a `syn:` hull has no IMO and so can never appear here."""
    if not cutoffs:
        return {}
    first, last = min(cutoffs), max(cutoffs) + timedelta(days=config.HORIZON_DAYS)
    return {str(imo): listing.date for imo, listing in lab.first_add_in_window(first, last).items()}
