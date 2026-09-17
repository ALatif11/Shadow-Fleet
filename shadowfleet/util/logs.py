"""Structured logging: JSON lines to reports/logs/<name>.jsonl, readable lines to stderr."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from shadowfleet import config

_STD = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _STD})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = {k: v for k, v in record.__dict__.items() if k not in _STD}
        tail = " ".join(f"{k}={v}" for k, v in extra.items())
        base = f"{datetime.now().strftime('%H:%M:%S')} {record.levelname:<7} {record.getMessage()}"
        return f"{base} {tail}".rstrip()


def setup(name: str, level: int = logging.INFO) -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    fh = logging.FileHandler(config.LOG_DIR / f"{name}.jsonl", encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(ConsoleFormatter())
    root.addHandler(fh)
    root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "pdfminer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
