"""UI data contract (ADR-18): the only thing the React console in `ui/` may read.

The console is a static app over JSON files. Python owns every number; the UI only renders. These models are
the single source of truth: `make ui-schema` writes their JSON Schema to `ui/src/contract/schema/`, the UI
generates its TypeScript types from those files, and tests on both sides fail when the two drift.

Files in a bundle (default `ui/public/ui_data/`, gitignored because dossiers carry GFW-derived events, rule 7):
  manifest.json
  watchlist/<cutoff>__<model>__<label_set>.json
  vessels/<file_key(hull_id)>.json

Invariants enforced here, not in the UI:
  - timestamps are UTC-aware; `observed_at` is never before an event starts (rule 1);
  - a watchlist row is never a hull already designated on or before the cutoff (rule 3);
  - ranks are 1..n with non-increasing scores;
  - metrics exist only for origin="live" if they come from `reports/` (rule 4); synthetic bundles say so.

Bump CONTRACT_VERSION on any change: major when a field is removed or renamed, minor when one is added.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "1.0.0"

Origin = Literal["synthetic", "live"]
LabelSet = Literal["ofac_eu_uk", "ofac_only"]  # PREREG headline is ofac_eu_uk; ofac_only is a sensitivity table
SanctionAuthority = Literal["OFAC", "EU", "UK"]
DataSource = Literal["dma", "gfw", "self_built", "sanctions"]
FeatureFamily = Literal["identity", "ais", "gfw", "detect", "static", "graph", "anomaly"]
EventType = Literal[
    "gap",                    # GFW GAP (4a)
    "encounter",              # GFW ENCOUNTER (4a)
    "loitering",              # GFW LOITERING (4a)
    "port_visit",             # GFW PORT_VISIT (4a)
    "sts_candidate",          # detect/sts.py (4b)
    "anchorage_loitering",    # detect/loitering.py (4b)
    "draught_inconsistency",  # detect/draught.py (4b)
    "identity_change",        # identity_intervals boundary (3)
    "spoof_day",              # detect/spoof.py (4b)
]
HULL_ID_RE = r"^(\d{7}|gfw:[A-Za-z0-9_.-]+|syn:[0-9a-f]+|fixture:\d{4})$"
ISO3_RE = r"^[A-Z]{3}$"

AttrValue = str | float | int | bool | None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, json_schema_serialization_defaults_required=True)


def _utc(v: datetime | None) -> datetime | None:
    if v is not None and v.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be UTC")
    return v


def file_key(hull_id: str) -> str:
    """Filesystem-safe name for a hull's dossier (':' is not allowed on Windows)."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", hull_id)


# --------------------------------------------------------------------------- manifest
class FeatureDef(_Strict):
    name: str
    family: FeatureFamily
    source: DataSource
    description: str
    post_hoc: bool = False  # rule 2: anything added after Phase 5b results were seen


class CutoffInfo(_Strict):
    cutoff: date
    horizon_end: date
    horizon_closed: bool
    supervised: bool  # at least one earlier cutoff's horizon had closed (config.supervised_cutoffs)


class Overlay(_Strict):
    """Static map context: configured anchorages, port zones, coverage areas (from config, never from labels)."""

    name: str
    kind: Literal["anchorage", "port", "coverage"]
    lon: float
    lat: float
    bbox: list[float] | None = Field(None, min_length=4, max_length=4, description="lon_min, lat_min, lon_max, lat_max")
    placeholder: bool = Field(description="value is a PLACEHOLDER in config.py, not a sourced polygon")


class Manifest(_Strict):
    contract_version: str = CONTRACT_VERSION
    origin: Origin
    generated_at: AwareDatetime
    window_start: date
    window_end: date
    cutoffs: list[CutoffInfo] = Field(min_length=1)
    models: list[str] = Field(min_length=1)
    default_model: str
    label_sets: list[LabelSet] = Field(min_length=1)
    default_label_set: LabelSet
    k_values: list[int]
    features: list[FeatureDef]
    vessels: list[str] = Field(description="hull_ids that have a dossier file")
    overlays: list[Overlay] = []
    notes: list[str] = []

    _chk_utc = field_validator("generated_at")(_utc)

    @model_validator(mode="after")
    def _consistent(self) -> Manifest:
        if self.default_model not in self.models:
            raise ValueError("default_model not in models")
        if self.default_label_set not in self.label_sets:
            raise ValueError("default_label_set not in label_sets")
        cuts = [c.cutoff for c in self.cutoffs]
        if cuts != sorted(set(cuts)):
            raise ValueError("cutoffs must be unique and ascending")
        names = [f.name for f in self.features]
        if len(names) != len(set(names)):
            raise ValueError("duplicate feature names")
        return self


# --------------------------------------------------------------------------- watchlist
class Driver(_Strict):
    """One SHAP contribution (Phase 6) or rule term (baselines) behind a score."""

    feature: str
    value: float | None
    contribution: float


class Outcome(_Strict):
    """Hindsight. The UI hides this unless the viewer turns on outcome reveal."""

    label: Literal[0, 1] | None = Field(description="null while the horizon is still open")
    designation_date: date | None = None
    designation_authorities: list[SanctionAuthority] = []
    lead_weeks: float | None = Field(None, description="event-study lead time (plan ADR-11), hull level")


class WatchlistRow(_Strict):
    rank: int = Field(ge=1)
    hull_id: Annotated[str, Field(pattern=HULL_ID_RE)]
    imo: int | None
    name: str | None
    flag_iso3: Annotated[str, Field(pattern=ISO3_RE)] | None
    score: float
    b1_stratum: bool = Field(description="B1 Russia-port rule fired at this cutoff")
    drivers: list[Driver] = Field(max_length=10)
    outcome: Outcome
    has_dossier: bool


class Metrics(_Strict):
    stratum: Literal["all", "b1"]
    precision_at: dict[str, float | None]  # keys are str(k)
    recall_at: dict[str, float | None]
    pr_auc: float | None
    base_rate: float | None
    n_positives: int | None


class Watchlist(_Strict):
    contract_version: str = CONTRACT_VERSION
    origin: Origin
    cutoff: date
    model: str
    label_set: LabelSet
    population_size: int = Field(ge=0)
    metrics: list[Metrics] = []
    rows: list[WatchlistRow]

    @model_validator(mode="after")
    def _invariants(self) -> Watchlist:
        ranks = [r.rank for r in self.rows]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("ranks must be 1..n in order")
        scores = [r.score for r in self.rows]
        if any(b > a for a, b in zip(scores, scores[1:], strict=False)):
            raise ValueError("scores must be non-increasing with rank")
        ids = [r.hull_id for r in self.rows]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate hull_id in watchlist")
        for r in self.rows:
            d = r.outcome.designation_date
            if d is not None and d <= self.cutoff:
                raise ValueError(f"{r.hull_id} was designated on {d}, before cutoff {self.cutoff} (rule 3)")
        if len(self.rows) > self.population_size:
            raise ValueError("more rows than population")
        return self


def watchlist_filename(cutoff: date, model: str, label_set: str) -> str:
    return f"{cutoff.isoformat()}__{model}__{label_set}.json"


# --------------------------------------------------------------------------- vessel dossier
class IdentityInterval(_Strict):
    start: AwareDatetime
    end: AwareDatetime | None
    mmsi: int | None
    imo: int | None
    name: str | None
    callsign: str | None
    flag_iso3: Annotated[str, Field(pattern=ISO3_RE)] | None
    source: DataSource

    _chk_utc = field_validator("start", "end")(_utc)


class Track(_Strict):
    """DMA positions, thinned for display. Columnar to keep files small. `t` is epoch seconds (UTC)."""

    t: list[int]
    lon: list[float]
    lat: list[float]
    sog: list[float | None]
    draught: list[float | None]

    @model_validator(mode="after")
    def _shape(self) -> Track:
        n = len(self.t)
        if any(len(x) != n for x in (self.lon, self.lat, self.sog, self.draught)):
            raise ValueError("track arrays must have equal length")
        if any(b < a for a, b in zip(self.t, self.t[1:], strict=False)):
            raise ValueError("track must be time-ordered")
        return self


class Event(_Strict):
    id: str
    type: EventType
    source: DataSource
    start: AwareDatetime
    end: AwareDatetime
    observed_at: AwareDatetime = Field(description="when this record became knowable; the as-of filter key")
    lat: float | None = Field(None, ge=-90, le=90)
    lon: float | None = Field(None, ge=-180, le=180)
    partner_hull_id: str | None = None
    summary: str
    attrs: dict[str, AttrValue] = {}

    _chk_utc = field_validator("start", "end", "observed_at")(_utc)

    @model_validator(mode="after")
    def _order(self) -> Event:
        if self.end < self.start:
            raise ValueError(f"{self.id}: end before start")
        if self.observed_at < self.start:
            raise ValueError(f"{self.id}: observed_at before start (rule 1)")
        return self


class ScorePoint(_Strict):
    cutoff: date
    model: str
    score: float
    rank: int | None
    population_size: int


class SanctionAction(_Strict):
    """Hindsight, like Outcome."""

    authority: SanctionAuthority
    action: Literal["add", "modify", "remove"]
    date: date
    program: str | None


class VesselHeader(_Strict):
    length_m: float | None
    beam_m: float | None
    dwt: float | None
    built_year: int | None
    ship_type: str | None


class Dossier(_Strict):
    contract_version: str = CONTRACT_VERSION
    origin: Origin
    hull_id: Annotated[str, Field(pattern=HULL_ID_RE)]
    imo: int | None
    header: VesselHeader
    identity: list[IdentityInterval]
    track: Track
    events: list[Event]
    scores: list[ScorePoint]
    sanctions: list[SanctionAction]

    @model_validator(mode="after")
    def _ids(self) -> Dossier:
        ids = [e.id for e in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate event ids")
        return self


TOP_LEVEL = {"manifest": Manifest, "watchlist": Watchlist, "dossier": Dossier}


def json_schemas() -> dict[str, dict]:
    """JSON Schema (draft 2020-12) per top-level file type, as written to ui/src/contract/schema/."""
    out = {}
    for name, model in TOP_LEVEL.items():
        s = model.model_json_schema(mode="serialization")
        s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        s["$id"] = f"shadowfleet/ui/{name}.schema.json"
        s["x-contract-version"] = CONTRACT_VERSION
        out[name] = s
    return out
