import { useEffect, useMemo, useState } from "react";
import { loadManifest } from "./data/api";
import { useDossier, useWatchlist } from "./data/hooks";
import { useConsole } from "./state/store";
import { useClock } from "./data/clock";
import { TopBar } from "./components/TopBar";
import { Watchlist } from "./components/Watchlist";
import { MapView } from "./components/MapView";
import { Dossier } from "./components/Dossier";
import { Timeline } from "./components/Timeline";
import { Rail } from "./components/Rail";
import { Brief } from "./components/Brief";
import { Chat } from "./components/Chat";
import { Guide } from "./components/Guide";
import { Tooltip } from "./components/Tooltip";
import { DOCK, usePick, type DockId } from "./state/store";
import { DockSlot } from "./components/Panel";
import { useMedia } from "./lib/motion";

export default function App() {
  const s = usePick("manifest", "init");
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
  const s = usePick("manifest", "cutoff", "model", "labelSet", "selected", "select", "layout");
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
  useClock();

  const { showList, dock, pinned } = s.layout;
  const wide = useMedia("(min-width: 1760px)");
  const body = (id: DockId) =>
    id === "dossier" ? <Dossier dossier={dossier.data} row={row} loading={dossier.loading} />
    : id === "brief" ? <Brief dossier={dossier.data} row={row} />
    : id === "chat" ? <Chat dossier={dossier.data} row={row} />
    : <Guide />;

  return (
    <div className="console">
      <TopBar />
      <div className="work">
        <Rail wide={wide} />
        <main className="deck">
          {showList && <Watchlist data={wl.data} previous={prev.data} loading={wl.loading} error={wl.error} />}
          <MapView dossier={dossier.data} />
          {wide && pinned && (
            <DockSlot id={pinned} pinned>
              {body(pinned)}
            </DockSlot>
          )}
          {dock && (
            <DockSlot id={dock} canPin={wide}>
              {body(dock)}
            </DockSlot>
          )}
        </main>
      </div>
      <Timeline dossier={dossier.data} />
      <footer className="footbar mono">
        <span className="keys-hint">
          <kbd>↑↓</kbd> ship <kbd>[ ]</kbd> month <kbd>space</kbd> play <kbd>H</kbd> hindsight <kbd>?</kbd> explain <kbd>1–6</kbd> panels
        </span>
        <span className="sources" data-tip="AIS tracks: Danish Maritime Authority. Worldwide events: Global Fishing Watch (CC BY-NC). Coastline: Natural Earth.">
          Sources: DMA · GFW · Natural Earth
        </span>
        <span className="build">
          {s.manifest!.origin} bundle · contract {s.manifest!.contract_version} · built {s.manifest!.generated_at.slice(0, 10)}
        </span>
      </footer>
      <Tooltip />
    </div>
  );
}

function useKeys(order: string[]) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
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
        case "?":
          st.setExplain(!st.explain);
          break;
        case " ":
          st.setPlaying(!st.playing);
          break;
        case "1":
          st.toggleList();
          break;
        case "2":
          st.toggleMapOnly();
          break;
        default: {
          const d = DOCK.find((x) => x.key === e.key);
          if (d) st.openDock(d.id);
          else return;
        }
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
      <div className="boot-ring" aria-hidden="true" />
      <div className="logo big">SHADOW<b>FLEET</b></div>
      <div className={`boot-msg ${error ? "err" : "wait"}`} role={error ? "alert" : "status"}>{message}</div>
    </div>
  );
}
