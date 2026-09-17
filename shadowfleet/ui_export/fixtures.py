"""Synthetic UI bundle (ADR-18). Deterministic, clearly labelled, never mistaken for results.

Everything here is invented: hull ids are `fixture:NNNN`, names start with "SYN", MMSIs sit in the unallocated
999xxxxxx block, IMOs are null, and every file says origin="synthetic". The generator keeps the same internal
logic the real pipeline will have, so the UI is exercised against realistic shapes:
  features(T) read only events with observed_at in (T - 180 d, T]; designated hulls leave the population
  after their first designation (rule 3); labels use the 182-day horizon; metrics are computed from the rows.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from shadowfleet import config
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

GENERATED_AT = datetime(2026, 9, 17, tzinfo=UTC)
FIXTURE_TODAY = date(2026, 9, 17)
MODELS = ["lightgbm", "logreg", "b2_rules", "isoforest"]
SUPERVISED_MODELS = {"lightgbm", "logreg"}
LABEL_SETS = ["ofac_eu_uk", "ofac_only"]

# Draft of the Phase 5a registry (phase-prompts.md). PREREG.md freezes the real list in Phase 2.
FEATURES: list[tuple[str, str, str, str]] = [
    ("n_name_changes", "identity", "dma", "Name changes in AIS static data, lifetime as of T"),
    ("n_flag_changes", "identity", "dma", "Flag (MID) changes, lifetime as of T"),
    ("flag_to_convenience_registry", "identity", "dma", "Most recent flag change went to a convenience registry"),
    ("days_since_last_identity_change", "identity", "dma", "Days since the last name/flag/MMSI change"),
    ("n_transits", "ais", "dma", "Passages through Danish waters in the 180-day window"),
    ("n_laden_transits", "ais", "dma", "Transits with draught above the hull's 75th percentile"),
    ("share_russian_destination", "ais", "dma", "Share of transits whose AIS destination is a Russian port"),
    ("spoof_jump_rate_excess", "ais", "self_built", "Position-jump rate above the cell-day baseline"),
    ("n_gaps", "gfw", "gfw", "GFW AIS gap events"),
    ("gap_hours_total", "gfw", "gfw", "Total hours dark across GFW gap events"),
    ("max_gap_distance_km", "gfw", "gfw", "Longest implied distance covered while dark"),
    ("n_encounters", "gfw", "gfw", "GFW at-sea encounters"),
    ("n_encounters_with_sanctioned_partner", "gfw", "gfw", "Encounters with a partner listed as of T"),
    ("loitering_hours", "gfw", "gfw", "Hours of GFW loitering"),
    ("n_russian_port_visits", "gfw", "gfw", "GFW port visits at Russian ports"),
    ("n_sts_candidates", "detect", "self_built", "Self-built ship-to-ship transfer candidates"),
    ("anchorage_loitering_hours", "detect", "self_built", "Hours loitering in configured anchorages"),
    ("n_draught_inconsistencies", "detect", "self_built", "Draught changes with no port call or STS"),
    ("vessel_age_years", "static", "gfw", "Age from registry build year"),
]
WEIGHTS = {
    "n_name_changes": 0.5, "n_flag_changes": 0.6, "flag_to_convenience_registry": 0.8,
    "days_since_last_identity_change": -0.002, "n_transits": 0.05, "n_laden_transits": 0.15,
    "share_russian_destination": 1.2, "spoof_jump_rate_excess": 2.0, "n_gaps": 0.25, "gap_hours_total": 0.006,
    "max_gap_distance_km": 0.0015, "n_encounters": 0.2, "n_encounters_with_sanctioned_partner": 0.9,
    "loitering_hours": 0.004, "n_russian_port_visits": 0.35, "n_sts_candidates": 0.5,
    "anchorage_loitering_hours": 0.01, "n_draught_inconsistencies": 0.4, "vessel_age_years": 0.05,
}
NAMES = ["KESTREL", "MARLOW", "ORIEL", "TAMSIN", "HALCYON", "VESPER", "CORVID", "LUMEN", "SABLE", "ARDEN",
         "BRIAR", "CINDER", "DUNLIN", "EMBER", "FALLOW", "GANNET", "HOLLIS", "ISOLDE", "JUNIPER", "KALIX",
         "LARKSPUR", "MERIDIAN", "NOLAN", "OSPREY", "PERRIN", "QUILL", "ROWAN", "SELKIE", "TERN", "UMBER",
         "VALE", "WREN", "YARROW", "ZEPHYR", "AUSTRAL", "BOREAL", "CAIRN", "DRIFT", "ESKER", "FJELL"]
FLAGS_MAIN = ["GRC", "MLT", "LBR", "MHL", "PAN", "CYP", "BHS", "NOR", "SGP", "HKG"]
FLAGS_CONVENIENCE = list(config.CONVENIENCE_FLAGS)

# Route T through the Danish straits, west-bound (lon, lat). Checked against Natural Earth 10m land on Sep 17 2026
# (waypoints and legs all at sea; ~0.02 percent of jittered points graze the coast).
ROUTE_T = [(14.20, 54.85), (13.20, 54.75), (12.30, 54.45), (11.60, 54.52), (11.20, 54.58), (10.88, 54.80),
           (10.92, 54.90), (10.97, 55.00), (11.02, 55.10), (11.02, 55.20), (11.00, 55.30), (10.98, 55.40),
           (10.95, 55.50), (10.90, 55.60), (10.75, 55.76), (10.90, 55.84), (11.10, 55.90), (11.20, 56.05),
           (11.35, 56.40), (11.20, 56.80), (11.30, 57.10), (11.30, 57.30), (11.00, 57.50), (10.80, 57.80),
           (10.50, 57.95), (10.00, 57.93), (9.20, 57.75), (8.20, 57.55)]
SKAGEN_LEG = 22  # index of the leg that ends off Skagen, where risky hulls divert to the anchorage
SKAGEN_ANCHOR = [(10.72, 57.64), (10.80, 57.70), (10.86, 57.66), (10.76, 57.60)]
RUSSIAN_PORTS = {"Primorsk": (28.66, 60.35), "Ust-Luga": (28.30, 59.72), "Vysotsk": (28.45, 60.45)}
GAP_AREAS = [(4.0, 60.5, "Norwegian Sea approaches"), (-9.8, 43.5, "off Cape Finisterre"),
             (22.8, 36.2, "south of the Peloponnese"), (-6.0, 36.0, "Strait of Gibraltar approaches"),
             (20.5, 56.8, "central Baltic")]
STS_AREAS = [(22.85, 36.60, "Laconian Gulf"), (-5.35, 36.00, "off Ceuta"), (10.80, 57.68, "Skagen anchorage")]


def _dt(d: date | datetime) -> datetime:
    return d if isinstance(d, datetime) else datetime(d.year, d.month, d.day, tzinfo=UTC)


@dataclass
class Hull:
    idx: int
    risk: float
    built_year: int
    length_m: float
    designations: dict[str, date] = field(default_factory=dict)
    identity: list[IdentityInterval] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    track: list[tuple[int, float, float, float | None, float | None]] = field(default_factory=list)
    transits: list[tuple[datetime, bool, bool]] = field(default_factory=list)  # (time, laden, russian dest)

    @property
    def hull_id(self) -> str:
        return f"fixture:{self.idx:04d}"

    @property
    def first_listed(self) -> date | None:
        return min(self.designations.values()) if self.designations else None


class _Gen:
    def __init__(self, seed: int, start: date, end: date, n_hulls: int, points_per_leg: int):
        self.r = random.Random(seed)
        self.start, self.end = start, end
        self.t0 = _dt(start - timedelta(days=config.FEATURE_WINDOW_DAYS))
        self.t1 = _dt(end)
        self.n_hulls = n_hulls
        self.ppl = points_per_leg
        self.hulls: list[Hull] = []
        self._eid = 0

    # ---------------------------------------------------------------- helpers
    def _rand_time(self, a: datetime | None = None, b: datetime | None = None) -> datetime:
        a, b = a or self.t0, b or self.t1
        return a + timedelta(seconds=self.r.uniform(0, (b - a).total_seconds()))

    def _event(self, h: Hull, etype, source, start, hours, lat, lon, summary, observed_at=None, **attrs) -> Event:
        self._eid += 1
        end = start + timedelta(hours=hours)
        e = Event(id=f"E{self._eid:06d}", type=etype, source=source, start=start, end=end,
                  observed_at=observed_at or end, lat=round(lat, 4), lon=round(lon, 4),
                  partner_hull_id=attrs.pop("partner_hull_id", None), summary=summary, attrs=attrs)
        h.events.append(e)
        return e

    def _jitter(self, x: float, s: float) -> float:
        return x + self.r.gauss(0, s)

    # ---------------------------------------------------------------- per hull
    def make_hulls(self) -> None:
        for i in range(1, self.n_hulls + 1):
            risk = self.r.betavariate(1.2, 2.8)
            h = Hull(idx=i, risk=risk, built_year=int(2016 - 18 * risk + self.r.randint(-3, 3)),
                     length_m=round(self.r.choice([183.0, 228.0, 244.0, 250.0, 274.0]), 1))
            self.hulls.append(h)
        # Designations: the riskiest hulls get listed, spread over the window, in waves like the real lists.
        ranked = sorted(self.hulls, key=lambda h: -h.risk)
        n_listed = max(2, int(len(ranked) * 0.3))
        span = (self.end - self.start).days
        for h in ranked[:n_listed]:
            if self.r.random() < 0.15:
                continue
            first = self.start + timedelta(days=self.r.randint(int(span * 0.25), span + 60))
            auths = self.r.sample(["OFAC", "EU", "UK"], k=self.r.choice([1, 1, 2, 3]))
            for j, a in enumerate(auths):
                h.designations[a] = first + timedelta(days=0 if j == 0 else self.r.randint(10, 150))

    def build_identity(self, h: Hull) -> None:
        n_changes = 0 if h.risk < 0.35 else self.r.choice([1, 1, 2, 3])
        times = sorted(self._rand_time() for _ in range(n_changes))
        name = f"SYN {self.r.choice(NAMES)}"
        flag = self.r.choice(FLAGS_MAIN)
        mmsi = 999_000_000 + h.idx * 10
        start = self.t0
        bounds = [*times, None]
        for k, b in enumerate(bounds):
            h.identity.append(IdentityInterval(start=start, end=b, mmsi=mmsi + k, imo=None, name=name,
                                               callsign=f"SYN{h.idx:03d}{k}", flag_iso3=flag, source="dma"))
            if b is None:
                break
            old_name, old_flag = name, flag
            name = f"SYN {self.r.choice([n for n in NAMES if f'SYN {n}' != old_name])}"
            flag = self.r.choice(FLAGS_CONVENIENCE) if self.r.random() < 0.7 else self.r.choice(FLAGS_MAIN)
            lon, lat = self.r.choice(ROUTE_T[18:24])
            self._event(h, "identity_change", "dma", b, 0, lat, lon,
                        f"{old_name} / {old_flag} became {name} / {flag}", observed_at=b,
                        old_name=old_name, new_name=name, old_flag=old_flag, new_flag=flag,
                        old_mmsi=mmsi + k, new_mmsi=mmsi + k + 1)
            start = b

    def build_voyages(self, h: Hull) -> None:
        """Non-overlapping voyages. A Russia-trade voyage is: east-bound ballast transit with a Russian AIS
        destination, a port visit at a Russian Baltic port (GFW, outside DMA coverage), then a laden west-bound
        transit. Other voyages are single transits either way."""
        cur = self.t0 + timedelta(days=self.r.uniform(0, 30))
        while cur < self.t1 - timedelta(days=14):
            if self.r.random() < 0.15 + 0.8 * h.risk:
                end = self._transit(h, cur, laden=False, russian_dest=True, westbound=False)
                port = self.r.choice(list(RUSSIAN_PORTS))
                plon, plat = RUSSIAN_PORTS[port]
                visit = end + timedelta(hours=self.r.uniform(40, 60))
                stay = self.r.uniform(20, 48)
                self._event(h, "port_visit", "gfw", visit, stay, plat, plon, f"Port visit, {port} (RUS)",
                            port=port, country="RUS", russian=True, confidence=self.r.choice([3, 4]))
                back = visit + timedelta(hours=stay + self.r.uniform(40, 60))
                cur = self._transit(h, back, laden=True, russian_dest=False, westbound=True)
            else:
                cur = self._transit(h, cur, laden=self.r.random() < 0.5, russian_dest=False,
                                    westbound=self.r.random() < 0.5)
            cur += timedelta(days=self.r.uniform(15, 70) * (1.3 - 0.6 * h.risk))

    def _transit(self, h: Hull, t: datetime, laden: bool, russian_dest: bool, westbound: bool) -> datetime:
        """Append one passage of Route T to the track; returns the time it leaves Danish waters."""
        h.transits.append((t, laden, russian_dest))
        route = ROUTE_T if westbound else ROUTE_T[::-1]
        draught = round(self.r.uniform(13.2, 14.8) if laden else self.r.uniform(7.8, 9.2), 1)
        offset = self.r.gauss(0, 0.006)
        sog = self.r.uniform(10.5, 13.0)
        stop_at_skagen = westbound and h.risk > 0.4 and self.r.random() < h.risk * 0.6
        cur = t
        for leg, (a, b) in enumerate(zip(route, route[1:], strict=False)):
            dist_nm = math.hypot((b[0] - a[0]) * 60 * math.cos(math.radians(a[1])), (b[1] - a[1]) * 60)
            leg_h = dist_nm / sog
            for s in range(self.ppl):
                f = s / self.ppl
                ts = cur + timedelta(hours=leg_h * f)
                lon = a[0] + (b[0] - a[0]) * f + self._jitter(0, 0.002) + offset
                lat = a[1] + (b[1] - a[1]) * f + self._jitter(0, 0.0015) + offset * 0.5
                h.track.append((int(ts.timestamp()), round(lon, 4), round(lat, 4),
                                round(self._jitter(sog, 0.4), 1), draught))
            cur += timedelta(hours=leg_h)
            if stop_at_skagen and leg == SKAGEN_LEG:
                cur = self._skagen_stop(h, cur, draught)
                if laden and self.r.random() < 0.5:
                    draught = round(draught - self.r.uniform(2.5, 4.5), 1)  # lightering
        if westbound and self.r.random() < 0.08 + 0.3 * h.risk:  # goes dark somewhere after leaving
            d = cur + timedelta(days=self.r.uniform(1, 6))
            lon, lat, where = self.r.choice(GAP_AREAS)
            hours = self.r.uniform(14, 140) * (0.6 + h.risk)
            km = hours * self.r.uniform(5, 18)
            self._event(h, "gap", "gfw", d, hours, self._jitter(lat, 0.6), self._jitter(lon, 0.8),
                        f"AIS gap {hours:.0f} h {where}", implied_speed_knots=round(km / hours / 1.852, 1),
                        distance_km=round(km, 0), off_lat=round(lat, 2), off_lon=round(lon, 2))
            cur = d + timedelta(hours=hours)
        return cur

    def _skagen_stop(self, h: Hull, t: datetime, draught: float) -> datetime:
        hours = self.r.uniform(14, 70)
        alon, alat = self.r.choice(SKAGEN_ANCHOR)
        steps = max(4, int(hours / 2))
        for s in range(steps):
            ts = t + timedelta(hours=hours * s / steps)
            h.track.append((int(ts.timestamp()), round(self._jitter(alon, 0.002), 4),
                            round(self._jitter(alat, 0.0015), 4), round(abs(self.r.gauss(0.3, 0.15)), 1), draught))
        self._event(h, "anchorage_loitering", "self_built", t, hours, alat, alon,
                    f"Loitered {hours:.0f} h in the Skagen anchorage", anchorage="Skagen", avg_sog_kn=0.3)
        if self.r.random() < 0.55:
            partner = self.r.choice(self.hulls)
            if partner is not h:
                sts_h = self.r.uniform(3, min(hours, 20))
                self._event(h, "sts_candidate", "self_built", t + timedelta(hours=1), sts_h, alat, alon,
                            f"STS candidate with {partner.hull_id}, {sts_h:.0f} h within 500 m",
                            partner_hull_id=partner.hull_id, min_distance_m=round(self.r.uniform(40, 300)),
                            draught_change_m=round(-self.r.uniform(0, 4), 1))
        return t + timedelta(hours=hours)

    def build_other(self, h: Hull) -> None:
        r = self.r
        for _ in range(int(r.expovariate(1 / (0.3 + 3 * h.risk)))):
            lon, lat, where = r.choice(STS_AREAS[:2])
            t = self._rand_time()
            partner = r.choice(self.hulls)
            if partner is h:
                continue
            self._event(h, "encounter", "gfw", t, r.uniform(3, 30), self._jitter(lat, 0.05),
                        self._jitter(lon, 0.05), f"Encounter with {partner.hull_id} {where}",
                        partner_hull_id=partner.hull_id, median_speed_knots=round(r.uniform(0.2, 1.5), 1),
                        median_distance_km=round(r.uniform(0.05, 0.4), 2))
            if r.random() < 0.5:
                self._event(h, "loitering", "gfw", t - timedelta(hours=r.uniform(6, 30)), r.uniform(8, 40),
                            self._jitter(lat, 0.08), self._jitter(lon, 0.08), f"Loitering {where}",
                            avg_speed=round(r.uniform(0.5, 2), 1),
                            distance_from_shore_km=round(r.uniform(5, 40), 0))
        if h.risk > 0.4:
            for _ in range(r.randint(0, 3)):
                t = self._rand_time()
                self._event(h, "spoof_day", "self_built", t.replace(hour=0, minute=0, second=0, microsecond=0),
                            24, self._jitter(19.5, 0.8), self._jitter(57.5, 0.5),
                            "Position jumps above the cell-day baseline", jumps=r.randint(3, 20),
                            baseline_share=round(r.uniform(0.01, 0.1), 3))
        for _ in range(int(r.expovariate(1 / (0.2 + 1.5 * h.risk)))):
            t = self._rand_time()
            lon, lat = r.choice(ROUTE_T[18:24])
            self._event(h, "draught_inconsistency", "self_built", t, r.uniform(6, 48), lat, lon,
                        "Draught changed with no port call or STS in between",
                        draught_before=13.8, draught_after=round(r.uniform(8.5, 12.0), 1))

    # ---------------------------------------------------------------- as-of features
    def features(self, h: Hull, T: date) -> dict[str, float]:
        hi = _dt(T) + timedelta(days=1)  # records observed on T count
        lo = hi - timedelta(days=config.FEATURE_WINDOW_DAYS)
        ev = [e for e in h.events if lo <= e.observed_at < hi]
        life = [e for e in h.events if e.observed_at < hi]
        by = lambda t: [e for e in ev if e.type == t]  # noqa: E731
        ident = [e for e in life if e.type == "identity_change"]
        trans = [x for x in h.transits if lo <= x[0] < hi]
        gaps = by("gap")
        encs = by("encounter")
        listed_partners = 0
        for e in encs:
            p = self.hulls[int(e.partner_hull_id.split(":")[1]) - 1]
            if p.first_listed is not None and p.first_listed <= T:
                listed_partners += 1
        last_ident = max((e.observed_at for e in ident), default=None)
        return {
            "n_name_changes": float(len(ident)),
            "n_flag_changes": float(sum(e.attrs["old_flag"] != e.attrs["new_flag"] for e in ident)),
            "flag_to_convenience_registry": float(bool(ident) and ident[-1].attrs["new_flag"] in FLAGS_CONVENIENCE),
            "days_since_last_identity_change": float((hi - last_ident).days) if last_ident else 3650.0,
            "n_transits": float(len(trans)),
            "n_laden_transits": float(sum(x[1] for x in trans)),
            "share_russian_destination": round(sum(x[2] for x in trans) / len(trans), 3) if trans else 0.0,
            "spoof_jump_rate_excess": round(len(by("spoof_day")) * 0.04, 3),
            "n_gaps": float(len(gaps)),
            "gap_hours_total": round(sum((e.end - e.start).total_seconds() / 3600 for e in gaps), 1),
            "max_gap_distance_km": max((float(e.attrs["distance_km"]) for e in gaps), default=0.0),
            "n_encounters": float(len(encs)),
            "n_encounters_with_sanctioned_partner": float(listed_partners),
            "loitering_hours": round(sum((e.end - e.start).total_seconds() / 3600 for e in by("loitering")), 1),
            "n_russian_port_visits": float(len(by("port_visit"))),
            "n_sts_candidates": float(len(by("sts_candidate"))),
            "anchorage_loitering_hours": round(
                sum((e.end - e.start).total_seconds() / 3600 for e in by("anchorage_loitering")), 1),
            "n_draught_inconsistencies": float(len(by("draught_inconsistency"))),
            "vessel_age_years": float(T.year - h.built_year),
        }


def _score(model: str, feats: dict[str, float], noise: float) -> tuple[float, list[Driver]]:
    contrib = {k: WEIGHTS[k] * v for k, v in feats.items()}
    if model == "b2_rules":  # coarse integer-ish rule score
        contrib = {k: round(c) for k, c in contrib.items()}
    elif model == "isoforest":
        contrib = {k: 0.5 * abs(c) for k, c in contrib.items()}
    raw = sum(contrib.values()) + noise
    score = 1 / (1 + math.exp(-(raw - 4.0) / 1.5))
    top = sorted(contrib.items(), key=lambda kv: -abs(kv[1]))[:8]
    return score, [Driver(feature=k, value=feats[k], contribution=round(c, 4)) for k, c in top if c != 0]


def _label(h: Hull, T: date, label_set: str) -> tuple[int | None, date | None, list[str]]:
    horizon = T + timedelta(days=config.HORIZON_DAYS)
    if label_set == "ofac_eu_uk":
        d = h.first_listed
        auths = sorted(a for a, x in h.designations.items() if x == d)
    else:
        d = h.designations.get("OFAC")
        auths = ["OFAC"] if d else []
    if d is None:
        return 0, None, []
    if T < d <= horizon:
        return 1, d, auths
    return 0, d, auths  # designated later than the horizon: label 0 here, date still shown in hindsight


def _metrics(rows: list[WatchlistRow], population: int, n_pos: int, k_values: list[int], stratum) -> Metrics:
    labels = [r.outcome.label or 0 for r in rows]
    prec, rec = {}, {}
    for k in k_values:
        hits = sum(labels[:k])
        prec[str(k)] = round(hits / k, 4) if len(labels) >= k else None
        rec[str(k)] = round(hits / n_pos, 4) if n_pos else None
    ap, hits = 0.0, 0
    for i, lab in enumerate(labels, 1):
        if lab:
            hits += 1
            ap += hits / i
    return Metrics(stratum=stratum, precision_at=prec, recall_at=rec,
                   pr_auc=round(ap / n_pos, 4) if n_pos else None,
                   base_rate=round(n_pos / population, 5) if population else None, n_positives=n_pos)


def overlays() -> list[Overlay]:
    sk = config.SKAGEN_ANCHORAGE_BBOX
    out = [Overlay(name="Skagen anchorage", kind="anchorage", lon=sum(sk["lon"]) / 2, lat=sum(sk["lat"]) / 2,
                   bbox=[sk["lon"][0], sk["lat"][0], sk["lon"][1], sk["lat"][1]], placeholder=True)]
    # Russian port points are fixture values; Phase 4a adds sourced polygons to config.
    out += [Overlay(name=n, kind="port", lon=lon, lat=lat, bbox=None, placeholder=True)
            for n, (lon, lat) in RUSSIAN_PORTS.items()]
    return out


def build(size: str = "full", seed: int = 7) -> tuple[Manifest, list[Watchlist], list[Dossier]]:
    if size == "full":
        start, end, n_hulls, ppl, population, top_n = date(2024, 1, 1), date(2026, 8, 31), 160, 6, 1840, 100
    elif size == "tiny":
        start, end, n_hulls, ppl, population, top_n = date(2024, 7, 1), date(2025, 9, 30), 6, 1, 40, 5
    else:
        raise ValueError(size)
    g = _Gen(seed, start, end, n_hulls, ppl)
    g.make_hulls()
    for h in g.hulls:
        g.build_identity(h)
        g.build_voyages(h)
        g.build_other(h)
        h.events.sort(key=lambda e: (e.start, e.id))
        h.track.sort()

    window = config.Window(start, end)
    cuts = config.monthly_cutoffs(window, FIXTURE_TODAY)
    supervised = set(config.supervised_cutoffs(cuts))
    if size == "tiny":
        cuts = cuts[-3:]
    k_values = list(config.TOP_K)

    ranks: dict[tuple[str, date], dict[str, tuple[int, float]]] = {}
    watchlists: list[Watchlist] = []
    feats_at: dict[tuple[str, date], dict[str, float]] = {}
    for T in cuts:
        pop_hulls = [h for h in g.hulls if h.first_listed is None or h.first_listed > T]
        pop_size = population - sum(1 for h in g.hulls if h.first_listed is not None and h.first_listed <= T)
        for h in pop_hulls:
            feats_at[(h.hull_id, T)] = g.features(h, T)
        for model in MODELS:
            if model in SUPERVISED_MODELS and T not in supervised:
                continue  # expanding-window training has nothing to train on yet (ADR-11)
            nr = random.Random(f"{seed}:{model}:{T}")
            scored = []
            for h in pop_hulls:
                s, drivers = _score(model, feats_at[(h.hull_id, T)], nr.gauss(0, 0.9))
                scored.append((round(s, 5), h.idx, h, drivers))
            scored.sort(key=lambda x: (-x[0], x[1]))
            ranks[(model, T)] = {h.hull_id: (i, s) for i, (s, _, h, _) in enumerate(scored, 1)}
            top = scored[:top_n]
            for ls in LABEL_SETS:
                rows = []
                for i, (s, _, h, drivers) in enumerate(top, 1):
                    lab, d, auths = _label(h, T, ls)
                    f = feats_at[(h.hull_id, T)]
                    ident = [x for x in h.identity if x.start <= _dt(T)][-1]
                    rows.append(WatchlistRow(
                        rank=i, hull_id=h.hull_id, imo=None, name=ident.name, flag_iso3=ident.flag_iso3,
                        score=s, b1_stratum=f["n_russian_port_visits"] > 0 or f["share_russian_destination"] > 0,
                        drivers=drivers, outcome=Outcome(label=lab, designation_date=d,
                                                         designation_authorities=auths, lead_weeks=None),
                        has_dossier=True))
                n_pos = sum(1 for h in pop_hulls if _label(h, T, ls)[0] == 1) + nr.randint(0, 6) * (size == "full")
                b1 = [r for r in rows if r.b1_stratum]
                b1 = [r.model_copy(update={"rank": j}) for j, r in enumerate(b1, 1)]
                b1_pos = sum(1 for h in pop_hulls if _label(h, T, ls)[0] == 1 and (
                    feats_at[(h.hull_id, T)]["n_russian_port_visits"] > 0))
                metrics = [_metrics(rows, pop_size, n_pos, k_values, "all"),
                           _metrics(b1, max(len(b1), 1), b1_pos, k_values, "b1")]
                watchlists.append(Watchlist(origin="synthetic", cutoff=T, model=model, label_set=ls,
                                            population_size=pop_size, metrics=metrics, rows=rows))

    # Event-study lead time: designation date minus the earliest cutoff (within the horizon before it)
    # at which the hull ranked in the top 50 for that model.
    def lead(h: Hull, model: str, ls: str) -> float | None:
        d = h.first_listed if ls == "ofac_eu_uk" else h.designations.get("OFAC")
        if d is None:
            return None
        for T in cuts:
            if T < d <= T + timedelta(days=config.HORIZON_DAYS):
                rk = ranks.get((model, T), {}).get(h.hull_id)
                if rk and rk[0] <= 50:
                    return round((d - T).days / 7, 1)
        return None

    by_id = {h.hull_id: h for h in g.hulls}
    watchlists = [w.model_copy(update={"rows": [
        r.model_copy(update={"outcome": r.outcome.model_copy(update={
            "lead_weeks": lead(by_id[r.hull_id], w.model, w.label_set) if r.outcome.label == 1 else None})})
        for r in w.rows]}) for w in watchlists]

    dossiers = []
    for h in g.hulls:
        scores = [ScorePoint(cutoff=T, model=m, score=ranks[(m, T)][h.hull_id][1],
                             rank=ranks[(m, T)][h.hull_id][0],
                             population_size=population)
                  for (m, T) in ranks if h.hull_id in ranks[(m, T)]]
        t, lon, lat, sog, dr = (list(x) for x in zip(*h.track, strict=True)) if h.track else ([], [], [], [], [])
        sanctions = [SanctionAction(authority=a, action="add", date=d, program="RUSSIA-EO14024" if a == "OFAC"
                                    else ("EU-MARE" if a == "EU" else "UK-RUS"))
                     for a, d in sorted(h.designations.items(), key=lambda kv: kv[1])]
        dossiers.append(Dossier(
            origin="synthetic", hull_id=h.hull_id, imo=None,
            header=VesselHeader(length_m=h.length_m, beam_m=round(h.length_m / 5.6, 1),
                                dwt=round(h.length_m * 450, -3), built_year=h.built_year, ship_type="Tanker"),
            identity=h.identity, track=Track(t=t, lon=lon, lat=lat, sog=sog, draught=dr),
            events=h.events, scores=scores, sanctions=sanctions))

    manifest = Manifest(
        origin="synthetic", generated_at=GENERATED_AT, window_start=start, window_end=end,
        cutoffs=[CutoffInfo(cutoff=T, horizon_end=T + timedelta(days=config.HORIZON_DAYS),
                            horizon_closed=T <= config.evaluation_limit(FIXTURE_TODAY), supervised=T in supervised)
                 for T in cuts],
        models=MODELS, default_model="lightgbm", label_sets=LABEL_SETS, default_label_set="ofac_eu_uk",
        k_values=k_values,
        features=[FeatureDef(name=n, family=f, source=s, description=d) for n, f, s, d in FEATURES],
        vessels=[h.hull_id for h in g.hulls],
        overlays=overlays(),
        notes=["SYNTHETIC FIXTURE. Every hull, event, score and metric in this bundle is invented "
               "(shadowfleet/ui_export/fixtures.py). Nothing here is a project result."],
    )
    return manifest, watchlists, dossiers
