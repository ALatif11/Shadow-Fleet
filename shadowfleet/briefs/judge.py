"""The LLM judge (Phase 9 task 2) and judge-versus-human agreement.

Two rules that make the number mean something:

The judge must be a different model family from the generator (ADR-10). A model grading its own output
agrees with its own blind spots, and the resulting "97 percent entailed" says nothing. `judge_all` refuses
to run when the two names look like the same family.

The judge sees ONLY the records a finding cited, never the whole bundle. Given the whole bundle it can
justify a claim from evidence the brief never pointed at, which is precisely the failure being measured.
"""

from __future__ import annotations

import csv
import json
import random
import re
from functools import partial
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from shadowfleet import config
from shadowfleet.briefs.generate import complete
from shadowfleet.util import probes

Verdict = Literal["entailed", "partially", "not_entailed"]


class Judgement(BaseModel):
    model_config = {"extra": "forbid"}

    verdict: Verdict
    reason: str = Field(min_length=3, max_length=300)


JUDGEMENT_SCHEMA = Judgement.model_json_schema()

SYSTEM_PROMPT = """You check whether a claim is supported by the evidence records given to you.

You see only the records the claim cited. Answer with respect to those records alone, not to what is
plausible about vessels in general.

- entailed: the records state everything the claim states.
- partially: the records support part of the claim, or support it only with an added assumption.
- not_entailed: the records do not support the claim, or the claim states something they do not.

Give one short reason naming the record field that decided it."""


def _family(model_name: str | None) -> str:
    """Crude on purpose: the first run of letters in the file or model name. Enough to catch Gemma judging
    Gemma, whether the name is `Gemma 4 12B` from config or `/home/x/models/gemma-4-12B-it.gguf` from the server.
    """
    m = re.search(r"[a-z]+", (model_name or "").replace("\\", "/").rsplit("/", 1)[-1].lower())
    return m.group() if m else ""


def served_model(url: str | None = None) -> str | None:
    """The model the server is actually running. Config says what SHOULD be loaded; this says what is."""
    try:
        data = httpx.get(f"{(url or config.LLAMA_SERVER_URL).rstrip('/')}/v1/models", timeout=10).json()
        return (data.get("data") or [{}])[0].get("id")
    except (httpx.HTTPError, ValueError):
        return None


def judge_finding(finding: dict, bundle: dict, completer, url: str | None = None) -> dict:
    """One verdict for one finding, from only the records it cited."""
    cited = {i: (bundle.get("evidence") or {}).get(i) for i in finding.get("evidence_ids") or []}
    payload = {"claim": finding.get("claim"), "evidence": {k: v for k, v in cited.items() if v}}
    if not payload["evidence"]:
        # nothing to judge against: the deterministic verifier already calls this a dangling citation
        return {"verdict": "not_entailed", "reason": "cited evidence ids are not in the bundle",
                "judged_by": "deterministic"}
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, separators=(",", ":"))}]
    try:
        content, _ = completer(messages, JUDGEMENT_SCHEMA, url)
        return {**Judgement.model_validate_json(content).model_dump(), "judged_by": "llm"}
    except Exception as exc:
        return {"verdict": None, "reason": f"{type(exc).__name__}: {exc}"[:200], "judged_by": "error"}


def judge_all(briefs: list[dict], completer=None, url: str | None = None,
              judge_model: str | None = None, generator_model: str | None = None) -> dict:
    """Judge every finding of every brief. `briefs` items are {"brief": ..., "bundle": ...}."""
    judge_model = judge_model or config.LLM_FALLBACK
    generator_model = generator_model or config.LLM_PRIMARY
    if _family(judge_model) == _family(generator_model):
        raise SystemExit(f"judge and generator are the same family ({_family(judge_model)}); ADR-10 "
                         "requires a cross-family judge, so this would measure nothing")
    completer = completer or partial(complete, temperature=0.0)  # phase-prompts Phase 9 task 2

    rows: list[dict] = []
    for item in briefs:
        brief, bundle = item["brief"], item["bundle"]
        for n, finding in enumerate(brief.get("findings") or []):
            rows.append({"hull_id": bundle.get("hull_id"), "cutoff": bundle.get("cutoff"),
                         "finding_index": n, "severity": finding.get("severity"),
                         "claim": finding.get("claim"),
                         **judge_finding(finding, bundle, completer, url)})
    return {"judge_model": judge_model, "generator_model": generator_model,
            "findings": len(rows), "rows": rows, **_rates(rows)}


def _rates(rows: list[dict]) -> dict:
    graded = [r for r in rows if r.get("verdict")]
    if not graded:
        return {"entailment_rate": None, "by_severity": {}, "by_cutoff": {}}
    def share(subset: list[dict]) -> float | None:
        return round(sum(1 for r in subset if r["verdict"] == "entailed") / len(subset), 4) if subset else None
    return {"entailment_rate": share(graded),
            "by_severity": {s: share([r for r in graded if r["severity"] == s])
                            for s in sorted({r["severity"] for r in graded if r["severity"]})},
            "by_cutoff": {c: share([r for r in graded if r["cutoff"] == c])
                          for c in sorted({r["cutoff"] for r in graded if r["cutoff"]})},
            "errors": sum(1 for r in rows if r.get("judged_by") == "error")}


AUDIT_COLUMNS = ["cutoff", "hull_id", "finding_index", "claim", "cited_evidence",
                 "human_verdict", "error_type", "notes"]
VERDICTS = ("entailed", "partially", "not_entailed")
ERROR_TYPES = ("fabricated_fact", "wrong_date", "unsupported_inference", "misattributed_evidence", "none")


def _sheet_path():
    return config.REPORTS_DIR / "audit_sheet.csv"


def _read_sheet() -> list[dict]:
    if not _sheet_path().exists():
        return []
    with open(_sheet_path()) as f:
        return list(csv.DictReader(f))


def write_audit_sheet(rows: list[dict], briefs: list[dict], n: int = 30, seed: int = 0) -> str:
    """`reports/audit_sheet.csv`: n findings sampled at random, stratified by judge verdict (Phase 9 task 3).

    Blind on purpose: the sheet shows the claim and the records it cited, never the judge's verdict, so
    Adam's verdict cannot anchor on it. `kappa_from_sheet` joins the judge back in from the probe file.
    A sheet with any human verdict filled in is never overwritten.
    """
    if any((r.get("human_verdict") or "").strip() for r in _read_sheet()):
        return "kept: the audit sheet already has human verdicts"
    rng = random.Random(seed)
    graded = [r for r in rows if r.get("verdict")]
    strata = {v: [r for r in graded if r["verdict"] == v] for v in VERDICTS}
    picked: list[dict] = []
    for v in VERDICTS:  # an even share from each verdict, topped up from what is left
        picked += rng.sample(strata[v], min(len(strata[v]), n // len(VERDICTS)))
    rest = [r for r in graded if r not in picked]
    picked += rng.sample(rest, min(len(rest), n - len(picked)))
    rng.shuffle(picked)  # so the strata are not readable from the row order
    evidence = {(b["bundle"]["cutoff"], b["bundle"]["hull_id"]): b["bundle"].get("evidence") or {}
                for b in briefs}
    findings = {(b["bundle"]["cutoff"], b["bundle"]["hull_id"]): b["brief"].get("findings") or []
                for b in briefs}
    _sheet_path().parent.mkdir(parents=True, exist_ok=True)
    with open(_sheet_path(), "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=AUDIT_COLUMNS)
        wr.writeheader()
        for r in picked:
            key = (r["cutoff"], r["hull_id"])
            ids = findings[key][r["finding_index"]].get("evidence_ids") or []
            wr.writerow({"cutoff": r["cutoff"], "hull_id": r["hull_id"], "finding_index": r["finding_index"],
                         "claim": r["claim"],
                         "cited_evidence": json.dumps({i: evidence[key].get(i) for i in ids}),
                         "human_verdict": "", "error_type": "", "notes": ""})
    return f"wrote {len(picked)} findings"


def kappa_from_sheet(judge_rows: list[dict] | None = None) -> dict:
    """Cohen's kappa between the judge and Adam over the three verdicts (ADR-10's credibility number).

    Rows with a blank human verdict are skipped, so the sheet can be filled in a few at a time. The judge's
    verdicts come from the `judge` probe, matched on (cutoff, hull, finding).
    """
    from sklearn.metrics import cohen_kappa_score

    if not _sheet_path().exists():
        return {"skipped": "audit_sheet.csv missing; run `make judge` first"}
    if judge_rows is None:
        judge_rows = (probes.read("judge") or {}).get("rows") or []
    machine = {(r["cutoff"], r["hull_id"], str(r["finding_index"])): r.get("verdict") for r in judge_rows}
    filled = [r for r in _read_sheet() if (r.get("human_verdict") or "").strip()]
    bad = [r["human_verdict"] for r in filled if r["human_verdict"].strip().lower() not in VERDICTS]
    pairs = [(r["human_verdict"].strip().lower(), machine.get((r["cutoff"], r["hull_id"], r["finding_index"])))
             for r in filled if r["human_verdict"].strip().lower() in VERDICTS]
    pairs = [(h, m) for h, m in pairs if m]
    if len(pairs) < 2:
        return {"skipped": f"{len(pairs)} usable rows on the audit sheet; need at least 2",
                "invalid_human_verdicts": bad}
    human, judge_v = zip(*pairs, strict=True)
    agree = sum(1 for h, m in pairs if h == m)
    out = {"n": len(pairs), "raw_agreement": round(agree / len(pairs), 4), "invalid_human_verdicts": bad}
    if len(set(human)) < 2 or len(set(judge_v)) < 2:
        return {**out, "kappa": None, "note": "kappa is undefined when either rater used only one category"}
    return {**out, "kappa": round(float(cohen_kappa_score(human, judge_v, labels=list(VERDICTS))), 4)}
