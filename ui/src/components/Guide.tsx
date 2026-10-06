import { useState } from "react";
import { GLOSSARY, EVENT_HELP, HOW_TO } from "../lib/plain";
import { EVENT_LABEL, EVENT_TYPES } from "../lib/format";
import { Panel } from "./Panel";
import { useConsole } from "../state/store";

export function Guide() {
  const [q, setQ] = useState("");
  const openDock = useConsole((s) => s.openDock);
  const needle = q.trim().toLowerCase();
  const terms = Object.values(GLOSSARY).filter(
    (t) => !needle || `${t.term} ${t.short} ${t.long ?? ""}`.toLowerCase().includes(needle),
  );
  return (
    <Panel title="How to use this" code="06" className="guide">
      <div className="guide-body">
        <div className="welcome">
          <b>New here?</b>
          <span>Read the six steps below, or jump straight in: the list on the left is already open on the most suspicious ship.</span>
          <button onClick={() => openDock("dossier")}>Open its file →</button>
        </div>
        <h3>What this console is</h3>
        <p>
          It replays a sanctions-evasion detector month by month. For any past month it shows which tankers the
          system would have put in front of an analyst <em>that month</em>, using only what was known then, and
          (if you ask for it) what actually happened to them afterwards.
        </p>

        <h3>Six steps</h3>
        <ol className="howto">
          {HOW_TO.map((s) => (
            <li key={s.step}>
              <b>{s.step}</b>
              <span>{s.body}</span>
            </li>
          ))}
        </ol>

        <h3>Keys</h3>
        <dl className="keys">
          <div><kbd>↑</kbd><kbd>↓</kbd><span>move through the watchlist</span></div>
          <div><kbd>[</kbd><kbd>]</kbd><span>previous / next pretend-today month</span></div>
          <div><kbd>←</kbd><kbd>→</kbd><span>move the as-of date by a day (hold shift for 30)</span></div>
          <div><kbd>space</kbd><span>play the ship's track</span></div>
          <div><kbd>H</kbd><span>hindsight on / off</span></div>
          <div><kbd>?</kbd><span>explain mode on / off</span></div>
          <div><kbd>1</kbd><span>show or hide the watchlist</span></div>
          <div><kbd>2</kbd><span>map only (press again to bring the panels back)</span></div>
          <div><kbd>3</kbd>–<kbd>6</kbd><span>dossier, brief, ask, guide in the right-hand dock; the same key again closes it</span></div>
        </dl>

        <h3>Event types</h3>
        <dl className="glossary">
          {EVENT_TYPES.map((t) => (
            <div key={t}>
              <dt style={{ ["--c" as string]: `var(--ev-${t})` }}><i />{EVENT_LABEL[t]}</dt>
              <dd>{EVENT_HELP[t]}</dd>
            </div>
          ))}
        </dl>

        <h3>Glossary</h3>
        <input
          className="guide-filter"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filter terms"
          aria-label="Filter glossary"
        />
        <dl className="glossary">
          {terms.map((t) => (
            <div key={t.term}>
              <dt>{t.term}</dt>
              <dd>
                {t.short}
                {t.long && <span className="long">{t.long}</span>}
              </dd>
            </div>
          ))}
          {terms.length === 0 && <p className="empty small">Nothing matches.</p>}
        </dl>
      </div>
    </Panel>
  );
}
