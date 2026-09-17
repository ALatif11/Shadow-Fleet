import { useEffect, useMemo, useRef, useState } from "react";
import type { Dossier } from "../contract";
import { asOfBounds, useConsole } from "../state/store";
import { DAY_MS, endOfDay, featureWindow, isKnown, startOfDay } from "../data/asof";
import { fmtDate } from "../lib/format";

const LANES = [
  { key: "identity", label: "IDENTITY", match: (_src: string, t: string) => t === "identity_change" },
  { key: "dma", label: "DMA TRACK", match: () => false },
  { key: "gfw", label: "GFW EVENTS", match: (src: string) => src === "gfw" },
  { key: "self", label: "SELF-BUILT", match: (src: string, t: string) => src === "self_built" && t !== "identity_change" },
  { key: "sanctions", label: "DESIGNATION", match: () => false },
];
const LANE_H = 16;
const LEFT = 104;
const TOP = 18;

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(800);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export function Timeline({ dossier }: { dossier: Dossier | null }) {
  const s = useConsole();
  const m = s.manifest!;
  const [ref, width] = useWidth();
  const svgRef = useRef<SVGSVGElement>(null);
  const dragging = useRef(false);

  const [lo, hiWindow] = [asOfBounds({ manifest: m, cutoff: s.cutoff, hindsight: true })[0], endOfDay(m.window_end) + 60 * DAY_MS];
  const plotW = Math.max(100, width - LEFT - 12);
  const X = (ms: number) => LEFT + ((ms - lo) / (hiWindow - lo)) * plotW;
  const T = (x: number) => lo + ((x - LEFT) / plotW) * (hiWindow - lo);
  const height = TOP + LANES.length * LANE_H + 22;
  const cutoffX = X(endOfDay(s.cutoff));
  const [fwLo, fwHi] = featureWindow(s.cutoff);
  const [, maxAsOf] = asOfBounds(s);

  // DMA coverage as a daily density strip.
  const density = useMemo(() => {
    if (!dossier) return [] as { x: number; n: number }[];
    const days = new Map<number, number>();
    for (const t of dossier.track.t) {
      const d = Math.floor((t * 1000) / DAY_MS);
      days.set(d, (days.get(d) ?? 0) + 1);
    }
    return [...days.entries()].map(([d, n]) => ({ x: d * DAY_MS, n }));
  }, [dossier]);

  const months = useMemo(() => {
    const out: number[] = [];
    const d = new Date(lo);
    d.setUTCDate(1);
    d.setUTCHours(0, 0, 0, 0);
    while (d.getTime() < hiWindow) {
      d.setUTCMonth(d.getUTCMonth() + 1);
      out.push(d.getTime());
    }
    return out;
  }, [lo, hiWindow]);

  // Playback: one day per frame-step, stopping at the limit.
  useEffect(() => {
    if (!s.playing) return;
    const id = setInterval(() => {
      const st = useConsole.getState();
      const [, hi] = asOfBounds(st);
      if (st.asOf >= hi) {
        st.setPlaying(false);
        return;
      }
      st.setAsOf(st.asOf + DAY_MS);
    }, 60);
    return () => clearInterval(id);
  }, [s.playing]);

  const onPointer = (e: React.PointerEvent) => {
    if (e.type === "pointerdown") {
      dragging.current = true;
      (e.target as Element).setPointerCapture?.(e.pointerId);
    }
    if (e.type === "pointerup" || e.type === "pointercancel") dragging.current = false;
    if (!dragging.current) return;
    const rect = svgRef.current!.getBoundingClientRect();
    s.setAsOf(T(e.clientX - rect.left));
  };

  const asOfX = X(s.asOf);
  const cutoffInfo = m.cutoffs.find((c) => c.cutoff === s.cutoff);

  return (
    <div className="timeline" ref={ref}>
      <div className="tl-bar">
        <button className="play" onClick={() => {
          if (!s.playing && s.asOf >= maxAsOf) s.setAsOf(fwLo);
          s.setPlaying(!s.playing);
        }} title="Play the feature window (space)">
          {s.playing ? "❚❚" : "▶"}
        </button>
        <button onClick={() => s.setAsOf(fwLo)} title="Jump to feature-window start">⇤ T−180D</button>
        <button onClick={() => s.setAsOf(endOfDay(s.cutoff))} title="Jump to cutoff">T</button>
        <span className="asof mono">
          AS-OF <b>{fmtDate(s.asOf)}</b>
          {s.asOf > endOfDay(s.cutoff) ? <em className="amber"> +{Math.round((s.asOf - endOfDay(s.cutoff)) / DAY_MS)}D PAST CUTOFF</em> : <em> T−{Math.round((endOfDay(s.cutoff) - s.asOf) / DAY_MS)}D</em>}
        </span>
        <span className="tl-note mono">
          HORIZON → {cutoffInfo?.horizon_end} {cutoffInfo?.horizon_closed ? "(CLOSED)" : "(OPEN)"}
          {!s.hindsight && " · FUTURE LOCKED (H)"}
        </span>
      </div>
      <svg ref={svgRef} width={width} height={height} onPointerDown={onPointer} onPointerMove={onPointer} onPointerUp={onPointer} onPointerCancel={onPointer}>
        <defs>
          <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" className="hatch" />
          </pattern>
        </defs>
        {months.map((t) => {
          const d = new Date(t);
          const jan = d.getUTCMonth() === 0;
          return (
            <g key={t}>
              <line x1={X(t)} x2={X(t)} y1={TOP - 4} y2={height - 18} className={jan ? "grid yr" : "grid"} />
              {(jan || plotW > 900) && (
                <text x={X(t) + 2} y={height - 6} className="tick">
                  {jan ? d.getUTCFullYear() : d.toISOString().slice(5, 7)}
                </text>
              )}
            </g>
          );
        })}
        <rect x={X(fwLo)} y={TOP - 6} width={Math.max(1, X(fwHi) - X(fwLo))} height={LANES.length * LANE_H + 8} className="fw" />
        <text x={X(fwLo) + 3} y={TOP - 8} className="fw-lbl">FEATURE WINDOW</text>
        {!s.hindsight && <rect x={cutoffX} y={TOP - 6} width={Math.max(0, LEFT + plotW - cutoffX)} height={LANES.length * LANE_H + 8} fill="url(#hatch)" className="locked" />}
        <rect x={cutoffX} y={TOP - 6} width={Math.max(0, X(endOfDay(cutoffInfo?.horizon_end ?? s.cutoff)) - cutoffX)} height={3} className="horizon" />
        {LANES.map((l, i) => (
          <g key={l.key} transform={`translate(0, ${TOP + i * LANE_H})`}>
            <text x={8} y={LANE_H / 2 + 4} className="lane">{l.label}</text>
            <line x1={LEFT} x2={LEFT + plotW} y1={LANE_H / 2} y2={LANE_H / 2} className="lane-line" />
          </g>
        ))}
        {density.map((d) => (
          <rect key={d.x} x={X(d.x)} y={TOP + LANE_H + 3} width={Math.max(1, plotW / ((hiWindow - lo) / DAY_MS))} height={LANE_H - 6}
            className={d.x <= s.asOf ? "dma on" : s.hindsight ? "dma future" : "dma off"} style={{ opacity: Math.min(1, 0.25 + d.n / 40) }} />
        ))}
        {dossier?.events.map((e) => {
          const lane = LANES.findIndex((l) => l.match(e.source, e.type));
          if (lane < 0 || s.hiddenTypes.has(e.type)) return null;
          const known = isKnown(e, s.asOf);
          if (!known && !s.hindsight) return null;
          const x0 = X(Date.parse(e.start));
          const x1 = Math.max(x0 + 2, X(Date.parse(e.end)));
          return (
            <rect key={e.id} x={x0} y={TOP + lane * LANE_H + 3} width={x1 - x0} height={LANE_H - 6} rx={1}
              className={`evt ${known ? "" : "future"} ${s.focusEvent === e.id ? "on" : ""}`}
              style={{ ["--c" as string]: `var(--ev-${e.type})` }}
              onPointerDown={(ev) => { ev.stopPropagation(); s.focus(e.id, e.lon, e.lat); }}>
              <title>{e.summary}</title>
            </rect>
          );
        })}
        {s.hindsight && dossier?.sanctions.map((a, i) => (
          <g key={i} transform={`translate(${X(startOfDay(a.date))}, ${TOP + 4 * LANE_H + LANE_H / 2})`}>
            <path d="M0,-6 L5,0 L0,6 L-5,0 Z" className="desig" />
            <text x={7} y={4} className="desig-lbl">{a.authority}</text>
          </g>
        ))}
        {m.cutoffs.map((c) => (
          <line key={c.cutoff} x1={X(endOfDay(c.cutoff))} x2={X(endOfDay(c.cutoff))} y1={height - 22} y2={height - 16}
            className={c.cutoff === s.cutoff ? "ctick on" : "ctick"} onPointerDown={(ev) => { ev.stopPropagation(); s.setCutoff(c.cutoff); }} />
        ))}
        <line x1={cutoffX} x2={cutoffX} y1={TOP - 10} y2={height - 16} className="cutoff" />
        <text x={cutoffX + 3} y={10} className="cutoff-lbl">T {s.cutoff}</text>
        <g transform={`translate(${asOfX}, 0)`} className="asof-handle">
          <line y1={TOP - 8} y2={height - 16} />
          <path d={`M-5,${TOP - 14} L5,${TOP - 14} L0,${TOP - 7} Z`} />
        </g>
      </svg>
    </div>
  );
}
