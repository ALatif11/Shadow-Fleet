"""The LLM judge (Phase 9 task 2) and judge-versus-human agreement.

Two rules that make the number mean something:

The judge must be a different model family from the generator (ADR-10). A model grading its own output
agrees with its own blind spots, and the resulting "97 percent entailed" says nothing. `judge_all` refuses
to run when the two names look like the same family.

The judge sees ONLY the records a finding cited, never the whole bundle. Given the whole bundle it can
justify a claim from evidence the brief never pointed at, which is precisely the failure being measured.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from shadowfleet import config

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


def _family(model_name: str) -> str:
    """Crude on purpose: the first word of the model name. Enough to catch Gemma judging Gemma."""
    return (model_name or "").strip().split()[0].lower() if model_name else ""


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
    if completer is None:
        from shadowfleet.briefs.generate import complete as completer  # noqa: N813

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


def kappa_from_sheet(path=None) -> dict:
    """Cohen's kappa between the judge and Adam on the filled audit sheet (ADR-10's credibility number).

    Rows with a blank human verdict are skipped, so the sheet can be filled in a few at a time.
    """
    import csv

    from sklearn.metrics import cohen_kappa_score

    path = path or config.REPORTS_DIR / "audit_sheet.csv"
    if not path.exists():
        return {"skipped": f"{path} missing; run `make briefs` first"}
    with open(path) as f:
        rows = [r for r in csv.DictReader(f) if (r.get("human_verdict") or "").strip()]
    if len(rows) < 2:
        return {"skipped": f"{len(rows)} of the audit sheet's rows are filled in; need at least 2"}
    human = [r["human_verdict"].strip().lower() for r in rows]
    machine = [(r.get("judge_verdict") or r.get("verifier_passed") or "").strip().lower() for r in rows]
    # the sheet carries the verifier's pass/fail until a judge column exists, so normalise both to a
    # two-way agreement question rather than pretending to grade three classes
    human_bin = ["ok" if h in ("entailed", "true", "pass", "ok", "yes") else "bad" for h in human]
    machine_bin = ["ok" if m in ("entailed", "true", "pass", "ok", "yes") else "bad" for m in machine]
    agree = sum(1 for a, b in zip(human_bin, machine_bin, strict=True) if a == b)
    if len(set(human_bin)) < 2 or len(set(machine_bin)) < 2:
        return {"n": len(rows), "raw_agreement": round(agree / len(rows), 4), "kappa": None,
                "note": "kappa is undefined when either rater used only one category"}
    return {"n": len(rows), "raw_agreement": round(agree / len(rows), 4),
            "kappa": round(float(cohen_kappa_score(human_bin, machine_bin)), 4)}
