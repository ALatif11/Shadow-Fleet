# Console shell (ADR-18), Sep 17 2026

Built in the Cowork cloud sandbox while Phase 0 waits for live data. Everything shown by the console is synthetic until Phase C. No project metric exists yet.

## Decisions taken with Adam
- Audience: a local analyst tool, demoed by screen share or recording. Not hosted (GFW and OpenSanctions are non-commercial; rule 7).
- Stack: React, chosen over Streamlit for the look Adam wants ("a futuristic movie screen"); he will fine-tune the styling himself.
- First screens: vessel timeline map and ranked watchlist. Brief viewer and review/audit UI are possible later.
- Timing: freeze the data contract and build the shell now against synthetic data; wire live data after Phase 6 (Phase C).

## What was built
| path | purpose |
|---|---|
| `shadowfleet/ui_export/contract.py` | pydantic models for `manifest`, `watchlist`, `dossier`; the enforced invariants are UTC timestamps, `observed_at >= start`, rule 3 and rank order; `CONTRACT_VERSION = 1.0.0` |
| `shadowfleet/ui_export/bundle.py` | cross-file checks, atomic write (temp dir then swap; refuses to replace a non-bundle dir), read-back, schema writer |
| `shadowfleet/ui_export/fixtures.py` | deterministic synthetic bundle; see the synthetic-bundle section below |
| `shadowfleet/ui_export/export.py` | live exporter stub with a field-by-field source map; `NotReady` lists the missing phases; `thin_track` for display thinning |
| `ui/` | Vite + React 19 + TypeScript + deck.gl console (details below) |
| `ui/scripts/gen-types.mjs` | TypeScript types from the JSON Schema; `--check` fails on drift |
| `ui/src/contract/` | generated schema, types, and a committed tiny synthetic sample (280 KB) |
| `ui/src/theme.css` | every colour, font and effect token; the map reads it too |

What the console shows:
- **Top bar:** cutoff stepper, model, label set, SYNTHETIC or LIVE badge, and the hindsight toggle.
- **Watchlist:** backtest metrics strip, rank delta against the previous cutoff, a B1 filter and search. Outcomes appear only in hindsight.
- **Tactical plot:** offline Natural Earth coastline. The DMA track is bright inside the feature window. Events are colour-coded by type, with a pulsing head and a focus ring. Overlays show the Skagen anchorage and Russian ports; both are marked placeholder.
- **Dossier:** header, score and rank history, SHAP-style drivers, the identity intervals known as of the scrubber date, designations (hindsight only), and the event log.
- **Timeline:** swimlanes for identity, DMA, GFW, self-built and designation, plus the feature window, the horizon bar and the cutoff ticks. The as-of scrubber is draggable and has playback. It cannot pass the cutoff unless hindsight is on.

Point-in-time behaviour: an event appears only once its `observed_at` has passed; track points appear once received. Supervised models show "not scored" at cutoffs before any horizon closed, which matches the expanding-window rule.

Synthetic bundle (`fixtures.py`):
- **Identifiers:** hull ids `fixture:NNNN`, names starting with SYN, MMSIs in the unallocated 999xxxxxx block, null IMOs.
- **Tracks:** Route T through the Danish straits, checked against Natural Earth 10m (0 of 809,543 track points on land).
- **Voyages:** do not overlap. A Russia-trade voyage is an east-bound ballast transit, then a port visit, then a laden west-bound transit.
- **Features:** read only events observed inside (T minus 180 d, T]. Designated hulls leave the population. Labels use the 182-day horizon. Metrics are computed from the synthetic rows and are invented.

## Commands
```bash
make ui-install      # npm ci in ui/
make ui-fixtures     # SYNTHETIC bundle -> ui/public/ui_data
make ui-dev          # http://127.0.0.1:5173
make ui-schema       # after any contract change: schema, sample, TS types
make ui-check        # re-validate the bundle on disk
make ui-export       # live bundle; prints the missing phases for now
make test-all        # pytest + UI type check + vitest
```

## Measured in the sandbox (not project numbers)
- **pytest:** 67 pass after rebasing onto main at 53d0b8d (53 before this commit, plus 14 new contract tests). The full fixture build adds about 5 s to the suite. `ruff` is clean.
- **UI checks:** `npm run check` passes (types current, `tsc` clean, vitest 9 pass).
- **Production build:** `vite build` produces a 1.01 MB app chunk (288 KB gzip) and a lazily loaded 3.1 MB land chunk (830 KB gzip).
- **Full synthetic bundle:** 41 MB, 21 cutoffs, 144 watchlists, 160 dossiers of about 150 KB each. It builds in about 5 s.
- **Headless Chromium (SwiftShader, 1680×1000 and 1366×768):**
  - no page errors;
  - keyboard selection, the cutoff stepper, the hindsight clamp and release, the not-scored state and filtering all worked.

## Assumptions to confirm
- The Natural Earth 10m coastline is detailed enough for the Danish straits. If not, add a higher-resolution local coastline later; no tile server.
- The size rule: dossiers for every hull that appears in any top-100 list. The live bundle size is unknown until Phase C. ADR-18 says to revisit above about 200 MB.
- `n_name_changes` in the fixture counts every identity change. The real Phase 3 feature separates name, flag and MMSI changes.
- The feature list in the fixture is the Phase 5a draft from `phase-prompts.md`. PREREG.md freezes the real list in Phase 2, and the console reads whatever the manifest says.
- Node 22 must be installed in WSL (SETUP.md section 9). TypeScript resolved to 7.0 and Vite to 8.3 on Sep 17. If `npm ci` misbehaves on Adam's machine, pin TypeScript 5.9.

## Next
Phase 0 session 2 on Adam's machine is still the critical path (DMA deletion race). Phase C wires live data after Phase 6. Styling can be tuned at any time in `ui/src/theme.css` and `ui/src/app.css` without touching the contract.
