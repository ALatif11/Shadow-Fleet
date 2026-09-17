"""Disk guard (ADR-13): ingest pauses when free space drops below MIN_FREE_GB."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from shadowfleet import config

log = logging.getLogger(__name__)
GB = 1024**3


class DiskFullError(RuntimeError):
    pass


def free_gb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / GB


def wait_for_space(
    path: Path,
    need_bytes: int = 0,
    min_free_gb: float | None = None,
    resume_free_gb: float | None = None,
    poll_s: float = 60.0,
    max_wait_s: float | None = None,
    _sleep=time.sleep,
) -> None:
    """Block until `path` has room for `need_bytes` while keeping `min_free_gb` spare.

    Raises DiskFullError if `max_wait_s` elapses (max_wait_s=0 means fail immediately).
    """
    min_free = config.MIN_FREE_GB if min_free_gb is None else min_free_gb
    resume = config.RESUME_FREE_GB if resume_free_gb is None else resume_free_gb
    need_gb = need_bytes / GB
    if free_gb(path) - need_gb >= min_free:
        return
    waited = 0.0
    while True:
        free = free_gb(path)
        if free - need_gb >= resume:
            log.info("disk guard released", extra={"free_gb": round(free, 1)})
            return
        if max_wait_s is not None and waited >= max_wait_s:
            raise DiskFullError(
                f"{free:.1f} GB free at {path}; need {need_gb:.1f} GB plus {min_free:.0f} GB reserve"
            )
        log.warning(
            "disk guard: pausing",
            extra={"free_gb": round(free, 1), "need_gb": round(need_gb, 2), "resume_at_gb": resume},
        )
        _sleep(poll_s)
        waited += poll_s
