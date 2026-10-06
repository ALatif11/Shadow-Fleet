# Phase 9 report (generated 2026-10-06 by `make judge`)

Generator: `Gemma 4 12B instruct, Q4_K_M GGUF`. Judge: `/home/adam1/models/Qwen3-14B-Q4_K_M.gguf`. Different families by construction; the command refuses to run otherwise, because a model grading its own output agrees with its own blind spots (ADR-10).

## Claim-level entailment

- 3100 findings judged. **Entailment rate 0.8477.**
- Judge errors (server or schema): 0.

The judge sees only the records a finding cited, never the whole bundle. Given the whole bundle it can justify a claim from evidence the brief never pointed at, which is the failure being measured.

### By severity

| | entailment |
|---|---:|
| high | 0.7955 |
| low | 0.8168 |
| medium | 0.9172 |

### By cutoff

| | entailment |
|---|---:|
| 2025-03-31 | 0.8952 |
| 2025-04-30 | 0.879 |
| 2025-05-31 | 0.9028 |
| 2025-06-30 | 0.8884 |
| 2025-07-31 | 0.86 |
| 2025-08-31 | 0.8517 |
| 2025-09-30 | 0.8425 |
| 2025-10-31 | 0.8246 |
| 2025-11-30 | 0.7949 |
| 2025-12-31 | 0.8472 |
| 2026-01-31 | 0.807 |
| 2026-02-28 | 0.7803 |
| 2026-03-31 | 0.8326 |

## Judge versus human

- Not available: 0 usable rows on the audit sheet; need at least 2. This is the credibility number for the whole brief layer, so until the sheet is filled in, the entailment rate above is one model's opinion of another's.

## Assumptions to confirm

- Family detection is the first word of the model name, which is enough to catch Gemma judging Gemma and no more than that.
- A finding whose cited ids are missing from the bundle is graded `not_entailed` without asking the model; the deterministic verifier already calls that a dangling citation.
- The audit sheet is 30 findings sampled at random (seed 0), stratified by judge verdict, and blind: it shows the claim and its cited records, never the judge's verdict. Kappa is over the three verdicts (entailed, partially, not_entailed); `make kappa` recomputes it as rows are filled in.
