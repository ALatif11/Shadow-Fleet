import { describe, expect, it } from "vitest";
import type { Event, Track } from "../contract";
import { boundsOf, crossedEvents, endOfDay, nextFixTime, eventsAsOf, featureWindow, identityAt, positionAt, segments, trackCount, DAY_MS } from "./asof";

const ev = (id: string, start: string, observed: string): Event => ({
  id, type: "gap", source: "gfw", start, end: observed, observed_at: observed, lat: 0, lon: 0,
  partner_hull_id: null, summary: "", attrs: {},
});

describe("as-of filtering", () => {
  it("hides an event until observed_at has passed, even if it started earlier", () => {
    const events = [ev("a", "2025-01-30T00:00:00Z", "2025-01-31T23:00:00Z"), ev("b", "2025-01-31T20:00:00Z", "2025-02-01T02:00:00Z")];
    expect(eventsAsOf(events, endOfDay("2025-01-31")).map((e) => e.id)).toEqual(["a"]);
    expect(eventsAsOf(events, endOfDay("2025-02-01")).map((e) => e.id)).toEqual(["a", "b"]);
  });

  it("feature window matches the Python rule (T inclusive, 180 days)", () => {
    const [lo, hi] = featureWindow("2025-06-30");
    expect(hi).toBe(endOfDay("2025-06-30"));
    expect((hi + 1 - lo) / DAY_MS).toBe(180);
  });

  it("counts track points up to and including asOf", () => {
    const t: Track = { t: [10, 20, 30, 30, 40], lon: [0, 0, 0, 0, 0], lat: [0, 0, 0, 0, 0], sog: [], draught: [] };
    expect(trackCount(t, 29_999)).toBe(2);
    expect(trackCount(t, 30_000)).toBe(4);
    expect(trackCount(t, 1e12)).toBe(5);
    expect(trackCount(t, 0)).toBe(0);
  });

  it("splits segments at time gaps", () => {
    const t: Track = { t: [0, 60, 120, 100_000, 100_060], lon: [0, 1, 2, 3, 4], lat: [0, 0, 0, 0, 0], sog: [], draught: [] };
    const s = segments(t, 0, 5);
    expect(s.map((x) => x.path.length)).toEqual([3, 2]);
  });

  it("returns the identity interval in force", () => {
    const iv = (start: string, name: string) => ({ start, end: null, mmsi: 1, imo: null, name, callsign: null, flag_iso3: null, source: "dma" as const });
    const ids = [iv("2024-01-01T00:00:00Z", "A"), iv("2025-01-01T00:00:00Z", "B")];
    expect(identityAt(ids, Date.parse("2024-06-01"))?.name).toBe("A");
    expect(identityAt(ids, Date.parse("2025-06-01"))?.name).toBe("B");
    expect(identityAt(ids, Date.parse("2023-06-01"))).toBeNull();
  });
});

describe("map framing", () => {
  const straits: [number, number][] = [[10.9, 55.6], [11.3, 57.1], [10.8, 57.8]];
  const worldwide: [number, number][] = [...straits, [-9.8, 43.5], [28.6, 60.3]];

  it("frames the Danish straits, not Europe, when given only the track", () => {
    const b = boundsOf(straits)!;
    expect(b[0][0]).toBeGreaterThan(10);
    expect(b[1][0]).toBeLessThan(12);
    expect(b[1][1] - b[0][1]).toBeLessThan(3); // under three degrees of latitude
  });

  it("widens once worldwide events are included", () => {
    const b = boundsOf(worldwide)!;
    expect(b[1][0] - b[0][0]).toBeGreaterThan(30);
  });

  it("ignores junk coordinates and empty input", () => {
    expect(boundsOf([])).toBeNull();
    expect(boundsOf([[NaN, NaN]])).toBeNull();
    const b = boundsOf([[NaN, 0], [10, 55], [11, 56]])!;
    expect(b[0][0]).toBeCloseTo(9.95);
  });
});

describe("model context", () => {
  it("never shows the model an event it could not know yet", async () => {
    const { evidence } = await import("./llm");
    const ev = (id: string, observed: string): Event => ({
      id, type: "gap", source: "gfw", start: observed, end: observed, observed_at: observed,
      lat: 1, lon: 1, partner_hull_id: null, summary: "went dark", attrs: {},
    });
    const d = {
      contract_version: "1.0.0", origin: "synthetic", hull_id: "fixture:0001", imo: null,
      header: { length_m: 1, beam_m: 1, dwt: 1, built_year: 2000, ship_type: "Tanker" },
      identity: [], track: { t: [], lon: [], lat: [], sog: [], draught: [] },
      events: [ev("E1", "2025-01-10T00:00:00Z"), ev("E2", "2025-03-01T00:00:00Z")],
      scores: [], sanctions: [],
    } as unknown as Parameters<typeof evidence>[0];
    const text = evidence(d, null, endOfDay("2025-01-31"), "2025-01-31", "lightgbm");
    expect(text).toContain("[E1]");
    expect(text).not.toContain("[E2]");
  });
});

describe("playback", () => {
  const track: Track = {
    t: [0, 600, 1200, 100_000],           // three fixes a minute apart, then a long gap
    lon: [10, 11, 12, 20],
    lat: [55, 55, 55, 57],
    sog: [12, 12, 12, 9],
    draught: [null, null, null, null],
  };

  it("interpolates between fixes so the ship moves smoothly", () => {
    expect(positionAt(track, 600_000)!.lon).toBeCloseTo(11);
    expect(positionAt(track, 900_000)!.lon).toBeCloseTo(11.5); // halfway between fix 2 and 3
    expect(positionAt(track, 300_000)!.lon).toBeCloseTo(10.5);
  });

  it("parks the ship at its last fix across a coverage gap, and says so", () => {
    const f = positionAt(track, 50_000_000)!;
    expect(f.lon).toBe(12);
    expect(f.stale).toBe(true);
  });

  it("has no position before the first fix", () => {
    expect(positionAt(track, -1)).toBeNull();
  });

  it("reports only the events the clock just passed", () => {
    const ev = (id: string, observed: string): Event => ({
      id, type: "gap", source: "gfw", start: observed, end: observed, observed_at: observed,
      lat: 1, lon: 1, partner_hull_id: null, summary: "", attrs: {},
    });
    const events = [ev("A", "2025-01-05T00:00:00Z"), ev("B", "2025-01-06T00:00:00Z")];
    const from = Date.parse("2025-01-05T12:00:00Z");
    const to = Date.parse("2025-01-06T12:00:00Z");
    expect(crossedEvents(events, from, to).map((e) => e.id)).toEqual(["B"]);
    expect(crossedEvents(events, to, from)).toEqual([]); // scrubbing backwards pings nothing
  });
});

describe("coverage gaps", () => {
  const track: Track = { t: [0, 600, 100_000], lon: [10, 11, 20], lat: [55, 55, 57], sog: [1, 1, 1], draught: [] };
  it("reports when the ship is next seen, so playback can skip the empty stretch", () => {
    expect(nextFixTime(track, 300_000)).toBe(600_000);      // next fix inside the passage
    expect(nextFixTime(track, 700_000)).toBe(100_000_000);  // next fix across the gap
    expect(nextFixTime(track, 200_000_000)).toBeNull();
  });
});
