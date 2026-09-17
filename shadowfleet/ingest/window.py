"""Window gate (ADR-11 addendum): decide config/window.json from the DMA probe and free disk."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta

from shadowfleet import config
from shadowfleet.util import disk, probes

SAFETY = 1.15  # projected Parquet is inflated by this factor; busy days are larger than the benchmark


class WindowGateError(RuntimeError):
    pass


@dataclass
class GateDecision:
    start: str
    end: str
    earliest_available: str
    reason: str
    months: float
    parquet_bytes_per_day: int
    projected_parquet_gb: float
    free_gb_at_gate: float
    budget_gb: float
    seconds_per_day: float
    projected_machine_hours: float
    days_per_wallclock_day: float
    cutoffs: list[str]
    supervised_cutoffs: list[str]
    decided_at: str
    allow_short: bool


def months_between(a: date, b: date) -> float:
    return round((b - a).days / 30.4375, 1)


def decide(dma_probe: dict, free_gb: float, today: date, allow_short: bool = False) -> GateDecision:
    earliest = date.fromisoformat(dma_probe["earliest_day"])
    latest = date.fromisoformat(dma_probe["latest_day"])
    day = dma_probe["result"]["day_stats"][0]
    per_day = sum(v for k, v in day["bytes_out"].items() if k != "ais_fullres")
    zipped = dma_probe["zipped_bytes"]
    secs = (dma_probe.get("seconds_download") or 0) + dma_probe["result"]["stage"]["seconds"] + day["seconds"]
    n_all = (latest - earliest).days + 1
    projected = per_day * n_all * SAFETY
    peak = zipped * 2.5 * config.DMA_MAX_WORKERS
    budget = free_gb * disk.GB - config.MIN_FREE_GB * disk.GB - peak
    if budget <= 0:
        raise WindowGateError(f"no disk budget: {free_gb:.1f} GB free, reserve {config.MIN_FREE_GB} GB")
    if projected <= budget:
        start, reason = earliest, "full available history fits the disk budget"
    else:
        fit_days = int(budget / (per_day * SAFETY))
        start = latest - timedelta(days=fit_days - 1)
        reason = (f"disk: full history projects {projected / disk.GB:.0f} GB > budget {budget / disk.GB:.0f} GB; "
                  f"start moved forward from {earliest}")
    months = months_between(start, latest)
    if months < config.MIN_WINDOW_MONTHS_WITHOUT_SIGNOFF and not allow_short:
        raise WindowGateError(
            f"window would be {months} months (< {config.MIN_WINDOW_MONTHS_WITHOUT_SIGNOFF}); "
            f"reason: {reason}. Free more disk or rerun with --allow-short after Adam signs off."
        )
    w = config.Window(start, latest)
    cuts = config.monthly_cutoffs(w, today)
    n_days = (latest - start).days + 1
    return GateDecision(
        start=start.isoformat(), end=latest.isoformat(), earliest_available=earliest.isoformat(),
        reason=reason, months=months, parquet_bytes_per_day=per_day,
        projected_parquet_gb=round(per_day * n_days * SAFETY / disk.GB, 1),
        free_gb_at_gate=round(free_gb, 1), budget_gb=round(budget / disk.GB, 1),
        seconds_per_day=round(secs, 1), projected_machine_hours=round(secs * n_days / 3600, 1),
        days_per_wallclock_day=round(86400 / secs, 1) if secs else float("inf"),
        cutoffs=[c.isoformat() for c in cuts],
        supervised_cutoffs=[c.isoformat() for c in config.supervised_cutoffs(cuts)],
        decided_at=datetime.now(UTC).isoformat(timespec="seconds"), allow_short=allow_short,
    )


def run(allow_short: bool = False, today: date | None = None) -> GateDecision:
    p = probes.read("dma")
    if not p:
        raise WindowGateError("reports/probes/dma.json missing: run `make probe-dma` first")
    dec = decide(p, disk.free_gb(config.DATA_DIR), today or date.today(), allow_short)
    old = json.loads(config.WINDOW_FILE.read_text()) if config.WINDOW_FILE.exists() else None
    payload = asdict(dec)
    if old and (old.get("start"), old.get("end")) != (dec.start, dec.end):
        payload["previous"] = {"start": old.get("start"), "end": old.get("end"), "reason": old.get("reason")}
    config.WINDOW_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.WINDOW_FILE.write_text(json.dumps(payload, indent=2))
    return dec
