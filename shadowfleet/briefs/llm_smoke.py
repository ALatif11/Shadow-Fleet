"""Phase 0 task 9: one JSON-schema-constrained prompt against a running llama-server."""

from __future__ import annotations

import json
import subprocess
import time

import httpx

from shadowfleet import config
from shadowfleet.util import probes

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "risk_level", "evidence_ids"],
}
PROMPT = (
    "Evidence: E1 = AIS gap of 31 hours on 2025-05-02 near 57.7N 10.8E. "
    "E2 = encounter with a vessel listed on 2025-01-10. "
    "Write a one-sentence summary citing only E1 and E2, a risk level, and the evidence ids you used."
)


def vram() -> str | None:
    try:
        return subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def run(url: str | None = None, max_tokens: int = 200) -> dict:
    base = (url or config.LLAMA_SERVER_URL).rstrip("/")
    out: dict = {"server": base}
    with httpx.Client(timeout=300) as c:
        try:
            out["models"] = [m.get("id") for m in c.get(f"{base}/v1/models").json().get("data", [])]
        except Exception as e:  # noqa: BLE001
            out["models_error"] = repr(e)
        body = {
            "messages": [{"role": "system", "content": "You write terse, sourced analyst notes."},
                         {"role": "user", "content": PROMPT}],
            "temperature": 0, "max_tokens": max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": "smoke", "schema": SCHEMA}},
        }
        t0 = time.monotonic()
        r = c.post(f"{base}/v1/chat/completions", json=body)
        out["seconds"] = round(time.monotonic() - t0, 2)
        out["status"] = r.status_code
        out["vram_after"] = vram()
        if r.status_code != 200:
            out["error"] = r.text[:500]
        else:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            out["content"] = content
            out["usage"] = data.get("usage")
            timings = data.get("timings") or {}
            out["tokens_per_second"] = timings.get("predicted_per_second")
            try:
                parsed = json.loads(content)
                out["schema_ok"] = set(SCHEMA["required"]) <= set(parsed) and parsed["risk_level"] in (
                    "low", "medium", "high")
                out["cited_only_known_ids"] = set(parsed.get("evidence_ids", [])) <= {"E1", "E2"}
            except (json.JSONDecodeError, TypeError):
                out["schema_ok"] = False
    out["go"] = bool(out.get("schema_ok"))
    probes.write("llm", out)
    return out
