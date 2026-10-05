# Portfolio material (generated 2026-10-05 by `make readme`)

Every figure is pulled from `reports/probes/`. A bullet that still shows "not measured yet" is not ready to put on a resume.

## Resume bullets

- Built a point-in-time backtested sanctions-evasion indicator model over 6605 tanker hulls transiting the Baltic exit from open AIS and watchlist data; best model `LGBM` reached precision@50 0.1815 among Russia-trade tankers not yet on any list, a median 10.9 weeks before listing, under a pre-registered evaluation.
- Engineered a leakage-tested as-of feature store (DuckDB/Parquet) with 32 features in 7 frozen families, enforced by five leakage tests that block metric reporting on failure.
- Resolved vessel identity across MMSI reassignments and renames by IMO majority vote over AIS static messages, reaching 0.8059 of MMSI-days on an IMO-based hull id, with the resolver's own hit rate reported separately from the unavoidable warm-up.
- Built the project's own detection layer on raw AIS tracks rather than consuming a vendor's: 25 ship-to-ship transfer candidates and 11357 loitering events, with ablations isolating their contribution from Global Fishing Watch's.
- Generated analyst briefs with a locally hosted LLM under schema-constrained decoding; **not measured yet** (`make briefs`) passed deterministic citation and numeric-grounding checks, with claim-level entailment **not measured yet** (`make judge`) under a cross-family judge and judge-versus-human kappa **not measured yet** (`fill reports/audit_sheet.csv`).

## The 60-second version

Tankers that carry sanctioned oil keep broadcasting AIS, because turning it off is itself a signal. I built a system that scores them on behaviour visible in that cooperative data and then asked an honest question: how many of the tankers the US, EU and UK later sanctioned would it have surfaced first, and how early?

The hard part is not the model, it is the evaluation. Sanctions lists have no historical snapshots, so I reconstructed them by replaying dated change archives. Almost every relevant tanker calls at a Russian port, so a one-line rule looks brilliant and the real question is what lift remains inside that stratum. And most features can be computed with tomorrow's data by accident, so the whole store is built as-of a cutoff and five tests refuse to report metrics if that breaks.

The limitation I lead with: ownership sits behind shell companies that free data cannot see. This measures how far behaviour alone gets you, and says where paid registries would change the answer.
