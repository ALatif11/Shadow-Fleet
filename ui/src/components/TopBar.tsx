import { useEffect, useState } from "react";
import { usePick } from "../state/store";
import { fmtMonth, LABEL_SET_HELP, LABEL_SET_LABEL, MODEL_HELP, MODEL_LABEL } from "../lib/format";
import { Segmented } from "./Segmented";
import { GLOSSARY } from "../lib/plain";

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return <span className="mono clock">{now.toISOString().slice(11, 19)}Z</span>;
}

export function TopBar() {
  const s = usePick("manifest", "cutoff", "stepCutoff", "setCutoff", "model", "setModel", "labelSet", "setLabelSet", "hindsight", "toggleHindsight");
  const m = s.manifest!;
  const idx = m.cutoffs.findIndex((c) => c.cutoff === s.cutoff);
  const info = m.cutoffs[idx];

  return (
    <header className="topbar">
      <div className="brand">
        <div className="logo">
          <span className="logo-mark" />
          SHADOW<b>FLEET</b>
        </div>
        <div className="subtitle">Would we have spotted it first?</div>
      </div>

      <div className="control cutoff">
        <label data-tip={`${GLOSSARY.cutoff.short} Everything on screen is what was knowable at the end of this month.`}>Pretend today</label>
        <div className="stepper">
          <button onClick={() => s.stepCutoff(-1)} disabled={idx <= 0} aria-label="Previous month" data-tip="Previous month ( [ )">‹</button>
          <select value={s.cutoff} onChange={(e) => s.setCutoff(e.target.value)} aria-label="Pretend-today month">
            {m.cutoffs.map((c) => (
              <option key={c.cutoff} value={c.cutoff}>{fmtMonth(c.cutoff)}</option>
            ))}
          </select>
          <button onClick={() => s.stepCutoff(1)} disabled={idx >= m.cutoffs.length - 1} aria-label="Next month" data-tip="Next month ( ] )">›</button>
        </div>
        <span className="hint mono" data-tip={info?.supervised ? undefined : GLOSSARY.supervised.long}>
          month {idx + 1} of {m.cutoffs.length}{info?.supervised ? "" : " · checklist only"}
        </span>
      </div>

      <div className="control">
        <label data-tip={GLOSSARY.model.long}>Ranked by</label>
        <Segmented value={s.model} options={m.models} label={(v) => MODEL_LABEL[v] ?? v} tip={(v) => MODEL_HELP[v]} onChange={s.setModel} ariaLabel="Model" />
      </div>

      <div className="control">
        <label data-tip={GLOSSARY.label_set.long}>Sanctions lists</label>
        <Segmented value={s.labelSet} options={m.label_sets} label={(v) => LABEL_SET_LABEL[v] ?? v} tip={(v) => LABEL_SET_HELP[v]} onChange={s.setLabelSet} ariaLabel="Sanctions lists" />
      </div>

      <div className="spacer" />
      <div className="status">
        {m.origin === "synthetic" ? (
          <span className="badge synthetic" data-tip={GLOSSARY.synthetic.long}>SYNTHETIC<span className="long"> · invented data, not results</span></span>
        ) : (
          <span className="badge live">LIVE BUNDLE</span>
        )}
        <button className={`badge hindsight ${s.hindsight ? "on" : ""}`} onClick={s.toggleHindsight} data-tip={`${GLOSSARY.hindsight.long} (H)`} aria-pressed={s.hindsight}>
          Hindsight {s.hindsight ? "on" : "off"}
        </button>
        <Clock />
      </div>
    </header>
  );
}
