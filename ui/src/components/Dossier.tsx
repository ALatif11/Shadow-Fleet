import { useMemo } from "react";
import type { Dossier as D, WatchlistRow } from "../contract";
import { useConsole } from "../state/store";
import { Panel } from "./Panel";
import { eventsAsOf, endOfDay, identityAt } from "../data/asof";
import { EVENT_LABEL, flagName, fmtDate, fmtDateTime, fmtDay, fmtFeature, fmtNum, MODEL_LABEL, SOURCE_LABEL } from "../lib/format";
import { useDecode } from "../lib/motion";
import { EVENT_HELP, GLOSSARY } from "../lib/plain";

export function Dossier({ dossier, row, loading }: { dossier: D | null; row: WatchlistRow | null; loading: boolean }) {
  const s = useConsole();
  const features = useMemo(() => new Map(s.manifest!.features.map((f) => [f.name, f])), [s.manifest]);

  if (!s.selected) {
    return (
      <Panel title="Dossier" code="03" help="dossier" className="dossier">
        <div className="empty">Pick a ship in the watchlist to see its file.</div>
      </Panel>
    );
  }
  if (!dossier) {
    return (
      <Panel title="Dossier" code="03" help="dossier" className="dossier">
        <div className={`empty ${loading ? "wait" : ""}`}>{loading ? "Opening file…" : "No file for this ship in this bundle."}</div>
      </Panel>
    );
  }

  const ident = identityAt(dossier.identity, s.asOf) ?? dossier.identity[0];
  const knownIdentity = dossier.identity.filter((iv) => Date.parse(iv.start) <= s.asOf);
  const events = eventsAsOf(dossier.events, s.asOf).filter((e) => !s.hiddenTypes.has(e.type)).reverse();
  const scores = dossier.scores.filter((p) => p.model === s.model);
  const cutoffEnd = endOfDay(s.cutoff);
  const maxAbs = Math.max(1e-9, ...(row?.drivers ?? []).map((d) => Math.abs(d.contribution)));

  return (
    <Panel
      title="Dossier"
      code="03"
      help="dossier"
      className="dossier"
      right={<span className="mono">{row ? `rank ${row.rank} · score ${row.score.toFixed(3)}` : "not in the top list"}</span>}
    >
      <div className="dos-scroll view-in" key={dossier.hull_id}>
        <div className="dos-id">
          <Name text={ident?.name ?? "UNKNOWN"} />
          <div className="dos-facts">
            <span data-tip={GLOSSARY.flag.short}><small>Flag</small>{flagName(ident?.flag_iso3)}</span>
            <span><small>Type</small>{dossier.header.ship_type ?? "—"}</span>
            <span data-tip="Length × beam"><small>Size</small>{fmtNum(dossier.header.length_m, 0)} × {fmtNum(dossier.header.beam_m, 0)} m</span>
            <span data-tip={GLOSSARY.dwt.short}><small>Carries</small>{dossier.header.dwt ? `${Math.round(dossier.header.dwt).toLocaleString("en-GB")} t` : "—"}</span>
            <span><small>Built</small>{dossier.header.built_year ?? "—"}</span>
          </div>
          <div className="dos-meta mono dim">
            <span data-tip={GLOSSARY.hull_id.long}>{dossier.hull_id}</span>
            <span data-tip={GLOSSARY.imo.long}>IMO {dossier.imo ?? "—"}</span>
            <span data-tip={GLOSSARY.mmsi.long}>MMSI {ident?.mmsi ?? "—"}</span>
          </div>
        </div>

        <h4>
          Rank, month by month <small>{MODEL_LABEL[s.model] ?? s.model}</small>
        </h4>
        <p className="hint-line">Higher is more suspicious. Above the dashed line, an analyst reviewing the top 50 would have seen it.</p>
        <RankSpark points={scores} cutoff={s.cutoff} designation={s.hindsight ? dossier.sanctions[0]?.date ?? null : null} />

        {row && (
          <>
            <h4 data-tip={GLOSSARY.driver.long}>Why it was flagged <small>biggest reasons first</small></h4>
            <p className="hint-line legend-line"><span className="pos">▮</span> raised the score <span className="neg">▮</span> lowered it</p>
            <div className="drivers">
              {row.drivers.map((d, i) => {
                const f = features.get(d.feature);
                const w = (Math.abs(d.contribution) / maxAbs) * 50;
                return (
                  <div className="driver" key={`${dossier.hull_id}|${s.cutoff}|${d.feature}`} style={{ ["--i" as string]: i }}>
                    <span className="dname">
                      <b>{f?.description ?? d.feature}</b>
                      {s.explain && <small>{f ? `${d.feature} · ${SOURCE_LABEL[f.source] ?? f.source}` : d.feature}</small>}
                    </span>
                    <span className="dval mono">{fmtFeature(d.feature, d.value)}</span>
                    <span className="dbar">
                      <i className={d.contribution >= 0 ? "pos" : "neg"} style={d.contribution >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
                    </span>
                  </div>
                );
              })}
            </div>
          </>
        )}

        <h4>Names and flags it has used <small>{knownIdentity.length} known</small></h4>
        {s.explain && <p className="hint-line">{GLOSSARY.hull_id.long}</p>}
        <div className="identity">
          {knownIdentity.map((iv, i) => (
            <div key={i} className={`iv ${iv === ident ? "cur" : ""}`}>
              <span className="mono">{fmtDate(Date.parse(iv.start))}</span>
              <span>{iv.name}</span>
              <span className="mono">{iv.flag_iso3}</span>
              <span className="mono dim">{iv.mmsi}</span>
            </div>
          ))}
        </div>

        {s.hindsight && dossier.sanctions.length > 0 && (
          <>
            <h4 className="amber">Sanctions designations <small>hindsight</small></h4>
            <div className="identity">
              {dossier.sanctions.map((a, i) => (
                <div key={i} className={`iv sanction ${endOfDay(a.date) <= cutoffEnd ? "" : "future"}`}>
                  <span className="mono">{a.date}</span>
                  <span>{a.authority} {a.action.toUpperCase()}</span>
                  <span className="mono dim">{a.program}</span>
                  <span />
                </div>
              ))}
            </div>
          </>
        )}

        <h4>What happened <small>{events.length} known by {fmtDay(s.asOf)}</small></h4>
        <p className="hint-line">Newest first. Click one to fly the map to it.</p>
        <div className="events">
          {events.length === 0 && <div className="empty small">Nothing had been observed by this date.</div>}
          {events.slice(0, 200).map((e) => (
            <button
              key={e.id}
              className={`ev ${s.focusEvent === e.id ? "on" : ""}`}
              style={{ ["--c" as string]: `var(--ev-${e.type})` }}
              data-tip={EVENT_HELP[e.type]}
              onClick={() => s.focus(e.id, e.lon, e.lat)}
            >
              <i />
              <span className="ev-type">{EVENT_LABEL[e.type]}</span>
              <span className="ev-src mono">{SOURCE_LABEL[e.source]}</span>
              <span className="ev-time mono">{fmtDateTime(Date.parse(e.start))}</span>
              <span className="ev-sum">{e.summary}</span>
            </button>
          ))}
        </div>
      </div>
    </Panel>
  );
}

function RankSpark({ points, cutoff, designation }: { points: D["scores"]; cutoff: string; designation: string | null }) {
  const W = 320;
  const H = 70;
  if (points.length === 0) return <div className="empty small">NOT SCORED BY THIS MODEL</div>;
  const xs = points.map((p) => Date.parse(p.cutoff));
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs, x0 + 1);
  const maxRank = Math.max(100, ...points.map((p) => p.rank ?? 0));
  const X = (ms: number) => 6 + ((ms - x0) / (x1 - x0)) * (W - 12);
  const Y = (rank: number) => 6 + (Math.log(rank) / Math.log(maxRank)) * (H - 12);
  const path = points.filter((p) => p.rank).map((p, i) => `${i ? "L" : "M"}${X(Date.parse(p.cutoff)).toFixed(1)},${Y(p.rank!).toFixed(1)}`).join("");
  const cur = points.find((p) => p.cutoff === cutoff);
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      <line className="top50" x1={0} x2={W} y1={Y(50)} y2={Y(50)} />
      <text className="lbl" x={W - 4} y={Y(50) - 3} textAnchor="end">TOP 50</text>
      <path d={path} className="line" />
      {cur?.rank && <circle cx={X(Date.parse(cur.cutoff))} cy={Y(cur.rank)} r={4} className="cur" />}
      <line className="cutoff" x1={X(Date.parse(cutoff))} x2={X(Date.parse(cutoff))} y1={0} y2={H} />
      {designation && Date.parse(designation) <= x1 + 200 * 86_400_000 && (
        <line className="desig" x1={X(Math.min(Date.parse(designation), x1))} x2={X(Math.min(Date.parse(designation), x1))} y1={0} y2={H} />
      )}
      <text className="lbl" x={4} y={12}>RANK 1</text>
      <text className="lbl" x={4} y={H - 3}>{maxRank}+</text>
    </svg>
  );
}

function Name({ text }: { text: string }) {
  const shown = useDecode(text);
  return <div className="dos-name" aria-label={text}><span aria-hidden="true">{shown}</span></div>;
}
