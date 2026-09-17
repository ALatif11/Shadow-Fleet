"""Probe result files: reports/probes/<name>.json. Report generators read only these."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from shadowfleet import config


def write(name: str, payload: dict[str, Any]) -> None:
    config.PROBE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"probe": name, "written_at": datetime.now(UTC).isoformat(timespec="seconds"), **payload}
    (config.PROBE_DIR / f"{name}.json").write_text(json.dumps(payload, indent=2, default=str))


def read(name: str) -> dict[str, Any] | None:
    p = config.PROBE_DIR / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None
