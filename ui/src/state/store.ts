import { create } from "zustand";
import { useShallow } from "zustand/react/shallow";
import type { EventType, Manifest } from "../contract";
import { EVENT_TYPES } from "../lib/format";
import { clamp, DAY_MS, endOfDay, FEATURE_WINDOW_DAYS, startOfDay } from "../data/asof";

/** Panels that share the right-hand dock. The watchlist and the map have fixed places. */
export const DOCK = [
  { id: "dossier", label: "Dossier", key: "3" },
  { id: "brief", label: "Brief", key: "4" },
  { id: "chat", label: "Ask", key: "5" },
  { id: "guide", label: "Guide", key: "6" },
] as const;
export type DockId = (typeof DOCK)[number]["id"];

export interface Layout {
  showList: boolean;
  /** The dock's open tab; null collapses the dock. */
  dock: DockId | null;
  /** A second dock panel kept open beside the first (wide screens only). */
  pinned: DockId | null;
}
// First visit opens the guide; after that the viewer's own layout comes back.
const FIRST_VISIT: Layout = { showList: true, dock: "guide", pinned: null };

// Per-viewer convenience only; the console works with an empty or throwing localStorage.
function loadLayout(): Layout {
  try {
    const raw = JSON.parse(localStorage.getItem("sf.layout") ?? "null") as Partial<Layout> | null;
    if (!raw) return FIRST_VISIT;
    const ok = (id: unknown) => DOCK.some((d) => d.id === id);
    return {
      showList: raw.showList !== false,
      dock: ok(raw.dock) ? raw.dock! : null,
      pinned: ok(raw.pinned) ? raw.pinned! : null,
    };
  } catch {
    return FIRST_VISIT;
  }
}
function saveLayout(l: Layout) {
  try {
    localStorage.setItem("sf.layout", JSON.stringify(l));
  } catch {
    /* private window, blocked storage: the layout just does not persist */
  }
}

export interface FlyTarget {
  lon: number;
  lat: number;
  zoom?: number;
  key: number;
}

export interface ConsoleState {
  manifest: Manifest | null;
  cutoff: string;
  model: string;
  labelSet: string;
  selected: string | null;
  /** The instant the console is showing (ms). Never past the cutoff unless hindsight is on. */
  asOf: number;
  hindsight: boolean;
  b1Only: boolean;
  query: string;
  playing: boolean;
  hiddenTypes: Set<EventType>;
  focusEvent: string | null;
  fly: FlyTarget | null;
  layout: Layout;
  /** The layout to restore when leaving map-only view. */
  beforeMapOnly: Layout | null;
  explain: boolean;
  /** Playback rate in days of ship-time per real second. */
  speed: number;
  follow: boolean;
  /** Set by the map: when the ship is outside coverage, the moment it is next seen. */
  nextFix: number | null;

  init: (m: Manifest) => void;
  setCutoff: (c: string) => void;
  stepCutoff: (d: number) => void;
  setModel: (m: string) => void;
  setLabelSet: (l: string) => void;
  select: (hull: string | null) => void;
  setAsOf: (ms: number) => void;
  toggleHindsight: () => void;
  setB1Only: (v: boolean) => void;
  setQuery: (q: string) => void;
  setPlaying: (v: boolean) => void;
  toggleType: (t: EventType) => void;
  focus: (eventId: string | null, lon?: number | null, lat?: number | null) => void;
  flyTo: (lon: number, lat: number, zoom?: number) => void;
  setLayout: (l: Partial<Layout>) => void;
  /** Rail click: open a dock tab, or collapse the dock / unpin if that tab is already showing. */
  openDock: (id: DockId) => void;
  pin: () => void;
  toggleList: () => void;
  toggleMapOnly: () => void;
  setExplain: (v: boolean) => void;
  setSpeed: (v: number) => void;
  setFollow: (v: boolean) => void;
  setNextFix: (v: number | null) => void;
}

/** Earliest and latest instant the scrubber may reach. */
export function asOfBounds(s: Pick<ConsoleState, "manifest" | "cutoff" | "hindsight">): [number, number] {
  if (!s.manifest) return [0, 0];
  const lo = startOfDay(s.manifest.window_start) - FEATURE_WINDOW_DAYS * DAY_MS;
  const hi = s.hindsight ? endOfDay(s.manifest.window_end) : endOfDay(s.cutoff);
  return [lo, hi];
}

export const useConsole = create<ConsoleState>((set, get) => ({
  manifest: null,
  cutoff: "",
  model: "",
  labelSet: "",
  selected: null,
  asOf: 0,
  hindsight: false,
  b1Only: false,
  query: "",
  playing: false,
  hiddenTypes: new Set(),
  focusEvent: null,
  fly: null,
  layout: loadLayout(),
  beforeMapOnly: null,
  explain: false,
  speed: 2,
  follow: true,
  nextFix: null,

  init: (m) => {
    const last = m.cutoffs[m.cutoffs.length - 1].cutoff;
    set({ manifest: m, cutoff: last, model: m.default_model, labelSet: m.default_label_set, asOf: endOfDay(last) });
  },
  setCutoff: (c) => set({ cutoff: c, asOf: endOfDay(c), playing: false }),
  stepCutoff: (d) => {
    const { manifest, cutoff } = get();
    if (!manifest) return;
    const cuts = manifest.cutoffs.map((x) => x.cutoff);
    const i = clamp(cuts.indexOf(cutoff) + d, 0, cuts.length - 1);
    get().setCutoff(cuts[i]);
  },
  setModel: (model) => set({ model }),
  setLabelSet: (labelSet) => set({ labelSet }),
  select: (selected) => set({ selected, focusEvent: null }),
  setAsOf: (ms) => {
    const [lo, hi] = asOfBounds(get());
    set({ asOf: clamp(ms, lo, hi) });
  },
  toggleHindsight: () => {
    const hindsight = !get().hindsight;
    set({ hindsight });
    if (!hindsight) get().setAsOf(get().asOf); // re-clamp to the cutoff
  },
  setB1Only: (b1Only) => set({ b1Only }),
  setQuery: (query) => set({ query }),
  setPlaying: (playing) => set({ playing }),
  toggleType: (t) => {
    const hiddenTypes = new Set(get().hiddenTypes);
    if (hiddenTypes.has(t)) hiddenTypes.delete(t);
    else hiddenTypes.add(t);
    set({ hiddenTypes });
  },
  focus: (focusEvent, lon, lat) => {
    set({ focusEvent });
    if (lon != null && lat != null) get().flyTo(lon, lat);
  },
  flyTo: (lon, lat, zoom) => set({ fly: { lon, lat, zoom, key: Date.now() } }),
  setLayout: (l) => {
    const layout = { ...get().layout, ...l };
    if (layout.pinned === layout.dock) layout.pinned = null;
    saveLayout(layout);
    set({ layout, beforeMapOnly: null });
  },
  openDock: (id) => {
    const { dock, pinned } = get().layout;
    if (id === pinned) get().setLayout({ pinned: null });
    else get().setLayout({ dock: id === dock ? null : id });
  },
  pin: () => {
    const { dock, pinned } = get().layout;
    if (!dock) return;
    // Pinning keeps this panel and moves the dock on to the next one, so both are visible at once.
    const next = DOCK.find((d) => d.id !== dock && d.id !== pinned)?.id ?? null;
    get().setLayout({ pinned: dock, dock: next });
  },
  toggleList: () => get().setLayout({ showList: !get().layout.showList }),
  toggleMapOnly: () => {
    const { layout, beforeMapOnly } = get();
    if (beforeMapOnly) return get().setLayout(beforeMapOnly);
    const bare = { showList: false, dock: null, pinned: null };
    saveLayout(bare);
    set({ layout: bare, beforeMapOnly: layout });
  },
  setExplain: (explain) => set({ explain }),
  setSpeed: (speed) => set({ speed }),
  setFollow: (follow) => set({ follow }),
  setNextFix: (nextFix) => set((st) => (st.nextFix === nextFix ? st : { nextFix })),
}));

export const SPEEDS = [0.5, 2, 7, 30];

export const ALL_TYPES = EVENT_TYPES;

/**
 * Subscribe to a few fields only. The playback clock rewrites `asOf` twenty times a second, and a component
 * holding the whole store re-renders on every one of those ticks even when it never reads the time.
 */
export function usePick<K extends keyof ConsoleState>(...keys: K[]): Pick<ConsoleState, K> {
  return useConsole(useShallow((st) => Object.fromEntries(keys.map((k) => [k, st[k]])) as Pick<ConsoleState, K>));
}
