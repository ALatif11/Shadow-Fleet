// Point-in-time arithmetic for the console (CLAUDE.md rule 1). Nothing here computes a metric: it only
// decides what was *knowable* at an instant. `observed_at` is the one key that answers that, so every
// filter in this file keys on it and never on `start`/`end`, which are when a thing happened.
import type { Event, IdentityInterval, Track } from "../contract";

export const DAY_MS = 86_400_000;
/** config.FEATURE_WINDOW_DAYS (plan ADR-11). Display only; the store's numbers come from Python. */
export const FEATURE_WINDOW_DAYS = 180;

export const clamp = (v: number, lo: number, hi: number) => (v < lo ? lo : v > hi ? hi : v);

/** A bundle date ("YYYY-MM-DD") or timestamp at 00:00:00.000 UTC. */
export function startOfDay(iso: string): number {
  return Date.parse(iso.slice(0, 10) + "T00:00:00Z");
}

/** The same day at 23:59:59.999 UTC: a cutoff T means "everything observed on or before T". */
export function endOfDay(iso: string): number {
  return startOfDay(iso) + DAY_MS - 1;
}

/** `[lo, hi]` of the 180-day feature window that ends at this cutoff. */
export function featureWindow(cutoff: string): [number, number] {
  const hi = endOfDay(cutoff);
  return [hi - FEATURE_WINDOW_DAYS * DAY_MS + 1, hi];
}

/** Was this record knowable at `asOf`? The whole point-in-time claim, in one line. */
export function isKnown(e: Pick<Event, "observed_at">, asOf: number): boolean {
  return Date.parse(e.observed_at) <= asOf;
}

/** Knowable events, oldest first (callers reverse for newest-first). */
export function eventsAsOf(events: Event[], asOf: number): Event[] {
  return events.filter((e) => isKnown(e, asOf)).sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
}

/** The identity in force at `asOf`, or null before the first interval. `end: null` means "still open". */
export function identityAt(intervals: IdentityInterval[], asOf: number): IdentityInterval | null {
  for (let i = intervals.length - 1; i >= 0; i--) {
    const iv = intervals[i];
    if (Date.parse(iv.start) <= asOf && (iv.end === null || Date.parse(iv.end) > asOf)) return iv;
  }
  return null;
}

/**
 * How many leading track points were observed at or before `asOf`.
 *
 * `track.t` is epoch **seconds** and contract-guaranteed ordered, so this is a binary search rather than a
 * filter: the scrubber calls it on every animation frame.
 */
export function trackCount(track: Track, asOf: number): number {
  const limit = Math.floor(asOf / 1000);
  let lo = 0;
  let hi = track.t.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (track.t[mid] <= limit) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export interface Segment {
  path: [number, number][];
  /** End of the segment in ms, so the map can dim anything older than the feature window. */
  t1: number;
}

/** Gap (seconds) that ends a segment. Longer and deck.gl would draw a straight line across the Baltic. */
const SEGMENT_GAP_S = 6 * 3600;

/** Track points `[from, to)` as drawable polylines, broken wherever the AIS record has a gap. */
export function segments(track: Track, from: number, to: number): Segment[] {
  const out: Segment[] = [];
  let path: [number, number][] = [];
  for (let i = Math.max(0, from); i < Math.min(to, track.t.length); i++) {
    if (path.length && track.t[i] - track.t[i - 1] > SEGMENT_GAP_S) {
      if (path.length > 1) out.push({ path, t1: track.t[i - 1] * 1000 });
      path = [];
    }
    path.push([track.lon[i], track.lat[i]]);
  }
  if (path.length > 1) out.push({ path, t1: track.t[Math.min(to, track.t.length) - 1] * 1000 });
  return out;
}
