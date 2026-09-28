// The console's only real logic: what was knowable at an instant. A boundary error here shows the viewer a
// future event as if it were known, which is the one bug the whole point-in-time design exists to prevent.
import { describe, expect, it } from "vitest";
import type { Event, IdentityInterval, Track } from "../contract";
import { endOfDay, eventsAsOf, featureWindow, identityAt, isKnown, segments, startOfDay, trackCount } from "./asof";

const ev = (id: string, observed: string, start = observed): Event =>
  ({ id, observed_at: observed, start, end: start, type: "gap", source: "gfw", lat: 0, lon: 0,
     partner_hull_id: null, summary: "", attrs: {} }) as Event;

const track = (seconds: number[]): Track =>
  ({ t: seconds, lon: seconds.map((_, i) => i), lat: seconds.map(() => 56), sog: seconds.map(() => 1),
     draught: seconds.map(() => 10) });

const T = (iso: string) => Date.parse(iso);

describe("as-of", () => {
  it("a cutoff includes the whole of its own day and nothing after", () => {
    expect(endOfDay("2025-07-31")).toBe(T("2025-07-31T23:59:59.999Z"));
    expect(startOfDay("2025-07-31T14:00:00Z")).toBe(T("2025-07-31T00:00:00Z"));
  });

  it("the feature window is exactly 180 days ending at the cutoff", () => {
    const [lo, hi] = featureWindow("2025-07-31");
    expect(hi).toBe(endOfDay("2025-07-31"));
    expect((hi - lo + 1) / 86_400_000).toBe(180);
  });

  it("knowability keys on observed_at, never on when the event happened", () => {
    const late = ev("E1", "2025-08-05T00:00:00Z", "2025-06-01T00:00:00Z"); // happened early, reported late
    expect(isKnown(late, endOfDay("2025-07-31"))).toBe(false);
    expect(isKnown(late, endOfDay("2025-08-05"))).toBe(true);
  });

  it("eventsAsOf drops the unknowable and orders by when it happened", () => {
    const asOf = endOfDay("2025-07-31");
    const got = eventsAsOf([
      ev("E2", "2025-07-30T00:00:00Z", "2025-07-02T00:00:00Z"),
      ev("E3", "2025-08-09T00:00:00Z"),
      ev("E1", "2025-07-05T00:00:00Z", "2025-07-01T00:00:00Z"),
    ], asOf).map((e) => e.id);
    expect(got).toEqual(["E1", "E2"]);
  });

  it("identityAt is half-open, so a rename does not show two names on its own day", () => {
    const ivs: IdentityInterval[] = [
      { start: "2025-01-01T00:00:00Z", end: "2025-06-01T00:00:00Z", mmsi: 1, imo: 9000019,
        name: "OLD", callsign: null, flag_iso3: null, source: "dma" },
      { start: "2025-06-01T00:00:00Z", end: null, mmsi: 1, imo: 9000019,
        name: "NEW", callsign: null, flag_iso3: null, source: "dma" },
    ];
    expect(identityAt(ivs, T("2025-06-01T00:00:00Z"))?.name).toBe("NEW");
    expect(identityAt(ivs, T("2025-05-31T23:59:59Z"))?.name).toBe("OLD");
    expect(identityAt(ivs, T("2024-12-31T00:00:00Z"))).toBeNull();
  });

  it("trackCount takes epoch-second points up to and including the instant", () => {
    const t = track([1_000, 2_000, 3_000]); // seconds; asOf is milliseconds
    expect(trackCount(t, 2_000_999)).toBe(2); // ms inside second 2000 still includes it
    expect(trackCount(t, 1_999_999)).toBe(1);
    expect(trackCount(t, 999_999)).toBe(0);
    expect(trackCount(t, 9_999_000)).toBe(3);
    expect(trackCount(track([]), 9_999_000)).toBe(0);
  });

  it("segments break on an AIS gap instead of drawing a line across the sea", () => {
    const hour = 3_600;
    const segs = segments(track([0, hour, 2 * hour, 40 * hour, 41 * hour]), 0, 5);
    expect(segs.map((s) => s.path.length)).toEqual([3, 2]);
    expect(segs[1].t1).toBe(41 * hour * 1000);
  });

  it("a lone point draws nothing, and a range beyond the track is clamped", () => {
    expect(segments(track([0]), 0, 1)).toEqual([]);
    expect(segments(track([0, 60]), 0, 99)[0].path.length).toBe(2);
  });
});
