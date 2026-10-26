import { useEffect, useState } from "react";
import type { BriefFinding, Dossier, SavedBrief, WatchlistRow } from "../contract";
import { useConsole } from "../state/store";
import { Panel } from "./Panel";
import { BRIEF_PROMPT, LLM_URL, useModel } from "../data/llm";
import { fmtDay } from "../lib/format";
import { Cited } from "./Cited";

const VERDICT: Record<string, [string, string]> = {
  entailed: ["SUPPORTED", "The judge model found everything this claim says in the records it cites."],
  partially: ["PARTLY", "The judge model found some of this claim in the cited records, not all of it."],
  not_entailed: ["NOT SUPPORTED", "The judge model did not find this claim in the records it cites."],
};

const recordText = (r: Record<string, unknown>) =>
  Object.entries(r).filter(([, v]) => v != null).map(([k, v]) => `${k}: ${v}`).join(", ");

/** One brief id as a chip: hover shows the record, click flies the map to it when it has a position. */
function Cite({ id, brief }: { id: string; brief: SavedBrief }) {
  const focus = useConsole((s) => s.focus);
  const r = brief.evidence[id] ?? {};
  const lon = typeof r.lon === "number" ? r.lon : null;
  const lat = typeof r.lat === "number" ? r.lat : null;
  return (
    <button className={`cite ${"note" in r ? "bad" : ""}`} style={{ ["--c" as string]: "var(--accent-500)" }}
      data-tip={recordText(r) + (lon != null ? ". Click to show it on the map." : "")}
      onClick={() => lon != null && focus(null, lon, lat)}>
      {id}
    </button>
  );
}

function Finding({ f, brief }: { f: BriefFinding; brief: SavedBrief }) {
  const [label, tip] = f.judge_verdict ? VERDICT[f.judge_verdict] : ["NOT JUDGED", "No judge verdict for this text of the claim."];
  return (
    <li>
      <span className={`sev ${f.severity}`}>{f.severity.toUpperCase()}</span> {f.claim}{" "}
      {f.evidence_ids.map((i) => <Cite key={i} id={i} brief={brief} />)}{" "}
      <span className={`verdict ${f.judge_verdict ?? "none"}`} data-tip={f.judge_reason ? `${tip} Judge: ${f.judge_reason}` : tip}>{label}</span>
    </li>
  );
}

function Saved({ brief }: { brief: SavedBrief }) {
  const ids = Object.keys(brief.evidence);
  return (
    <div className="saved">
      <p className={`checked ${brief.passes_checks ? "ok" : "bad"}`}
        data-tip="Every number, date and name in the brief is in its evidence, every citation exists, and the top drivers are covered. Checked by code, not by a model.">
        {brief.passes_checks
          ? `Saved brief · passed every automatic check${brief.repaired ? " after one repair round" : ""}`
          : "Saved brief · failed an automatic check"}
      </p>
      <p className="dim note">Written for the {fmtDay(Date.parse(brief.cutoff))} cutoff from evidence available then. Risk level: {brief.risk_level}.</p>
      <p className="brief-text">{brief.summary}</p>
      <ul className="findings">{brief.findings.map((f, i) => <Finding key={i} f={f} brief={brief} />)}</ul>
      {brief.caveats.length > 0 && <ul className="caveats dim">{brief.caveats.map((c, i) => <li key={i}>{c}</li>)}</ul>}
      {brief.truncated_families.length > 0 && (
        <p className="dim note">Trimmed to fit the model's context: {brief.truncated_families.join(", ")} records.</p>
      )}
      <details className="records">
        <summary>Cited records ({ids.length})</summary>
        <ul>{ids.map((i) => <li key={i}><b>{i}</b> {recordText(brief.evidence[i])}</li>)}</ul>
      </details>
    </div>
  );
}

export function Brief({ dossier, row }: { dossier: Dossier | null; row: WatchlistRow | null }) {
  const { asOf, cutoff, model } = useConsole();
  const { status, run } = useModel();
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // a bundle from contract 1.0.0 has no briefs field
  const saved = dossier?.briefs?.find((b) => b.cutoff === cutoff) ?? null;

  useEffect(() => {
    setText(null);
    setErr(null);
  }, [dossier?.hull_id, cutoff]);

  async function generate() {
    if (!dossier) return;
    setBusy(true);
    setErr(null);
    try {
      setText("");
      setText(await run(dossier, row, asOf, cutoff, model, [{ role: "user", content: BRIEF_PROMPT }], setText));
    } catch (e) {
      setErr(`${(e as Error).message}. Start llama-server on ${LLM_URL} (SETUP.md section 6).`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Brief"
      code="04"
      help="brief"
      className="brief"
      right={<span className={`llm ${status}`}>{status === "online" ? "MODEL ONLINE" : status === "checking" ? "CHECKING" : "MODEL OFFLINE"}</span>}
    >
      <div className="brief-body">
        {!dossier && <p className="empty">Select a ship.</p>}
        {dossier && (
          <>
            {saved && <Saved brief={saved} />}
            {!saved && (
              <p className="empty small">No saved brief for this ship at this cutoff: only each month's LightGBM top 50 were briefed.</p>
            )}
            <div className="brief-head">
              <button className={saved ? "secondary" : ""} onClick={generate} disabled={busy}>
                {busy ? "Writing…" : text ? "Rewrite live draft" : "Write live draft"}
              </button>
              <span className="dim">{dossier.hull_id} · as of {fmtDay(asOf)}</span>
            </div>
            {err && <p className="empty err">{err}</p>}
            {text !== null && (
              <>
                <p className="unverified" data-tip="Written just now with a short prompt and no checks. The saved brief above is the checked one.">
                  Unverified draft · written just now by your local model
                </p>
                <div className={`brief-text ${busy ? "streaming" : ""}`}><Cited text={text} dossier={dossier} /></div>
              </>
            )}
          </>
        )}
      </div>
    </Panel>
  );
}
