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

## Design system (Sep 17, second session)
Adam supplied the design system from a production app of his. Applied as decided with him: the dark HUD stays, the
system's colour *roles* are mapped onto dark surfaces, and everything else is followed as written.

- `ui/src/theme.css` is the only file with a colour, duration, easing curve, radius or shadow. Motion is
  `--ease-out` with `--t-fast` (0.14 s) and `--t-med` (0.22 s); one entrance keyframe, `view-in`, is reused by
  panels, the watchlist body, the dossier card and tooltips.
- Every interactive element transitions background, border, colour, transform and shadow together, presses with
  `scale(0.98)`, and shows a 3 px `--accent-500` focus ring. Touch targets reach 44 px under `pointer: coarse`.
  Body text is 16 px and inputs exactly 16 px; small uppercase labels are 0.72 rem at 0.05 em.
- Removed: backdrop blur on panels, every surface gradient, the scanline sweep, neon text-shadows and
  drop-shadow filters on the map. Resting cards have a 10 px radius, a 1 px `--line` border and no shadow.
  Elevation is limited to floating surfaces (tooltips) and the accent lift.
- Kept, as Adam asked: a matte accent glow (`--glow-matte`, 14 percent at 22 px blur) on the brand mark, and the
  two motions that carry state — the last-known-position ring on the map and the SYNTHETIC badge breathe.

Deliberate deviations, all recorded in ADR-18:
1. Shadows are tinted with black, not `--ink`; on a dark base an ink-tinted shadow glows.
2. The nine event colours are a categorical data scale, exempt from "one accent only", and the destructive red
   (`--danger`) is reserved and never used for data. Chrome uses the single accent.
3. Looping status indicators (spinner, badge breathe, position ring) run longer than 0.25 s because a continuous
   indicator has to. Nothing that responds to input does.

`ui/src/design-system.test.ts` enforces the system: no duration, easing curve or literal colour outside
theme.css; WCAG AA contrast on eleven token pairs and 3:1 for every event colour on the map; one keyframe;
the reduced-motion guard; press and focus rules; the 44 px rule; no icon-only button without a label; no
backdrop blur, surface gradient or resting card shadow. It caught two real bugs when first run: `outline: none`
on the cutoff select and the search input was out-specifying the focus ring, so neither control had one.

Accessibility work that came with it: watchlist rows are now buttons with roving tabindex (one tab stop for the
list, not 100), the timeline scrubber is a labelled `role="slider"` with live `aria-valuetext`, the play button
and stepper arrows have `aria-label`s, and the boot message is a live region.

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
- **UI checks:** `npm run check` passes (types current, `tsc` clean, vitest 31 pass: 9 contract, 22 design system).
- **Production build:** `vite build` produces a 1.01 MB app chunk (288 KB gzip) and a lazily loaded 3.1 MB land chunk (830 KB gzip).
- **Full synthetic bundle:** 41 MB, 21 cutoffs, 144 watchlists, 160 dossiers of about 150 KB each. It builds in about 5 s.
- **Headless Chromium (SwiftShader, 1680×1000 and 1366×768):**
  - no page errors;
  - keyboard selection, the cutoff stepper, the hindsight clamp and release, the not-scored state and filtering all worked;
  - every tab stop reports a 3 px focus ring; no element uses backdrop blur or a gradient; five elements use the
    single entrance animation; transitions measure 0.14 s and collapse to 0.001 s under emulated reduced motion.

## Assumptions to confirm
- The Natural Earth 10m coastline is detailed enough for the Danish straits. If not, add a higher-resolution local coastline later; no tile server.
- The size rule: dossiers for every hull that appears in any top-100 list. The live bundle size is unknown until Phase C. ADR-18 says to revisit above about 200 MB.
- `n_name_changes` in the fixture counts every identity change. The real Phase 3 feature separates name, flag and MMSI changes.
- The feature list in the fixture is the Phase 5a draft from `phase-prompts.md`. PREREG.md freezes the real list in Phase 2, and the console reads whatever the manifest says.
- Node 22 must be installed in WSL (SETUP.md section 9). TypeScript resolved to 7.0 and Vite to 8.3 on Sep 17. If `npm ci` misbehaves on Adam's machine, pin TypeScript 5.9.

## Next
Phase 0 session 2 on Adam's machine is still the critical path (DMA deletion race). Phase C wires live data after Phase 6. Styling can be tuned at any time in `ui/src/theme.css` and `ui/src/app.css` without touching the contract.

---

## Rebased onto main, Sep 27 2026 (and what was wrong with the branch)

The console was built in a session whose repo history no longer exists; `ui-shell.bundle` was the only copy
and its base commit (53d0b8d) is not an ancestor of anything on main, so it could not be fetched. It was
brought over as a tree instead: `ui/`, `shadowfleet/ui_export/`, `tests/test_ui_contract.py` and this report
came across whole, and the six shared files (Makefile, SETUP.md, README.md, .gitignore, `cli.py`,
`config.py`) were re-applied by hand against current main. The branch's edits to CLAUDE.md,
`shadow-fleet-plan.md` and `phase-prompts.md` were taken as content (rule 11, ADR-18, Phase C) rather than
as a diff, because main had rewritten all three since.

**Finding: the branch was incomplete and its own check had never passed in the repo.**
`ui/src/data/{api,asof,hooks}.ts` were never committed. They existed in that session's working tree, which
is why the report above records `npm run check` passing and a clean headless run: both were true of the
working tree and false of the commit. `tsc` on the recovered tree gave 12 errors, all rooted in those three
missing modules. They were rewritten from their call sites and the Python contract:

- `data/asof.ts` — the point-in-time arithmetic. Every filter keys on `observed_at`, never on `start`, so an
  event that happened in June but was reported in August stays invisible at a July cutoff. `trackCount` is a
  binary search because the scrubber calls it per animation frame, and `segments` breaks a track on any AIS
  gap over 6 h so deck.gl does not draw a straight line across the Baltic.
- `data/api.ts` — file reads over HTTP, no API server. `fileKey` and `watchlistFile` mirror `contract.py`;
  `loadManifest` refuses a bundle whose `contract_version` differs from the generated types rather than
  rendering a stale shape field by field.
- `data/hooks.ts` — one cache per file kind, keyed by URL, so `[` and `]` walk back over cutoffs already
  fetched. A stale response cannot win a race with a newer one.

**Process rule this earns:** a phase report may only quote a check that was run against `git stash -u`-clean
state, or `git status` must be pasted beside it. An untracked file makes a report true locally and false in
the repo, and the next person to check out the branch is the one who finds out.

### Measured after the rebase (sandbox, still not project numbers)
- **pytest:** 206 pass, 0 skipped. `tests/test_ui_contract_sync.py` was skipping on main for want of
  `ui_export` and now runs (3 tests); `tests/test_ui_contract.py` adds 17. `ruff` clean.
- **UI checks:** `npm run check` passes — types current, `tsc` clean, vitest 34 pass (9 contract, 22 design
  system, 8 new as-of tests, one of which caught a millisecond-versus-second slip while being written).
- **Production build:** `vite build` clean; 1.01 MB app chunk (289 KB gzip), 3.09 MB land chunk lazily loaded.
- **Tiny synthetic bundle:** 232 KB, 3 cutoffs, 24 watchlists, 6 dossiers; `make ui-check` re-validates it.
- **Not re-verified:** the headless render. Chromium launches in this sandbox but hangs on `goto` with
  `--use-gl=swiftshader`, and chasing it further was not worth the budget. `make ui-dev` on Adam's machine
  answers it in under a minute, and until it has, treat the render claims in the section above as belonging
  to the pre-rebase tree.

Also fixed while verifying: `tests/test_portfolio.py` called `portfolio.write()` with no path, and
`portfolio.write()` defaults to `REPO_ROOT / "README.md"`, so **running the test suite rewrote the repo's
tracked README**. `tmp_data` now redirects `REPO_ROOT` as well, and a new test fails if README.md grows a
`##` section that `render_readme` does not produce — which is what silently ate the console section on the
first attempt at this rebase.
