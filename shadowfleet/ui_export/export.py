"""Live UI bundle (ADR-18). Filled in after Phase 6; until then it reports what is missing.

Field-by-field source map (the contract in contract.py is fixed; only this file changes when data lands):

  manifest.window_*, cutoffs      config/window.json, config.monthly_cutoffs / supervised_cutoffs   (Phase 0)
  manifest.features               FEATURES registry in features/asof.py, post_hoc flags              (Phase 5a)
  manifest.models                 models scored by the harness                                       (5b, 6)
  watchlist.rows                  data/parquet/scores/cutoff=T/ (every model, every hull, B1 flag)    (Phase 6)
  watchlist.rows[].drivers        data/parquet/shap/cutoff=T/ (top-10 per hull)                      (Phase 6)
  watchlist.rows[].b1_stratum     the `b1` column of the scores table (models/rules.py B1 at T)      (Phase 6)
  watchlist.rows[].outcome        labels.labels(T, ...); lead time from the backtest probe, by hull   (2, 5b)
  watchlist.metrics               reports/metrics_by_cutoff.csv, copied, never recomputed (rule 4)   (5b, 6)
  dossier.identity                data/parquet/identity_intervals.parquet                            (Phase 3)
  dossier.track                   ais_dynamic via hull_map, thinned with thin_track(); draught as-of
                                  joined from ais_static                                             (0, 1, 3)
  dossier.events                  gfw_events/ (observed_at = end), detect/ tables, identity changes  (3, 4a, 4b)
  dossier.scores                  harness per-cutoff scores for every model                          (5b, 6)
  dossier.sanctions               data/parquet/sanctions_actions.parquet                             (Phase 2)
  dossier.header                  length from the latest feature row; dwt and build year have no
                                  dated source, so they stay null                                    (5a)

Registry ownership fields never enter a bundle (ADR-16), same rule as features.

Scope of the live bundle, stated in its notes: one label set (OFAC+EU+UK, the PREREG headline), because the
supervised models were only scored under it; dossiers map transmitters to hulls as of the window's end.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from shadowfleet import config
from shadowfleet.backtest import explain
from shadowfleet.features.asof import FEATURES, SOURCE_OF_FAMILY
from shadowfleet.ingest.dma import connect
from shadowfleet.labels import labels as lab
from shadowfleet.resolve.identity import INTERVALS, hull_at
from shadowfleet.ui_export import bundle as ubundle
from shadowfleet.ui_export.contract import (
    CutoffInfo,
    Dossier,
    Driver,
    Event,
    FeatureDef,
    IdentityInterval,
    Manifest,
    Metrics,
    Outcome,
    Overlay,
    SanctionAction,
    ScorePoint,
    Track,
    VesselHeader,
    Watchlist,
    WatchlistRow,
)
from shadowfleet.util import probes
from shadowfleet.util.store import glob_table, has_table

# harness name -> console name (ui/src/lib/format.ts labels these). B0 random is left out: it is a metric
# floor in the reports, not a list anyone would read.
MODELS = {"LGBM": "lightgbm", "ISO_forest": "isoforest", "B3_logistic": "logreg", "B2_weighted": "b2_rules",
          "B1_russia_port": "b1_russia_port"}
LABEL_SET = "ofac_eu_uk"  # harness "union"
TOP_N = 100
FAMILY = {"gfw_gaps": "gfw", "gfw_encounters": "gfw", "gfw_ports": "gfw"}
GFW_TYPES = {"gap", "encounter", "loitering", "port_visit"}
ISO3 = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")


@dataclass(frozen=True)
class Requirement:
    path: Path
    phase: str
    what: str


def requirements() -> list[Requirement]:
    """What the exporter cannot run without. GFW events, SHAP and the feature matrix are optional: without them the
    dossiers have no GFW events or length and the rows no drivers, which is what the data says."""
    pq, rp = config.PARQUET_DIR, config.REPORTS_DIR
    return [
        Requirement(config.WINDOW_FILE, "0", "window and cutoffs"),
        Requirement(pq / "sanctions_actions.parquet", "2", "designations and outcomes"),
        Requirement(pq / "hull_map.parquet", "3", "hull ids"),
        Requirement(pq / "identity_intervals.parquet", "3", "identity timeline"),
        Requirement(pq / "scores", "6", "every model's scores (rerun `make phase6` if only this is missing)"),
        Requirement(rp / "metrics_by_cutoff.csv", "5b", "metrics"),
    ]


def missing() -> list[Requirement]:
    return [r for r in requirements() if not r.path.exists()]


class NotReady(RuntimeError):
    pass


def _utc(t: datetime | None) -> datetime | None:
    return None if t is None else (t if t.tzinfo else t.replace(tzinfo=UTC))


def _iso3(v: str | None) -> str | None:
    return v if v and len(v) == 3 and set(v) <= ISO3 else None


def _rows(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def _in(values) -> str:
    vals = sorted({str(int(v)) for v in values}) or ["NULL"]
    return "(" + ",".join(vals) + ")"


def _metrics_csv() -> dict[tuple[str, str, str], dict]:
    """(cutoff, harness model, stratum) -> the row `make backtest` wrote. Copied, never recomputed (rule 4)."""
    with open(config.REPORTS_DIR / "metrics_by_cutoff.csv") as f:
        return {(r["cutoff"], r["model"], r["stratum"]): r for r in csv.DictReader(f)
                if r["label_set"] == "union" and r.get("not_scored") != "True"}


def _num(v: str | None) -> float | None:
    return float(v) if v not in (None, "") else None


def _metrics(row: dict | None, stratum: str) -> Metrics | None:
    if not row:
        return None
    return Metrics(stratum=stratum,
                   precision_at={str(k): _num(row.get(f"precision_at_{k}")) for k in config.TOP_K},
                   recall_at={str(k): _num(row.get(f"recall_at_{k}")) for k in config.TOP_K},
                   pr_auc=_num(row.get("pr_auc")), base_rate=_num(row.get("base_rate")),
                   n_positives=int(row["n_positive"]) if row.get("n_positive") else None)


def _outcome(imo: int | None, T: date, adds: dict[int, list[tuple[date, str]]], label: int | None,
             lead: float | None) -> Outcome:
    after = sorted(a for a in adds.get(imo or -1, []) if a[0] > T)
    if not after:
        return Outcome(label=label)
    first = after[0][0]
    return Outcome(label=label, designation_date=first,
                   designation_authorities=sorted({s for d, s in after if d == first}),
                   lead_weeks=lead if label == 1 else None)


def _scores(con, T: date) -> list[dict]:
    part = config.PARQUET_DIR / "scores" / f"cutoff={T.isoformat()}" / "part-0.parquet"
    return _rows(con, f"SELECT * FROM '{part.as_posix()}'")


def _names_at(con, T: date) -> dict[str, tuple[str | None, str | None]]:
    """hull -> (name, flag) in force at the end of T, from the hull's most recently started interval."""
    rows = con.execute(f"""
        SELECT hm.hull_id, arg_max(i.name_normalised, i."start"), arg_max(i.flag_iso3, i."start")
        FROM read_parquet('{(config.PARQUET_DIR / INTERVALS).as_posix()}') i
        JOIN {hull_at(T)} hm ON hm.mmsi = i.mmsi
        WHERE i."start" <= TIMESTAMP '{T} 23:59:59' GROUP BY 1
    """).fetchall()
    return {h: (n, _iso3(f)) for h, n, f in rows}


def export_live(out_dir: Path | None = None, con=None) -> dict:
    """Build and write the live bundle. Raises NotReady listing the phases still owed."""
    gaps = missing()
    if gaps:
        lines = [f"  phase {r.phase}: {r.what} ({r.path})" for r in gaps]
        raise NotReady("live UI export needs outputs that do not exist yet:\n" + "\n".join(lines)
                       + "\nuse `make ui-fixtures` to work on the console with synthetic data")
    con = con or connect()
    window = config.load_window()
    cutoffs = sorted(date.fromisoformat(p.name.split("=")[1])
                     for p in (config.PARQUET_DIR / "scores").glob("cutoff=*"))
    if not cutoffs:
        raise NotReady("data/parquet/scores is empty; run `make phase6`")
    supervised = set(config.supervised_cutoffs(config.monthly_cutoffs(window, date.today())))
    metric_rows = _metrics_csv()
    adds: dict[int, list[tuple[date, str]]] = {}
    for a in lab.actions():
        if a["action"] == "add":
            adds.setdefault(a["imo"], []).append((a["date"], a["source"]))
    lead = {m: (v or {}).get("by_hull") or {}
            for m, v in ((probes.read("backtest") or {}).get("lead_time") or {}).items()}
    evaluation_limit = config.evaluation_limit(date.today())

    watchlists: list[Watchlist] = []
    score_points: dict[str, list[ScorePoint]] = {}
    pop_size: dict[date, int] = {}
    for T in cutoffs:
        rows = _scores(con, T)
        pop_size[T] = len(rows)
        closed = T <= evaluation_limit
        imos = [int(r["hull_id"]) for r in rows if r["hull_id"].isdigit()]
        labels = {x["imo"]: x["label"] for x in lab.labels(T, imos)} if closed else {}
        shap = explain.read_shap(T)
        names = _names_at(con, T)
        for model, ui_model in MODELS.items():
            ranked = sorted((r for r in rows if r.get(model) is not None),
                            key=lambda r: (-r[model], r["hull_id"]))
            if not ranked:
                continue  # supervised models before the first closed horizon: no file, not an empty one
            for rank, r in enumerate(ranked, start=1):
                score_points.setdefault(r["hull_id"], []).append(ScorePoint(
                    cutoff=T, model=ui_model, score=round(r[model], 6), rank=rank, population_size=len(rows)))
            top = []
            for rank, r in enumerate(ranked[:TOP_N], start=1):
                h = r["hull_id"]
                imo = int(h) if h.isdigit() else None
                name, flag = names.get(h, (None, None))
                drivers = [Driver(feature=d["feature"], value=d["value"], contribution=d["contribution"])
                           for d in (shap.get(h) or [])[:10]] if model == "LGBM" else []
                top.append(WatchlistRow(
                    rank=rank, hull_id=h, imo=imo, name=name, flag_iso3=flag, score=round(r[model], 6),
                    b1_stratum=bool(r["b1"]), drivers=drivers,
                    outcome=_outcome(imo, T, adds, (labels.get(imo, 0) if imo else 0) if closed else None,
                                     (lead.get(model) or {}).get(h)),
                    has_dossier=True))
            ms = [m for m in (_metrics(metric_rows.get((T.isoformat(), model, s)), s) for s in ("all", "b1")) if m]
            watchlists.append(Watchlist(origin="live", cutoff=T, model=ui_model, label_set=LABEL_SET,
                                        population_size=len(rows), metrics=ms, rows=top))

    models = [m for m in MODELS.values() if any(w.model == m for w in watchlists)]
    hulls = sorted({r.hull_id for w in watchlists for r in w.rows})
    dossiers = dossiers_for(con, hulls, window.end, adds, score_points)
    manifest = Manifest(
        origin="live", generated_at=datetime.now(UTC), window_start=window.start, window_end=window.end,
        cutoffs=[CutoffInfo(cutoff=T, horizon_end=T + timedelta(days=config.HORIZON_DAYS),
                            horizon_closed=T <= evaluation_limit, supervised=T in supervised) for T in cutoffs],
        models=models, default_model="lightgbm" if "lightgbm" in models else models[0],
        label_sets=[LABEL_SET], default_label_set=LABEL_SET,
        k_values=list(config.TOP_K),
        features=[FeatureDef(name=n, family=FAMILY.get(fam, fam), source=SOURCE_OF_FAMILY[fam], description=d)
                  for n, (fam, d) in FEATURES.items()],
        vessels=[d.hull_id for d in dossiers],
        overlays=[Overlay(name="Skagen anchorage (Skaw Road)", kind="anchorage",
                          lon=sum(config.SKAGEN_ANCHORAGE_BBOX["lon"]) / 2,
                          lat=sum(config.SKAGEN_ANCHORAGE_BBOX["lat"]) / 2,
                          bbox=[config.SKAGEN_ANCHORAGE_BBOX["lon"][0], config.SKAGEN_ANCHORAGE_BBOX["lat"][0],
                                config.SKAGEN_ANCHORAGE_BBOX["lon"][1], config.SKAGEN_ANCHORAGE_BBOX["lat"][1]],
                          placeholder=False)],
        notes=["LIVE bundle from the real pipeline. Local only: dossiers carry GFW-derived events (rule 7).",
               "One label set (OFAC+EU+UK, the PREREG headline): the supervised models were scored under it "
               "only. Metrics are copied from reports/metrics_by_cutoff.csv.",
               "Drivers are LightGBM SHAP contributions; the other models' rows have none.",
               "Dossier tracks map transmitters to hulls as known at the end of the window (ADR-23)."],
    )
    return ubundle.write_bundle(out_dir or config.UI_DATA_DIR, manifest, watchlists, dossiers)


def dossiers_for(con, hulls: list[str], end: date, adds, score_points) -> list[Dossier]:
    """One dossier per hull: identity, thinned DMA track, every event family, scores and sanctions."""
    hm = _rows(con, f"SELECT mmsi, hull_id FROM {hull_at(end)} WHERE hull_id IN "
                    f"({','.join(repr(h) for h in hulls) or 'NULL'})")
    mmsis: dict[str, list[int]] = {}
    for r in hm:
        mmsis.setdefault(r["hull_id"], []).append(r["mmsi"])
    hull_of = {r["mmsi"]: r["hull_id"] for r in hm}
    all_mmsi = _in(hull_of)

    tracks = _tracks(con, all_mmsi, hull_of)
    events: dict[str, list[Event]] = {}

    def add(h: str | None, e: dict) -> None:
        if h is None or e["start"] is None or e["end"] is None:
            return
        lst = events.setdefault(h, [])
        lst.append(Event(id=f"{e['type']}:{len(lst) + 1}", **e))

    ints = _rows(con, f"""SELECT mmsi, "start", "end", name_normalised AS name, callsign, flag_iso3
                          FROM read_parquet('{(config.PARQUET_DIR / INTERVALS).as_posix()}')
                          WHERE mmsi IN {all_mmsi}
                          ORDER BY mmsi, "start"
                       """)
    identity: dict[str, list[IdentityInterval]] = {}
    prev: dict[int, dict] = {}
    for r in ints:
        h = hull_of[r["mmsi"]]
        identity.setdefault(h, []).append(IdentityInterval(
            start=_utc(r["start"]), end=_utc(r["end"]), mmsi=r["mmsi"], imo=int(h) if h.isdigit() else None,
            name=r["name"], callsign=r["callsign"], flag_iso3=_iso3(r["flag_iso3"]), source="dma"))
        p = prev.get(r["mmsi"])
        if p:
            changed = {k: [p[k], r[k]] for k in ("name", "callsign", "flag_iso3") if p[k] != r[k]}
            t = _utc(r["start"])
            add(h, {"type": "identity_change", "source": "dma", "start": t, "end": t, "observed_at": t,
                    "summary": "Identity change: " + ", ".join(f"{k} {a} -> {b}" for k, (a, b) in changed.items()),
                    "attrs": {"mmsi": r["mmsi"], **{f"{k}_from": a for k, (a, _) in changed.items()},
                              **{f"{k}_to": b for k, (_, b) in changed.items()}}})
        prev[r["mmsi"]] = r

    imos = [int(h) for h in hulls if h.isdigit()]
    if has_table("gfw_events") and imos:
        types = ",".join(f"'{t}'" for t in sorted(GFW_TYPES))
        for r in _rows(con, f"""SELECT * FROM read_parquet('{glob_table("gfw_events")}', hive_partitioning=true)
                               WHERE imo IN {_in(imos)} AND event_type IN ({types})"""):
            port = ", ".join(x for x in (r.get("port_name"), r.get("port_country")) if x)
            hours = round(r["duration_h"], 1) if r.get("duration_h") is not None else None
            add(str(r["imo"]), {
                "type": r["event_type"], "source": "gfw", "start": _utc(r["start"]), "end": _utc(r["end"]),
                "observed_at": _utc(r["observed_at"]), "lat": r["lat"], "lon": r["lon"],
                "summary": f"GFW {r['event_type'].replace('_', ' ')}" + (f" at {port}" if port else "")
                           + (f", {hours} h" if hours is not None else ""),
                "attrs": {k: r[k] for k in ("duration_h", "port_name", "port_country", "gap_distance_km",
                                            "gap_implied_speed_knots", "partner_ssvid",
                                            "encounter_median_distance_km", "start_distance_from_shore_km")
                          if r.get(k) is not None}})
    _detector_events(con, all_mmsi, hull_of, add)

    by_act: dict[int, list[dict]] = {}
    for a in lab.actions():
        by_act.setdefault(a["imo"], []).append(a)
    lengths = _lengths(con, hulls)
    out = []
    for h in hulls:
        t = tracks.get(h) or {"t": [], "lon": [], "lat": [], "sog": [], "draught": []}
        imo = int(h) if h.isdigit() else None
        out.append(Dossier(
            origin="live", hull_id=h, imo=imo,
            header=VesselHeader(length_m=lengths.get(h), beam_m=None, dwt=None, built_year=None, ship_type="Tanker"),
            # time order across transmitters: the console takes the last interval started by the as-of date,
            # so sorting by MMSI first showed a reflagged ship under its old name (9297321, Oct 6 2026)
            identity=sorted(identity.get(h, []), key=lambda iv: (iv.start, iv.mmsi or 0)), track=Track(**t),
            events=sorted(events.get(h, []), key=lambda e: (e.start, e.id)),
            scores=score_points.get(h, []),
            sanctions=[SanctionAction(authority=a["source"], action=a["action"], date=a["date"],
                                      program=a.get("program"))
                       for a in sorted(by_act.get(imo or -1, []), key=lambda a: a["date"])
                       if a["source"] in ("OFAC", "EU", "UK") and a["action"] in ("add", "modify", "remove")]))
    return out


def _detector_events(con, all_mmsi: str, hull_of: dict[int, str], add) -> None:
    """Phase 4b tables, each with its own observed_at. STS names its partner as a hull where it has one."""
    if has_table("detect_sts"):
        for r in _rows(con, f"""SELECT * FROM read_parquet('{glob_table("detect_sts")}', hive_partitioning=true)
                               WHERE mmsi_a IN {all_mmsi} OR mmsi_b IN {all_mmsi}"""):
            for me, other in ((r["mmsi_a"], r["mmsi_b"]), (r["mmsi_b"], r["mmsi_a"])):
                if me in hull_of:
                    add(hull_of[me], {"type": "sts_candidate", "source": "self_built", "start": _utc(r["start"]),
                                      "end": _utc(r["end"]), "observed_at": _utc(r["observed_at"]),
                                      "lat": r["lat"], "lon": r["lon"], "partner_hull_id": hull_of.get(other),
                                      "summary": f"STS candidate, {r['hours']:.1f} h, closest "
                                                 f"{r['min_distance_m']:.0f} m",
                                      "attrs": {"hours": round(r["hours"], 2), "partner_mmsi": other,
                                                "min_distance_m": round(r["min_distance_m"])}})
    if has_table("detect_loitering"):
        for r in _rows(con, f"""SELECT * FROM read_parquet('{glob_table("detect_loitering")}',
                               hive_partitioning=true) WHERE mmsi IN {all_mmsi}"""):
            add(hull_of.get(r["mmsi"]), {"type": "anchorage_loitering", "source": "self_built",
                                         "start": _utc(r["start"]), "end": _utc(r["end"]),
                                         "observed_at": _utc(r["observed_at"]), "lat": r["lat"], "lon": r["lon"],
                                         "summary": f"Loitering {r['hours']:.1f} h ({r.get('modal_nav_status')})",
                                         "attrs": {"hours": round(r["hours"], 2),
                                                   "nav_status": r.get("modal_nav_status")}})
    if has_table("detect_draught"):
        for r in _rows(con, f"""SELECT * FROM read_parquet('{glob_table("detect_draught")}',
                               hive_partitioning=true) WHERE mmsi IN {all_mmsi}"""):
            add(hull_of.get(r["mmsi"]), {"type": "draught_inconsistency", "source": "self_built",
                                         "start": _utc(r["from"]), "end": _utc(r["to"]),
                                         "observed_at": _utc(r["observed_at"]),
                                         "summary": f"Draught {r['prev_draught']} -> {r['draught']} m "
                                                    "without a port call or STS in between",
                                         "attrs": {"prev_draught": r["prev_draught"], "draught": r["draught"],
                                                   "delta_m": round(r["delta_m"], 2)}})
    if has_table("detect_spoof"):
        for r in _rows(con, f"""SELECT * FROM read_parquet('{glob_table("detect_spoof")}',
                               hive_partitioning=true) WHERE mmsi IN {all_mmsi} AND n_jumps > 0"""):
            day = datetime.combine(r["day"], datetime.min.time(), tzinfo=UTC)
            add(hull_of.get(r["mmsi"]), {"type": "spoof_day", "source": "self_built", "start": day,
                                         "end": day + timedelta(hours=23, minutes=59, seconds=59),
                                         "observed_at": max(_utc(r["observed_at"]), day),
                                         "summary": f"{r['n_jumps']} position jumps",
                                         "attrs": {"n_jumps": r["n_jumps"],
                                                   "expected_incidence": r["expected_incidence"],
                                                   "excess": r["excess"]}})
    if has_table("detect_churn"):
        for r in _rows(con, f"""SELECT * FROM read_parquet('{glob_table("detect_churn")}',
                               hive_partitioning=true) WHERE mmsi IN {all_mmsi}"""):
            t = _utc(r["observed_at"])
            add(hull_of.get(r["mmsi"]), {"type": "identity_change", "source": "self_built", "start": t, "end": t,
                                         "observed_at": t,
                                         "summary": f"IMO under this MMSI: {r['old_value']} -> {r['new_value']}",
                                         "attrs": {"mmsi": r["mmsi"], "old": str(r["old_value"]),
                                                   "new": str(r["new_value"])}})


def _tracks(con, all_mmsi: str, hull_of: dict[int, str]) -> dict[str, dict]:
    """DMA positions per hull, one per 10 minutes in SQL first (the store holds millions of rows), then
    thin_track, with draught as-of joined from the latest static message at or before each point."""
    if not has_table("ais_dynamic"):
        return {}
    pts = con.execute(f"""
        WITH p AS (
          SELECT mmsi, min(observed_at) AS t, arg_min(lon, observed_at) AS lon, arg_min(lat, observed_at) AS lat,
                 arg_min(sog, observed_at) AS sog
          FROM read_parquet('{glob_table("ais_dynamic")}', hive_partitioning=true)
          WHERE mmsi IN {all_mmsi}
          GROUP BY mmsi, time_bucket(INTERVAL 10 minutes, observed_at)
        ), s AS (
          SELECT mmsi, observed_at, nullif(draught, 0) AS draught
          FROM read_parquet('{glob_table("ais_static")}', hive_partitioning=true)
          WHERE mmsi IN {all_mmsi} AND draught IS NOT NULL
        )
        SELECT p.mmsi, epoch(p.t)::BIGINT, p.lon, p.lat, p.sog, s.draught
        FROM p ASOF LEFT JOIN s ON p.mmsi = s.mmsi AND p.t >= s.observed_at
        ORDER BY p.t
    """).fetchall()
    by_hull: dict[str, list[tuple]] = {}
    for m, t, lon, lat, sog, dr in pts:
        by_hull.setdefault(hull_of[m], []).append((t, lon, lat, sog, dr))
    out = {}
    for h, rows in by_hull.items():
        rows.sort()
        t = [r[0] for r in rows]
        keep = thin_track(t, [r[1] for r in rows], [r[2] for r in rows])
        out[h] = {"t": [t[i] for i in keep], "lon": [round(rows[i][1], 5) for i in keep],
                  "lat": [round(rows[i][2], 5) for i in keep],
                  "sog": [None if rows[i][3] is None else round(rows[i][3], 1) for i in keep],
                  "draught": [rows[i][4] for i in keep]}
    return out


def _lengths(con, hulls: list[str]) -> dict[str, float]:
    """Length from each hull's most recent feature row; the only header field with a dated source."""
    fm = config.PARQUET_DIR / "feature_matrix"
    if not fm.exists():
        return {}
    rows = con.execute(f"""
        SELECT hull_id, arg_max(length_m, cutoff) FROM read_parquet('{fm.as_posix()}/cutoff=*/*.parquet',
               hive_partitioning=true)
        WHERE hull_id IN ({','.join(repr(h) for h in hulls) or 'NULL'}) GROUP BY 1
    """).fetchall()
    return {h: float(v) for h, v in rows if v}


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
