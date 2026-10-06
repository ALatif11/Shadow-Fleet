"""Phase 10: the README's results half and the portfolio bullets, generated from `reports/probes/`.

The rule this module exists to enforce is CLAUDE.md rule 4: no number reaches the README unless a probe
file produced it. Anything not yet measured renders as an explicit marker naming the command that would
measure it, so a half-finished README reads as half-finished instead of as a modest result.
"""

from __future__ import annotations

import re
from datetime import date

from shadowfleet import config
from shadowfleet.util import probes

MISSING = "not measured yet"


def _n(value, command: str) -> str:
    """A measured value, or a marker that names the command that would produce it."""
    return str(value) if value not in (None, "", []) else f"**{MISSING}** (`{command}`)"


PRIMARY_MODEL = "LGBM"  # PREREG section 4: the headline is this model, not whichever model scored best


def _b1_union(backtest: dict | None, key: str) -> list[dict]:
    return [a for a in ((backtest or {}).get(key) or [])
            if a.get("label_set") == "union" and a.get("stratum") == "b1" and a.get("precision_at_50") is not None]


def _primary(backtest: dict | None) -> dict:
    """The pre-registered primary model's row on the pre-registered endpoint (union label, B1 stratum).

    Matched cutoffs when the harness wrote them: the supervised models skip the early, high-base-rate
    cutoffs, so an all-cutoff average is not comparable across models. Picking the best row instead would
    let a model nobody pre-registered become the headline after the fact.
    """
    rows = _b1_union(backtest, "aggregate_matched") or _b1_union(backtest, "aggregate")
    return next((a for a in rows if a["model"] == PRIMARY_MODEL), {})


def _comparison(backtest: dict | None) -> list[str]:
    """Every model on the same cutoffs, best first, with lift over the random baseline."""
    rows = _b1_union(backtest, "aggregate_matched") or _b1_union(backtest, "aggregate")
    if not rows:
        return []
    base = next((a["precision_at_50"] for a in rows if a["model"] == "B0_random"), None)
    lines = ["| model | precision@50 | lift over random | PR-AUC | cutoffs |", "|---|---:|---:|---:|---:|"]
    for a in sorted(rows, key=lambda a: -a["precision_at_50"]):
        lift = f"{a['precision_at_50'] / base:.1f}x" if base else "n/a"
        lines.append(f"| `{a['model']}` | {a['precision_at_50']} | {lift} | {a.get('pr_auc')} | {a.get('cutoffs')} |")
    return lines


def _rival(backtest: dict | None) -> str:
    """One sentence comparing the primary with the strongest other model, worded from the numbers.

    The headline stays the pre-registered model; this keeps a rival that matches it from being buried in
    the table, which is the honest reading when the primary does not clearly win.
    """
    rows = _b1_union(backtest, "aggregate_matched") or _b1_union(backtest, "aggregate")
    best = _primary(backtest)
    others = [a for a in rows if a["model"] not in (PRIMARY_MODEL, "B0_random")]
    if not best or not others:
        return ""
    top = max(others, key=lambda a: a["precision_at_50"])
    gap = round(best["precision_at_50"] - top["precision_at_50"], 4)
    if gap > 0.01:
        return (f"`{PRIMARY_MODEL}` leads the next model, `{top['model']}`, by {gap} in precision@50.")
    return (f"`{PRIMARY_MODEL}` does not clearly beat `{top['model']}` on the primary endpoint "
            f"({best['precision_at_50']} vs {top['precision_at_50']}; PR-AUC {best.get('pr_auc')} vs "
            f"{top.get('pr_auc')}). It stays the headline because it was pre-registered; the reading is that "
            "the learned and unsupervised models find the same, mostly Russia-trade, signal.")


def _model_name(path: str | None) -> str | None:
    """`/home/x/models/Qwen3-14B-Q4_K_M.gguf` -> `Qwen3-14B-Q4_K_M`. Home paths stay out of the repo."""
    return path.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".gguf") if path else path


def _bold(value, command: str) -> str:
    """Bold a measured value; leave the not-measured marker alone, or the markdown nests (`****`)."""
    return f"**{value}**" if value not in (None, "", []) else _n(value, command)


def _briefs_section(briefs: dict, judge: dict) -> list[str]:
    f = briefs.get("faithfulness") or {}
    k = judge.get("kappa") or {}
    sev = judge.get("by_severity") or {}
    return [
        "## Analyst briefs from a local LLM", "",
        f"For each cutoff's top 50, a locally hosted model (`{_n(judge.get('generator_model'), 'make judge')}`) "
        "writes a short brief under a JSON schema that forces "
        "every claim to cite evidence-record ids from a bundle built as of the cutoff. Nothing leaves the "
        "machine. Three checks then grade it, from cheapest to most trusted:", "",
        f"- **Deterministic verifier:** {_n(f.get('clean'), 'make briefs')} of {_n(f.get('briefs'), 'make briefs')} "
        f"briefs ({_n(f.get('share_clean'), 'make briefs')}) cite only real records, cover their top drivers, "
        "and state no number, date or name the bundle does not contain. The rest are the model departing from "
        "the evidence (converting hours to days, naming a country it was only given a code for).",
        f"- **Cross-family judge:** `{_n(_model_name(judge.get('judge_model')), 'make judge')}` grades each of "
        f"{_n(judge.get('findings'), 'make judge')} claims against only the records it cited: "
        f"{_bold(judge.get('entailment_rate'), 'make judge')} entailed"
        + (f" (high-severity claims {sev.get('high')}, medium {sev.get('medium')}, low {sev.get('low')})."
           if sev else "."),
        f"- **Human audit:** 30 claims sampled across the judge's verdicts and graded blind. Judge-versus-human "
        f"Cohen's kappa: {_bold(k.get('kappa'), 'fill reports/audit_sheet.csv, then make kappa')}. Until that "
        "number exists the judge's rate is one model's opinion of another's.", "",
        "The first full run failed most briefs. Most of those failures were the verifier's own "
        "(it read `(E4)` as the number 4), one was the evidence bundle's (Russian port calls had been cut "
        "for space); both were fixed, disclosed in `reports/phase8.md`, and every brief regenerated. "
        "Details: `reports/phase8.md`, `reports/phase9.md`.", ""]


def _forward_lists() -> list[str]:
    """The committed forward-test lists, read from the append-only manifest."""
    manifest = config.REPORTS_DIR / "forward" / "README.md"
    rows = re.findall(r"^\| (\d{4}-\d{2}-\d{2}) \| `([^`]+)` \| ([^|]+?) \| (\d+) \| `([0-9a-f]{64})` \|$",
                      manifest.read_text(), re.M) if manifest.exists() else []
    if not rows:
        return [f"{_n(None, 'make forward-score')}."]
    return (["| scored at | model | population | sha256 |", "|---|---|---:|---|"]
            + [f"| {d} | {m} | {n} | `{h[:12]}...` |" for d, _, m, n, h in rows])


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
        out += [f"On the pre-registered endpoint (precision@50 within the Russia-port stratum, label = "
                f"OFAC ∪ EU ∪ UK, macro-averaged over {best.get('cutoffs')} monthly cutoffs), the "
                f"pre-registered primary model, **`{best.get('model')}`, reaches precision@50 "
                f"{best.get('precision_at_50')}** (PR-AUC {best.get('pr_auc')}, recall@50 "
                f"{best.get('recall_at_50')}): of the 50 tankers it ranks highest each month, that share is "
                "designated by the US, EU or UK within the next 182 days.", "",
                "Every model on the same cutoffs (the supervised models cannot score the earliest ones, which "
                "have the highest base rates, so all-cutoff averages are not comparable):", "",
                *_comparison(back), "",
                _rival(back), "",
                "The signal is modest and simple: every learned model lands close to the others, and the "
                "per-family ablations in `reports/phase6.md` show which data carries it.", "",
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
    out += ["", "## Forward test", "",
            "A backtest can be tuned without meaning to. So the primary model's top 50 is committed to this "
            "repo, hash-stamped, before any of its outcomes exist, and scored against real designations "
            "later (`make forward-eval`). The manifest, `reports/forward/README.md`, is append-only and says "
            "which list is primary and from which date hits count.", "", *_forward_lists(), "",
            "## Changes made after results were seen", "",
            "Every one is recorded in `PREREG.md` section 12 and labelled post-hoc in the reports, with the "
            "numbers from before the change kept beside the numbers after it. Most were bug fixes that "
            "brought the code in line with what was pre-registered. One mattered for every number above: "
            "LightGBM gave different results on identical re-runs, because the database summed floats across "
            "threads in no fixed order. Feature builds are now single-threaded and rounded, two builds of a "
            "cutoff compare equal, and every reported number is from after that fix. The forward test is "
            "the check none of these changes can influence."]
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
            *_briefs_section(briefs, judge),
            "## How the point-in-time claim is enforced", "",
            "`features(hull_id, T)` may only read records with `observed_at <= T`. Five tests hold that "
            "claim up, and `make backtest` runs them before it reports anything:", "",
            "1. Features at T from the live store equal features at T from a store whose later partitions "
            "are physically deleted and whose derived tables are rebuilt from what is left.",
            "2. Static analysis: nothing under `features/` imports label construction, no feature name "
            "mentions sanctions except the partner feature, no GFW registry ownership field is read.",
            "3. Permutation: shuffling the training labels drops PR-AUC to the base rate.",
            "4. Reverse time: training on later cutoffs and scoring earlier ones is not dramatically "
            "better than forward, measured as lift over each cutoff's base rate."
            + (" **Disclosed:** the pre-registered version compared raw PR-AUC, which failed because the "
               "earliest cutoff has several times the late one's base rate; the comparison was amended to "
               "lift on 2026-10-02, after results had been seen (`PREREG.md` section 12)."
               if (back.get("leakage") or {}).get("reverse_time", {}).get("raw_passes") is False else ""),
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
            "ranked risks and every ADR. `phase-prompts.md` is the build plan.", "",
            "## Analyst console", "",
            "`ui/` is a local React console (ADR-18): a ranked watchlist per cutoff, a map of each "
            "hull's DMA track and its events, and a timeline that shows each hull only as it was "
            "knowable at the chosen instant. It renders a JSON bundle the Python side writes and never "
            "computes a metric itself. `make ui-export` builds that bundle from the real outputs (every "
            "model's scores, the copied metrics, and per-hull dossiers); `make ui-fixtures` builds a synthetic "
            "one, labelled as such. Local only: its dossiers carry GFW-derived "
            "events (rule 7). See `SETUP.md` section 9.", "",
            "## Documents", "",
            "- `shadow-fleet-plan.md`: architecture, risks, ADRs, evaluation design.",
            "- `CLAUDE.md`: rules for every build session.",
            "- `phase-prompts.md`, `phase1-prompt.md`: one prompt per phase.",
            "- `PREREG.md`: the pre-registered evaluation, committed before any label existed.", ""]
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
