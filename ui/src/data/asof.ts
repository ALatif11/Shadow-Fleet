// Point-in-time helpers. The console shows a hull exactly as it was knowable at the as-of instant:
// an event appears only once its observed_at has passed (CLAUDE.md rule 1), a track point once it was received.
import type { Event, IdentityInterval, Track } from "../contract";

export const DAY_MS = 86_400_000;
export const FEATURE_WINDOW_DAYS = 180; // config.FEATURE_WINDOW_DAYS
export const HORIZON_DAYS = 182; // config.HORIZON_DAYS

/** Last millisecond of a UTC calendar day ("records observed on T count", same as the Python side). */
export const endOfDay = (isoDate: string) => Date.parse(`${isoDate}T00:00:00Z`) + DAY_MS - 1;
export const startOfDay = (isoDate: string) => Date.parse(`${isoDate}T00:00:00Z`);

/** Feature window for cutoff T: (T+1d-180d, T+1d), inclusive of T. */
export function featureWindow(cutoff: string): [number, number] {
  const hi = endOfDay(cutoff) + 1;
  return [hi - FEATURE_WINDOW_DAYS * DAY_MS, hi - 1];
}

export const isKnown = (e: Event, asOf: number) => Date.parse(e.observed_at) <= asOf;

export function eventsAsOf(events: Event[], asOf: number): Event[] {
  return events.filter((e) => isKnown(e, asOf));
}

/** Number of track points with t <= asOf (t is epoch seconds, sorted). */
export function trackCount(track: Track, asOf: number): number {
  const target = Math.floor(asOf / 1000);
  let lo = 0;
  let hi = track.t.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (track.t[mid] <= target) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export interface Segment {
  path: [number, number][];
  t0: number; // ms
  t1: number; // ms
}

/** Split the track into segments at time gaps (each DMA transit becomes its own line). */
export function segments(track: Track, from: number, to: number, maxGapS = 6 * 3600): Segment[] {
  const out: Segment[] = [];
  let cur: Segment | null = null;
  for (let i = from; i < to; i++) {
    const t = track.t[i];
    if (!cur || t - track.t[i - 1] > maxGapS) {
      if (cur && cur.path.length > 1) out.push(cur);
      cur = { path: [], t0: t * 1000, t1: t * 1000 };
    }
    cur.path.push([track.lon[i], track.lat[i]]);
    cur.t1 = t * 1000;
  }
  if (cur && cur.path.length > 1) out.push(cur);
  return out;
}

export function identityAt(identity: IdentityInterval[], asOf: number): IdentityInterval | null {
  let found: IdentityInterval | null = null;
  for (const iv of identity) {
    if (Date.parse(iv.start) <= asOf) found = iv;
  }
  return found;
}

export function clamp(v: number, lo: number, hi: number) {
  return Math.min(hi, Math.max(lo, v));
}

/** Bounding box of lon/lat points, padded, for framing the map. Null when there is nothing to frame. */
export function boundsOf(points: [number, number][], pad = 0.05): [[number, number], [number, number]] | null {
  if (points.length === 0) return null;
  let [w, s, e, n] = [Infinity, Infinity, -Infinity, -Infinity];
  for (const [lon, lat] of points) {
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
    w = Math.min(w, lon); e = Math.max(e, lon);
    s = Math.min(s, lat); n = Math.max(n, lat);
  }
  if (!Number.isFinite(w)) return null;
  return [[w - pad, s - pad], [e + pad, n + pad]];
}

/** Where the hull was at `asOf`, interpolated between the two surrounding fixes. */
export interface Fix {
  lon: number;
  lat: number;
  sog: number | null;
  /** True when the last fix is older than `staleAfterS`: the ship is outside DMA coverage, not sitting still. */
  stale: boolean;
  t: number;
}

export function positionAt(track: Track, asOf: number, staleAfterS = 6 * 3600): Fix | null {
  const n = trackCount(track, asOf);
  if (n === 0) return null;
  const i = n - 1;
  const ti = track.t[i];
  const last: Fix = { lon: track.lon[i], lat: track.lat[i], sog: track.sog[i] ?? null, stale: false, t: ti * 1000 };
  const next = i + 1 < track.t.length ? i + 1 : -1;
  if (next < 0) return { ...last, stale: asOf / 1000 - ti > staleAfterS };
  const gap = track.t[next] - ti;
  if (gap > staleAfterS) return { ...last, stale: asOf / 1000 - ti > staleAfterS };
  const f = gap === 0 ? 0 : (asOf / 1000 - ti) / gap;
  return {
    lon: last.lon + (track.lon[next] - last.lon) * f,
    lat: last.lat + (track.lat[next] - last.lat) * f,
    sog: last.sog,
    stale: false,
    t: asOf,
  };
}

/** Events whose observed_at falls in (from, to] — what just became knowable as the clock ran forward. */
export function crossedEvents(events: Event[], from: number, to: number): Event[] {
  if (to <= from) return [];
  return events.filter((e) => {
    const t = Date.parse(e.observed_at);
    return t > from && t <= to;
  });
}

/** When the hull is next seen after `asOf`, in ms. Null when there is nothing ahead. */
export function nextFixTime(track: Track, asOf: number): number | null {
  const n = trackCount(track, asOf);
  return n < track.t.length ? track.t[n] * 1000 : null;
}
