import { useEffect, useRef, useState } from "react";
import type { Dossier, WatchlistRow } from "../contract";
import { useConsole } from "../state/store";
import { Panel } from "./Panel";
import { LLM_URL, useModel } from "../data/llm";
import { Cited } from "./Cited";

const QUICK = ["Why was this ship flagged?", "What is the weakest part of this case?", "Summarise its last 90 days."];

export function Chat({ dossier, row }: { dossier: Dossier | null; row: WatchlistRow | null }) {
  const { asOf, cutoff, model } = useConsole();
  const { status, run } = useModel();
  const [msgs, setMsgs] = useState<{ role: string; content: string }[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const log = useRef<HTMLDivElement>(null);

  useEffect(() => setMsgs([]), [dossier?.hull_id]);
  useEffect(() => {
    log.current?.scrollTo({ top: log.current.scrollHeight });
  }, [msgs, busy]);

  async function send(text: string) {
    if (!text.trim() || !dossier || busy) return;
    const next = [...msgs, { role: "user", content: text }];
    setMsgs(next);
    setInput("");
    setBusy(true);
    try {
      const reply = await run(dossier, row, asOf, cutoff, model, next, (soFar) =>
        setMsgs([...next, { role: "assistant", content: soFar }]));
      setMsgs([...next, { role: "assistant", content: reply }]);
    } catch (e) {
      setMsgs([...next, { role: "error", content: `${(e as Error).message}. Is llama-server running on ${LLM_URL}?` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Ask the model"
      code="05"
      help="chat"
      className="chat"
      right={<span className={`llm ${status}`}>{status === "online" ? "MODEL ONLINE" : status === "checking" ? "CHECKING" : "MODEL OFFLINE"}</span>}
    >
      <div className="chat-log" ref={log}>
        {!dossier && <p className="empty">Select a ship first. The model is only given that ship's evidence.</p>}
        {dossier && msgs.length === 0 && (
          <p className="empty small">
            Everything the model can see: this hull's identity, its score breakdown at {cutoff}, and every event
            observed on or before the as-of date. It has to cite record ids.
          </p>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`bubble ${m.role} view-in`}>
            <span className="who">{m.role === "user" ? "You" : m.role === "error" ? "Error" : "Model · draft"}</span>
            <p className={busy && i === msgs.length - 1 && m.role === "assistant" ? "streaming" : ""}>
              {m.role === "assistant" && dossier ? <Cited text={m.content} dossier={dossier} /> : m.content}
            </p>
          </div>
        ))}
        {busy && msgs[msgs.length - 1]?.role === "user" && (
          <div className="bubble assistant view-in"><span className="who">Model</span><p className="wait">reading the evidence…</p></div>
        )}
      </div>
      {dossier && (
        <div className="quick">
          {QUICK.map((q) => (
            <button key={q} onClick={() => send(q)} disabled={busy}>{q}</button>
          ))}
        </div>
      )}
      <form
        className="chat-form"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={dossier ? "Ask about this ship" : "No ship selected"}
          disabled={!dossier || busy}
          aria-label="Message to the local model"
        />
        <button type="submit" disabled={!dossier || busy || !input.trim()}>Send</button>
      </form>
    </Panel>
  );
}
