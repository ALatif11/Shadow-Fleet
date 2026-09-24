"""Brief generation against a local llama.cpp server, plus the batch runner.

The HTTP call is one small function so everything around it is testable without a GPU: the batch runner,
the retry, the schema validation and the verifier all run in CI against a stub.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import date
from pathlib import Path

from shadowfleet import config
from shadowfleet.briefs import bundle as bmod
from shadowfleet.briefs import verify
from shadowfleet.briefs.schema import BRIEF_SCHEMA, SYSTEM_PROMPT, Brief, render

log = logging.getLogger(__name__)

TEMPERATURE = 0.2  # phase-prompts Phase 8 task 2
MAX_TOKENS = 1200
RETRIES = 1  # one retry on a schema failure, then the brief is recorded as failed rather than faked
TOP_K_PER_CUTOFF = 50


def complete(messages: list[dict], schema: dict | None = None, url: str | None = None,
             timeout: float = 180.0) -> tuple[str, dict]:
    """One chat completion. Returns (content, usage). The only function here that touches the network."""
    import httpx

    base = (url or config.LLAMA_SERVER_URL).rstrip("/")
    payload: dict = {"messages": messages, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS}
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


def _shap_for(cutoff: date) -> dict[str, list[dict]]:
    import duckdb

    d = config.PARQUET_DIR / "shap" / f"cutoff={cutoff.isoformat()}" / "part-0.parquet"
    if not d.exists():
        return {}
    rows = duckdb.connect().execute(
        f"SELECT hull_id, features, values, contributions FROM '{d.as_posix()}'").fetchall()
    return {r[0]: [{"feature": f, "value": v, "contribution": c}
                   for f, v, c in zip(r[1], r[2], r[3], strict=True)] for r in rows}


def run_batch(cutoffs: list[date] | None = None, top_k: int = TOP_K_PER_CUTOFF,
              completer=complete, url: str | None = None) -> dict:
    """Briefs for the top-k flagged hulls at each cutoff, with the verifier run on every one."""
    import time

    from shadowfleet.features import asof
    from shadowfleet.ingest.dma import connect

    con = connect()
    cutoffs = cutoffs or config.monthly_cutoffs(config.load_window(), date.today())
    out: dict = {"cutoffs": [], "verified": [], "failed": []}
    t0 = time.time()
    for T in cutoffs:
        hulls = _flagged(T, top_k)
        if not hulls:
            out["cutoffs"].append({"cutoff": T.isoformat(), "skipped": "no flagged list"})
            continue
        shap = _shap_for(T)
        features = {r["hull_id"]: r for r in asof.features(T, con)}
        d = config.REPORTS_DIR / "briefs" / T.isoformat()
        d.mkdir(parents=True, exist_ok=True)
        done = 0
        for hull in hulls:
            if hull not in features:
                continue
            b = bmod.build(hull, T, features[hull], shap.get(hull), con)
            brief, meta = generate(b, completer=completer, url=url)
            if brief is None:
                out["failed"].append({"cutoff": T.isoformat(), "hull_id": hull, **meta})
                continue
            prose = render(brief, b)
            (d / f"{hull}.json").write_text(json.dumps({"brief": brief, "bundle": b, "meta": meta},
                                                       indent=1))
            (d / f"{hull}.md").write_text(prose)
            out["verified"].append(verify.verify(brief, b, prose))
            done += 1
        out["cutoffs"].append({"cutoff": T.isoformat(), "briefs": done, "dir": str(d)})
    out["seconds"] = round(time.time() - t0, 1)
    out["faithfulness"] = verify.aggregate(out["verified"])
    _write_audit_sheet(out["verified"])
    return out


def _write_audit_sheet(results: list[dict], n: int = 30) -> str | None:
    """`reports/audit_sheet.csv`: 30 findings for Adam, stratified by whether the verifier passed them.

    Blank human columns on purpose. The judge-versus-human agreement is the credibility number for the whole
    brief layer (ADR-10), and it means nothing if the sheet arrives pre-filled.
    """
    if not results:
        return None
    clean = [r for r in results if r["passes"]]
    dirty = [r for r in results if not r["passes"]]
    picked = (dirty[: n // 2] + clean[: n - len(dirty[: n // 2])])[:n]
    out = config.REPORTS_DIR / "audit_sheet.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=["cutoff", "hull_id", "verifier_passed", "verifier_failures",
                                           "human_verdict", "error_type", "notes"])
        wr.writeheader()
        for r in picked:
            fails = {k: v for k, v in r.items() if isinstance(v, list) and v}
            wr.writerow({"cutoff": r["cutoff"], "hull_id": r["hull_id"],
                         "verifier_passed": r["passes"], "verifier_failures": json.dumps(fails),
                         "human_verdict": "", "error_type": "", "notes": ""})
    return str(Path(out))
