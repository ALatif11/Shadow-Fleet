import { DOCK, usePick } from "../state/store";

/**
 * Left bar. 1 shows or hides the watchlist, 2 clears everything but the map, 3 to 6 switch the dock.
 * Clicking the tab that is already open closes it, so the map gets the room.
 */
export function Rail({ wide }: { wide: boolean }) {
  const { layout, beforeMapOnly, openDock, toggleList, toggleMapOnly, explain, setExplain } = usePick("layout", "beforeMapOnly", "openDock", "toggleList", "toggleMapOnly", "explain", "setExplain");
  const shown = (id: string) => layout.dock === id || (wide && layout.pinned === id);
  const items = [
    { key: "1", label: "Watchlist", on: layout.showList, tip: "Show or hide the ranked list of ships (1)", act: toggleList },
    {
      key: "2",
      label: "Map only",
      on: beforeMapOnly !== null,
      tip: beforeMapOnly ? "Bring the panels back (2)" : "Hide every panel and give the map the whole screen (2)",
      act: toggleMapOnly,
    },
    ...DOCK.map((d) => ({
      key: d.key,
      label: d.label,
      on: shown(d.id),
      tip: `${d.label} panel (${d.key})${layout.pinned === d.id && wide ? ": pinned" : ""}`,
      act: () => openDock(d.id),
    })),
  ];
  return (
    <nav className="rail" aria-label="Panels">
      <div className="rail-mark" aria-hidden="true">SF</div>
      {items.map((it, i) => (
        <button key={it.key} className={`rail-btn ${it.on ? "on" : ""} ${i === 2 ? "gap" : ""}`} aria-pressed={it.on} data-tip={it.tip} onClick={it.act}>
          <span className="rail-num">{it.key}</span>
          <span className="rail-label">{it.label}</span>
        </button>
      ))}
      <button
        className={`rail-btn explain ${explain ? "on" : ""}`}
        aria-pressed={explain}
        data-tip="Explain mode (?): a plain-English note on every panel, and the raw field names behind each number"
        onClick={() => setExplain(!explain)}
      >
        <span className="rail-num">?</span>
        <span className="rail-label">Explain</span>
      </button>
    </nav>
  );
}
