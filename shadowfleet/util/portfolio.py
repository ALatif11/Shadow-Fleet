"""Phase 10: the README's results half and the portfolio bullets, generated from `reports/probes/`.

The rule this module exists to enforce is CLAUDE.md rule 4: no number reaches the README unless a probe
file produced it. Anything not yet measured renders as an explicit marker naming the command that would
measure it, so a half-finished README reads as half-finished instead of as a modest result.
"""

from __future__ import annotations

from datetime import date

from shadowfleet import config
from shadowfleet.util import probes

MISSING = "not measured yet"


def _n(value, command: str) -> str:
    """A measured value, or a marker that names the command that would produce it."""
    return str(value) if value not in (None, "", []) else f"**{MISSING}** (`{command}`)"


def _primary(backtest: dict | None) -> dict:
    """The pre-registered headline row: best model on precision@50, B1 stratum, union label."""
    rows = [a for a in ((backtest or {}).get("aggregate") or [])
            if a.get("label_set") == "union" and a.get("stratum") == "b1"
            and a.get("precision_at_50") is not None]
    return max(rows, key=lambda a: a["precision_at_50"]) if rows else {}


def render_readme() -> str:
    win = probes.read("window") or {}
    labels = probes.read("labels") or {}
    ident = probes.read("identity") or {}
    det = probes.read("detect") or {}
    feats = probes.read("features") or {}
    back = probes.read("backtest") or {}
    briefs = probes.read("briefs") or {}
    judge = probes.read("judge") or {}
    best = _primary(back)
    lead = (back.get("lead_time") or {}).get(best.get("model") or "", {})

    out = [
        "# Shadow Fleet", "",
        "Evasion-indicator scoring for tankers from cooperative data (terrestrial AIS and open "
        "watchlists), with a point-in-time backtest. The question it measures: how many tankers later "
        "listed by OFAC, the EU or the UK would this system have ranked highly *before* they were listed, "
        "and how many weeks early?", "",
        "It is not dark-fleet detection. Every input is cooperative, and the evaluation is pre-registered "
        "in `PREREG.md`, committed before any label existed. Every number below is generated from "
        "`reports/` by `make readme`; nothing here is typed by hand.", "",
        f"*Generated {date.today().isoformat()}.*", "",
        "## Headline result", "",
    ]
    if best:
        out += [f"On the pre-registered primary endpoint (precision@50 within the Russia-port stratum, "
                f"label = OFAC ∪ EU ∪ UK, macro-averaged over {best.get('cutoffs')} monthly cutoffs), the "
                f"best model is **`{best.get('model')}` at precision@50 {best.get('precision_at_50')}** "
                f"(PR-AUC {best.get('pr_auc')}, recall@50 {best.get('recall_at_50')}).", "",
                "Median lead time for hulls it flagged before designation: "
                + (f"**{lead['median_weeks']} weeks**" if lead.get("median_weeks") is not None
                   else _n(None, "make backtest"))
                + f" ({lead.get('flagged_before_designation', 0)} of "
                  f"{lead.get('designated_in_window', 0)} designated hulls flagged in time).", "",
                "Full tables: `reports/phase5b.md`, `reports/phase6.md`, "
                "`reports/metrics_by_cutoff.csv`."]
    else:
        out += [f"{_n(None, 'make backtest')}. The harness runs; no cutoff has produced a scored model "
                "yet."]
    out += ["", "## What was built, and what it measured", "",
            "| stage | measured | source |", "|---|---|---|",
            f"| Window | {_n(win.get('months'), 'make window-gate')} months, "
            f"{_n(win.get('evaluable_cutoffs'), 'make window-gate')} evaluable cutoffs | "
            "`config/window.json` |",
            f"| Labels | {_n((labels.get('table') or {}).get('rows'), 'make labels')} dated sanctions "
            f"actions | `reports/phase2.md` |",
            f"| Identity | {_n((ident.get('coverage') or {}).get('share_by_imo'), 'make identity')} of "
            f"MMSI-days resolved to an IMO-based hull id | `reports/phase3.md` |",
            f"| Self-built detectors | {_n((det.get('sts') or {}).get('candidates'), 'make detect')} STS "
            f"candidates, {_n((det.get('loitering') or {}).get('events'), 'make detect')} loitering "
            f"events | `reports/phase4b.md` |",
            f"| Feature store | {_n(feats.get('features'), 'make features')} features in "
            f"{len(feats.get('families') or []) or _n(None, 'make features')} frozen families | "
            "`reports/phase5a.md` |",
            f"| Briefs | {_n((briefs.get('faithfulness') or {}).get('briefs'), 'make briefs')} generated, "
            f"{_n((briefs.get('faithfulness') or {}).get('share_clean'), 'make briefs')} with zero "
            f"verifier failures | `reports/phase8.md` |",
            f"| Judge | entailment {_n(judge.get('entailment_rate'), 'make judge')}, kappa vs human "
            f"{_n((judge.get('kappa') or {}).get('kappa'), 'fill reports/audit_sheet.csv')} | "
            "`reports/phase9.md` |", "",
            "## How the point-in-time claim is enforced", "",
            "`features(hull_id, T)` may only read records with `observed_at <= T`. Five tests hold that "
            "claim up, and `make backtest` runs them before it reports anything:", "",
            "1. Features at T from the live store equal features at T from a store whose later partitions "
            "are physically deleted and whose derived tables are rebuilt from what is left.",
            "2. Static analysis: nothing under `features/` imports label construction, no feature name "
            "mentions sanctions except the partner feature, no GFW registry ownership field is read.",
            "3. Permutation: shuffling the training labels drops PR-AUC to the base rate.",
            "4. Reverse time: training on later cutoffs and scoring earlier ones is not dramatically "
            "better than forward.",
            "5. Entity-resolution sensitivity: no hull id depends on a GFW merge, so the delta is zero by "
            "construction and the check fails if that ever stops being true.", "",
            "## Limitations, stated up front", "",
            "- Most Baltic tankers later designated were calling at Russian ports, so a one-line rule has "
            "large lift. The headline is therefore lift **within** that stratum, not against the whole "
            "population (R3).",
            "- Hundreds of tankers were EU or UK-listed before OFAC designated them, so an OFAC-only label "
            "would be trivially predictable from the other lists. The population excludes anything already "
            "listed by any of the three, and OFAC-only is a sensitivity table (R12).",
            "- Many designations follow ownership or price-cap reasons rather than observable behaviour, "
            "so recall has a ceiling behaviour-based scoring cannot pass (R9).",
            "- Ownership through shell companies is invisible in free data. The project quantifies how far "
            "behaviour alone gets you and says where paid registries would change the answer (R8).",
            "- Feature design was informed by public reporting through 2026, so it is not blind to the "
            "test period. `PREREG.md` limits what could be tuned after results were seen; it cannot make "
            "the design blind.",
            "- Lead time is bounded by the 182-day horizon and right-censored at the last cutoff.", "",
            "## Licences and what is published", "",
            "Global Fishing Watch and OpenSanctions data are non-commercial only (OpenSanctions: "
            "CC BY-NC 4.0). This repo publishes code, aggregate tables and figures. It never contains "
            "GFW-derived tables, OpenSanctions bulk files or raw AIS; `data/` and `reports/probes/` are "
            "gitignored. Nothing is scraped from Equasis or any site whose terms forbid it.", "",
            "## Run it", "", "```bash", "make setup && make doctor && make test", "make all",
            "```", "",
            "`SETUP.md` covers WSL2, uv and llama.cpp. `shadow-fleet-plan.md` has the architecture, the "
            "ranked risks and every ADR. `phase-prompts.md` is the build plan.", ""]
    return "\n".join(out)


def render_portfolio() -> str:
    back = probes.read("backtest") or {}
    best = _primary(back)
    lead = (back.get("lead_time") or {}).get(best.get("model") or "", {})
    ident = probes.read("identity") or {}
    feats = probes.read("features") or {}
    briefs = probes.read("briefs") or {}
    judge = probes.read("judge") or {}
    det = probes.read("detect") or {}
    ing = probes.read("phase1") or {}

    return "\n".join([
        f"# Portfolio material (generated {date.today().isoformat()} by `make readme`)", "",
        "Every figure is pulled from `reports/probes/`. A bullet that still shows "
        f"\"{MISSING}\" is not ready to put on a resume.", "",
        "## Resume bullets", "",
        "- " + (f"Built a point-in-time backtested sanctions-evasion indicator model over "
               f"{_n((ing.get('population') or {}).get('mmsi_ever_tanker_class'), 'make phase1')} tanker "
               f"hulls transiting the Baltic exit from open AIS and watchlist data; best model "
               f"`{best.get('model', MISSING)}` reached precision@50 "
               f"{_n(best.get('precision_at_50'), 'make backtest')} among Russia-trade tankers not yet on "
               f"any list, a median "
               f"{_n(lead.get('median_weeks'), 'make backtest')} weeks before listing, under a "
               f"pre-registered evaluation."),
        "- " + (f"Engineered a leakage-tested as-of feature store (DuckDB/Parquet) with "
               f"{_n(feats.get('features'), 'make features')} features in "
               f"{len(feats.get('families') or []) or '?'} frozen families, enforced by five leakage "
               f"tests that block metric reporting on failure."),
        "- " + (f"Resolved vessel identity across MMSI reassignments and renames by IMO majority vote over "
               f"AIS static messages, reaching "
               f"{_n((ident.get('coverage') or {}).get('share_by_imo'), 'make identity')} of MMSI-days on "
               f"an IMO-based hull id, with the resolver's own hit rate reported separately from the "
               f"unavoidable warm-up."),
        "- " + (f"Built the project's own detection layer on raw AIS tracks rather than consuming a "
               f"vendor's: {_n((det.get('sts') or {}).get('candidates'), 'make detect')} ship-to-ship "
               f"transfer candidates and {_n((det.get('loitering') or {}).get('events'), 'make detect')} "
               f"loitering events, with ablations isolating their contribution from Global Fishing Watch's."),
        "- " + (f"Generated analyst briefs with a locally hosted LLM under schema-constrained decoding; "
               f"{_n((briefs.get('faithfulness') or {}).get('share_clean'), 'make briefs')} passed "
               f"deterministic citation and numeric-grounding checks, with claim-level entailment "
               f"{_n(judge.get('entailment_rate'), 'make judge')} under a cross-family judge and "
               f"judge-versus-human kappa "
               f"{_n((judge.get('kappa') or {}).get('kappa'), 'fill reports/audit_sheet.csv')}."), "",
        "## The 60-second version", "",
        "Tankers that carry sanctioned oil keep broadcasting AIS, because turning it off is itself a "
        "signal. I built a system that scores them on behaviour visible in that cooperative data and then "
        "asked an honest question: how many of the tankers the US, EU and UK later sanctioned would it "
        "have surfaced first, and how early?", "",
        "The hard part is not the model, it is the evaluation. Sanctions lists have no historical "
        "snapshots, so I reconstructed them by replaying dated change archives. Almost every relevant "
        "tanker calls at a Russian port, so a one-line rule looks brilliant and the real question is what "
        "lift remains inside that stratum. And most features can be computed with tomorrow's data by "
        "accident, so the whole store is built as-of a cutoff and five tests refuse to report metrics if "
        "that breaks.", "",
        "The limitation I lead with: ownership sits behind shell companies that free data cannot see. "
        "This measures how far behaviour alone gets you, and says where paid registries would change the "
        "answer.", ""])


def write(path=None) -> dict:
    readme = path or config.REPO_ROOT / "README.md"
    readme.write_text(render_readme())
    portfolio = config.REPORTS_DIR / "portfolio.md"
    portfolio.parent.mkdir(parents=True, exist_ok=True)
    portfolio.write_text(render_portfolio())
    text = readme.read_text() + portfolio.read_text()
    return {"readme": str(readme), "portfolio": str(portfolio),
            "unmeasured": text.count(MISSING)}
