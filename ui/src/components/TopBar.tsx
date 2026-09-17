import { useEffect, useState } from "react";
import { useConsole } from "../state/store";
import { LABEL_SET_LABEL, MODEL_LABEL } from "../lib/format";

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return <span className="mono clock">{now.toISOString().slice(11, 19)}Z</span>;
}

function Segmented<T extends string>({ value, options, label, onChange }: {
  value: T;
  options: T[];
  label: (v: T) => string;
  onChange: (v: T) => void;
}) {
  return (
    <div className="seg" role="radiogroup">
      {options.map((o) => (
        <button key={o} role="radio" aria-checked={o === value} className={o === value ? "on" : ""} onClick={() => onChange(o)}>
          {label(o)}
        </button>
      ))}
    </div>
  );
}

export function TopBar() {
  const s = useConsole();
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
        <div className="subtitle">EVASION-INDICATOR CONSOLE · PIT BACKTEST</div>
      </div>

      <div className="control cutoff">
        <label>CUTOFF</label>
        <div className="stepper">
          <button onClick={() => s.stepCutoff(-1)} disabled={idx <= 0} aria-label="previous cutoff">‹</button>
          <select value={s.cutoff} onChange={(e) => s.setCutoff(e.target.value)}>
            {m.cutoffs.map((c) => (
              <option key={c.cutoff} value={c.cutoff}>{c.cutoff}</option>
            ))}
          </select>
          <button onClick={() => s.stepCutoff(1)} disabled={idx >= m.cutoffs.length - 1} aria-label="next cutoff">›</button>
        </div>
        <span className="hint mono">
          {idx + 1}/{m.cutoffs.length} {info?.supervised ? "· SUPERVISED" : "· RULES/UNSUP ONLY"}
        </span>
      </div>

      <div className="control">
        <label>MODEL</label>
        <Segmented value={s.model} options={m.models} label={(v) => MODEL_LABEL[v] ?? v} onChange={s.setModel} />
      </div>

      <div className="control">
        <label>LABEL</label>
        <Segmented value={s.labelSet} options={m.label_sets} label={(v) => LABEL_SET_LABEL[v] ?? v} onChange={s.setLabelSet} />
      </div>

      <div className="spacer" />
      <div className="status">
        {m.origin === "synthetic" ? (
          <span className="badge synthetic" title={m.notes.join("\n")}>SYNTHETIC<span className="long"> DATA · NOT RESULTS</span></span>
        ) : (
          <span className="badge live">LIVE BUNDLE</span>
        )}
        <button className={`badge hindsight ${s.hindsight ? "on" : ""}`} onClick={s.toggleHindsight} title="Show outcomes and let the scrubber pass the cutoff (H)">
          HINDSIGHT {s.hindsight ? "ON" : "OFF"}
        </button>
        <Clock />
      </div>
    </header>
  );
}
