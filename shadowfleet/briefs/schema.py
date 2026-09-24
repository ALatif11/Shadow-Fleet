"""The JSON schema the brief generator is constrained to, and the Markdown renderer.

Schema-first (ADR-10): the model fills a structure whose every claim carries evidence ids, and the prose is
rendered from that. Free prose asked to "cite carefully" is not checkable; this is.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

# the pattern belongs to the element, not to the list: `pattern` on a list[str] field is a type error
EvidenceId = Annotated[str, StringConstraints(pattern=r"^E[0-9]+$")]

Severity = Literal["low", "medium", "high"]
RiskLevel = Literal["low", "medium", "high"]


class Finding(BaseModel):
    """One claim and the evidence ids that support it. `evidence_ids` is what makes a brief checkable."""

    model_config = {"extra": "forbid"}

    claim: str = Field(min_length=10, max_length=400)
    evidence_ids: list[EvidenceId] = Field(min_length=1, max_length=6)
    severity: Severity


class Brief(BaseModel):
    """Pydantic rather than a hand-written JSON Schema: pydantic is already a core dependency and is what
    the UI contract uses, and `model_json_schema()` gives llama.cpp exactly the schema it constrains on."""

    model_config = {"extra": "forbid"}

    summary: str = Field(min_length=20, max_length=900)
    risk_level: RiskLevel
    findings: list[Finding] = Field(min_length=1, max_length=8)
    caveats: list[str] = Field(default_factory=list, max_length=5)


BRIEF_SCHEMA = Brief.model_json_schema()

SYSTEM_PROMPT = """You are a sanctions-risk analyst writing a short brief about one vessel.

Rules you must follow:
1. State only facts that appear in the evidence bundle. Do not add context, history or inference about the
   vessel, its owner, its cargo or its trade that is not in the bundle.
2. Every finding must cite at least one evidence id from the bundle, and must be supported by the records
   those ids name.
3. Do not name any vessel, company, person or port that does not appear in the bundle.
4. Every date, number, duration and position in your text must appear in the bundle.
5. This is an indicator score, not an accusation. Say what the data shows, not what it proves.
6. If the evidence is thin, say so in the caveats and set risk_level accordingly. A short honest brief is
   correct; padding it is not."""


def render(brief: dict, bundle: dict) -> str:
    """JSON -> Markdown with inline [E#] citations and an appendix of the records actually cited."""
    lines = [f"# {bundle['hull_id']} as of {bundle['cutoff']}", "",
             f"**Risk level: {brief.get('risk_level', 'unknown')}**", "",
             brief.get("summary", ""), "", "## Findings", ""]
    cited: list[str] = []
    for f in brief.get("findings") or []:
        ids = f.get("evidence_ids") or []
        cited += [i for i in ids if i not in cited]
        marks = " ".join(f"[{i}]" for i in ids)
        lines.append(f"- **{f.get('severity', '?')}** — {f.get('claim', '')} {marks}")
    if brief.get("caveats"):
        lines += ["", "## Caveats", ""] + [f"- {c}" for c in brief["caveats"]]
    if bundle.get("truncated_families"):
        lines += ["", f"- Evidence families trimmed to fit the context budget: "
                      f"{', '.join(bundle['truncated_families'])}."]
    lines += ["", "## Evidence cited", ""]
    for i in cited:
        rec = bundle.get("evidence", {}).get(i)
        lines.append(f"- **{i}** — " + (", ".join(f"{k}: {v}" for k, v in rec.items() if v is not None)
                                        if rec else "**missing from the bundle**"))
    return "\n".join(lines) + "\n"
