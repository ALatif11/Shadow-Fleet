"""Brief generation against a local llama.cpp server, plus the batch runner.

The HTTP call is one small function so everything around it is testable without a GPU: the batch runner,
the retry, the schema validation and the verifier all run in CI against a stub.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import time
from datetime import date
from pathlib import Path

import httpx

from shadowfleet import config
from shadowfleet.backtest import explain
from shadowfleet.briefs import bundle as bmod
from shadowfleet.briefs import verify
from shadowfleet.briefs.llm_smoke import vram
from shadowfleet.briefs.schema import BRIEF_SCHEMA, SYSTEM_PROMPT, Brief, render
from shadowfleet.features import asof
from shadowfleet.ingest.dma import connect

log = logging.getLogger(__name__)

TEMPERATURE = 0.2  # phase-prompts Phase 8 task 2
MAX_TOKENS = 1200
RETRIES = 1  # one retry on a schema failure, then the brief is recorded as failed rather than faked
TOP_K_PER_CUTOFF = 50


def complete(messages: list[dict], schema: dict | None = None, url: str | None = None,
             timeout: float = 180.0, temperature: float = TEMPERATURE) -> tuple[str, dict]:
    """One chat completion. Returns (content, usage). The only function here that touches the network."""

    base = (url or config.LLAMA_SERVER_URL).rstrip("/")
    # Thinking off: Gemma 4 and Qwen3 think by default, the thinking eats the token budget, and the answer
    # (content) comes back empty. Server-side `--reasoning off` does the same; this keeps it per request.
    payload: dict = {"messages": messages, "temperature": temperature, "max_tokens": MAX_TOKENS,
                     "chat_template_kwargs": {"enable_thinking": False}}
    if schema:
        # llama.cpp constrains generation to the schema, which is what makes a retry rare rather than normal
        payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "brief",
                                                                            "schema": schema, "strict": True}}
    r = httpx.post(f"{base}/v1/chat/completions", json=payload, timeout=timeout)
    r.raise_for_status()
    body = r.json()
    return body["choices"][0]["message"]["content"], body.get("usage") or {}


def generate(bundle: dict, completer=complete, url: str | None = None) -> tuple[dict | None, dict]:
    """A validated brief, or (None, meta) after the retry. Never returns unvalidated model output."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(bundle, separators=(",", ":"))}]
    meta: dict = {"attempts": 0, "usage": {}, "errors": []}
    for _ in range(RETRIES + 1):
        meta["attempts"] += 1
        try:
            content, usage = completer(messages, BRIEF_SCHEMA, url)
            meta["usage"] = usage
            return Brief.model_validate_json(content).model_dump(), meta
        except Exception as exc:  # a schema failure, a refusal, or a dead server: all retried once
            meta["errors"].append(f"{type(exc).__name__}: {exc}"[:300])
    return None, meta


def _flagged(cutoff: date, top_k: int) -> list[str]:
    path = config.REPORTS_DIR / f"flagged_{cutoff.isoformat()}.csv"
    if not path.exists():
        return []
    with open(path) as f:
        return [r["hull_id"] for r in list(csv.DictReader(f))[:top_k]]


def run_batch(cutoffs: list[date] | None = None, top_k: int = TOP_K_PER_CUTOFF,
              completer=complete, url: str | None = None) -> dict:
    """Briefs for the top-k flagged hulls at each cutoff, with the verifier run on every one."""


    con = connect()
    cutoffs = cutoffs or config.monthly_cutoffs(config.load_window(), date.today())
    out: dict = {"cutoffs": [], "verified": [], "failed": [], "resumed": 0}
    t0 = time.time()
    gen_seconds, gen_tokens = 0.0, 0
    for T in cutoffs:
        hulls = _flagged(T, top_k)
        if not hulls:
            out["cutoffs"].append({"cutoff": T.isoformat(), "skipped": "no flagged list"})
            continue
        shap = explain.read_shap(T)
        features = {r["hull_id"]: r for r in asof.features(T, con)}
        d = config.REPORTS_DIR / "briefs" / T.isoformat()
        d.mkdir(parents=True, exist_ok=True)
        done = 0
        for hull in hulls:
            if hull not in features:
                continue
            path = d / f"{hull}.json"
            b = bmod.build(hull, T, features[hull], shap.get(hull), con)
            key = hashlib.sha256((SYSTEM_PROMPT + json.dumps(b, sort_keys=True)).encode()).hexdigest()
            saved = json.loads(path.read_text()) if path.exists() and (d / f"{hull}.md").exists() else None
            if saved and saved["meta"].get("key") == key:
                # resumable: a crash or Ctrl+C costs nothing already done. A changed bundle or prompt does
                # not match the key, so the brief is regenerated rather than verified against stale inputs.
                out["verified"].append(verify.verify(saved["brief"], saved["bundle"],
                                                     (d / f"{hull}.md").read_text()))
                out["resumed"] += 1
                done += 1
                continue
            t = time.time()
            brief, meta = generate(b, completer=completer, url=url)
            gen_seconds += time.time() - t
            gen_tokens += int((meta.get("usage") or {}).get("completion_tokens") or 0)
            meta["key"] = key
            if brief is None:
                out["failed"].append({"cutoff": T.isoformat(), "hull_id": hull, **meta})
                continue
            prose = render(brief, b)
            path.write_text(json.dumps({"brief": brief, "bundle": b, "meta": meta}, indent=1))
            (d / f"{hull}.md").write_text(prose)
            out["verified"].append(verify.verify(brief, b, prose))
            done += 1
        out["cutoffs"].append({"cutoff": T.isoformat(), "briefs": done, "dir": str(d)})
    out["seconds"] = round(time.time() - t0, 1)
    out["tokens_per_second"] = round(gen_tokens / gen_seconds, 1) if gen_tokens and gen_seconds else None
    out["vram"] = vram()  # llama.cpp allocates weights and KV cache at start, so in-use is the peak
    out["faithfulness"] = verify.aggregate(out["verified"])
    dirs = [c["dir"] for c in out["cutoffs"] if c.get("briefs")]
    # three cutoffs spread across the window, one brief each, for Adam to read (paths only: rule 7)
    out["samples"] = [str(sorted(Path(d).glob("*.md"))[0]) for d in dirs[::max(1, len(dirs) // 3)][:3]]
    return out
