import { useEffect, useMemo, useState } from "react";
import { loadManifest } from "./data/api";
import { useDossier, useWatchlist } from "./data/hooks";
import { useConsole } from "./state/store";
import { TopBar } from "./components/TopBar";
import { Watchlist } from "./components/Watchlist";
import { MapView } from "./components/MapView";
import { Dossier } from "./components/Dossier";
import { Timeline } from "./components/Timeline";

export default function App() {
  const s = useConsole();
  const [fatal, setFatal] = useState<string | null>(null);

  useEffect(() => {
    loadManifest().then(s.init, (e: Error) => setFatal(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (fatal) return <Boot message={fatal} error />;
  if (!s.manifest) return <Boot message="LINKING TO BUNDLE…" />;
  return <Console />;
}

function Console() {
  const s = useConsole();
  const cuts = s.manifest!.cutoffs.map((c) => c.cutoff);
  const prevCut = cuts[cuts.indexOf(s.cutoff) - 1] ?? null;
  const wl = useWatchlist(s.cutoff, s.model, s.labelSet);
  const prev = useWatchlist(prevCut, s.model, s.labelSet);
  const dossier = useDossier(s.selected);
  const row = useMemo(() => wl.data?.rows.find((r) => r.hull_id === s.selected) ?? null, [wl.data, s.selected]);

  // First load: select rank 1 so the plot is never empty.
  useEffect(() => {
    if (!s.selected && wl.data?.rows.length) s.select(wl.data.rows[0].hull_id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wl.data]);

  useKeys(wl.data?.rows.map((r) => r.hull_id) ?? []);

  return (
    <div className="console">
      <div className="fx-grid" />
      <TopBar />
      <main className="deck">
        <Watchlist data={wl.data} previous={prev.data} loading={wl.loading} error={wl.error} />
        <MapView dossier={dossier.data} />
        <Dossier dossier={dossier.data} row={row} loading={dossier.loading} />
      </main>
      <Timeline dossier={dossier.data} />
      <footer className="footbar mono">
        <span>↑↓ HULL · [ ] CUTOFF · ←→ DAY (SHIFT ×30) · SPACE PLAY · H HINDSIGHT</span>
        <span>AIS: DANISH MARITIME AUTHORITY · EVENTS: GLOBAL FISHING WATCH (CC BY-NC) · LAND: NATURAL EARTH</span>
        <span>CONTRACT {s.manifest!.contract_version} · {s.manifest!.origin.toUpperCase()} · GENERATED {s.manifest!.generated_at.slice(0, 16)}Z</span>
      </footer>
      <div className="fx-scan" />
    </div>
  );
}

function useKeys(order: string[]) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
      const st = useConsole.getState();
      const i = st.selected ? order.indexOf(st.selected) : -1;
      const day = 86_400_000 * (e.shiftKey ? 30 : 1);
      switch (e.key) {
        case "ArrowDown":
          if (order.length) st.select(order[Math.min(order.length - 1, i + 1)]);
          break;
        case "ArrowUp":
          if (order.length) st.select(order[Math.max(0, i - 1)]);
          break;
        case "ArrowLeft":
          st.setAsOf(st.asOf - day);
          break;
        case "ArrowRight":
          st.setAsOf(st.asOf + day);
          break;
        case "[":
          st.stepCutoff(-1);
          break;
        case "]":
          st.stepCutoff(1);
          break;
        case "h":
        case "H":
          st.toggleHindsight();
          break;
        case " ":
          st.setPlaying(!st.playing);
          break;
        default:
          return;
      }
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [order]);
}

function Boot({ message, error }: { message: string; error?: boolean }) {
  return (
    <div className="boot">
      <div className="boot-ring" />
      <div className="logo big">SHADOW<b>FLEET</b></div>
      <div className={`boot-msg mono ${error ? "err" : "blink"}`}>{message}</div>
    </div>
  );
}
