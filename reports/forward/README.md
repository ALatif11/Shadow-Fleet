# Forward test (Phase F, ADR-17)

Each row is a ranked top-50 committed before any of its outcomes were known. The SHA-256 is recorded here and the git commit timestamp is the evidence. Nothing in this directory is ever edited; `make forward-eval` is read-only and refuses to run if a hash no longer matches.

| scored at | file | model | population | sha256 |
|---|---|---|---:|---|
| 2026-10-01 | `top50_2026-10-01.csv` | LGBM | 2916 | `f64527a59ad40df8f11071c91de0053169b781465d1bccd9a92ab21ddb38a7f5` |

**2026-10-02, before the second list was scored and before any outcome is known.** The 2026-10-01 list is
void as a prediction. Early stopping watched logloss, which `scale_pos_weight` deliberately distorts, so on a
low-base-rate validation cutoff it kept a single tree: all 50 scores are identical (0.125392), the order is
just row order, and no drivers were written. It stays here unedited with its hash intact and will be
evaluated and reported next to the primary list. **The primary forward test is the list scored 2026-10-02**
with early stopping on average precision (PREREG section 12), declared here before it exists.

| scored at | file | model | population | sha256 |
|---|---|---|---:|---|
| 2026-10-02 | `top50_2026-10-02.csv` | LGBM | 2909 | `2e1ff25872e7a2ba14587d39f886156736b00143a6be377b00f15e3247b127f5` |
