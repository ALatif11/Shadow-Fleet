import { create } from "zustand";
import type { EventType, Manifest } from "../contract";
import { EVENT_TYPES } from "../lib/format";
import { clamp, DAY_MS, endOfDay, FEATURE_WINDOW_DAYS, startOfDay } from "../data/asof";

export interface FlyTarget {
  lon: number;
  lat: number;
  zoom?: number;
  key: number;
}

interface ConsoleState {
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
}));

export const ALL_TYPES = EVENT_TYPES;
