import { useEffect, useState } from "react";
import type { Dossier, WatchlistRow } from "../contract";
import { useConsole } from "../state/store";
import { Panel } from "./Panel";
import { BRIEF_PROMPT, LLM_URL, useModel } from "../data/llm";
import { fmtDay } from "../lib/format";
import { Cited } from "./Cited";

export function Brief({ dossier, row }: { dossier: Dossier | null; row: WatchlistRow | null }) {
  const { asOf, cutoff, model } = useConsole();
  const { status, run } = useModel();
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
            <div className="brief-head">
              <button onClick={generate} disabled={busy}>{busy ? "Writing…" : text ? "Rewrite brief" : "Write brief"}</button>
              <span className="dim">{dossier.hull_id} · as of {fmtDay(asOf)}</span>
            </div>
            {err && <p className="empty err">{err}</p>}
            {text !== null && (
              <>
                <p className="unverified" data-tip="Phase 8 replaces this with a schema-constrained brief, and Phase 9 measures how much of it the evidence actually supports.">
                  Unverified draft · written just now by your local model
                </p>
                <div className={`brief-text ${busy ? "streaming" : ""}`}><Cited text={text} dossier={dossier} /></div>
              </>
            )}
            {text === null && !err && !busy && (
              <p className="empty small">
                The model reads this hull's evidence records and writes a short case summary with citations.
                It runs on your GPU; nothing leaves the machine.
              </p>
            )}
          </>
        )}
      </div>
    </Panel>
  );
}
