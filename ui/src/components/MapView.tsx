import { useEffect, useMemo, useRef, useState } from "react";
import DeckGL from "@deck.gl/react";
import { FlyToInterpolator, WebMercatorViewport, type MapViewState, type PickingInfo } from "@deck.gl/core";
import { GeoJsonLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { FeatureCollection } from "geojson";
import type { Dossier, Event, EventType } from "../contract";
import { useConsole } from "../state/store";
import { boundsOf, crossedEvents, featureWindow, isKnown, nextFixTime, positionAt, segments, type Fix, type Segment } from "../data/asof";
import { rgba } from "../lib/colors";
import { EVENT_LABEL, EVENT_TYPES, fmtDateTime, SOURCE_LABEL } from "../lib/format";
import { EVENT_HELP } from "../lib/plain";
import { Panel } from "./Panel";

const HOME: MapViewState = { longitude: 11.2, latitude: 56.3, zoom: 5.4, pitch: 0, bearing: 0 };

/**
 * Natural Earth land (public domain), bundled via world-atlas: no tile server, works offline.
 * 50m is loaded first and used while zoomed out; 10m (5x the geometry, 3 MB) is fetched only when the
 * view is close enough for the extra coastline detail to be visible. Both are cached after first use.
 */
const DETAIL_ZOOM = 7;
const landCache = new Map<string, FeatureCollection>();

function useLand(zoom: number): FeatureCollection | null {
  const want = zoom >= DETAIL_ZOOM ? "10m" : "50m";
  const [, bump] = useState(0);
  useEffect(() => {
    if (landCache.has(want)) return;
    let live = true;
    Promise.all([
      want === "10m" ? import("world-atlas/land-10m.json") : import("world-atlas/land-50m.json"),
      import("topojson-client"),
    ]).then(([topo, tc]) => {
      const t = (topo as { default: unknown }).default as Parameters<typeof tc.feature>[0];
      const obj = (t as unknown as { objects: { land: Parameters<typeof tc.feature>[1] } }).objects.land;
      landCache.set(want, tc.feature(t, obj) as unknown as FeatureCollection);
      if (live) bump((n) => n + 1);
    });
    return () => {
      live = false;
    };
  }, [want]);
  return landCache.get(want) ?? landCache.get("50m") ?? null;
}

const GRATICULE: [number, number][][] = (() => {
  const lines: [number, number][][] = [];
  for (let lon = -180; lon <= 180; lon += 2) lines.push([[lon, -80], [lon, 0], [lon, 80]]);
  for (let lat = -80; lat <= 80; lat += 1) lines.push([[-180, lat], [0, lat], [180, lat]]);
  return lines;
})();

interface Split {
  past: Segment[];
  future: Segment[];
}

/**
 * Segmenting a 5,000-point track on every playback frame was the expensive part. The whole track is cut
 * into passages once per hull; each frame only clips the passage the ship is currently inside.
 */
function useSegments(d: Dossier | null) {
  return useMemo(() => (d ? segments(d.track, 0, d.track.t.length) : []), [d]);
}

function clip(all: Segment[], d: Dossier | null, asOf: number, hindsight: boolean): Split {
  if (!d || all.length === 0) return { past: [], future: [] };
  const cut = asOf;
  const past: Segment[] = [];
  const future: Segment[] = [];
  for (const seg of all) {
    if (seg.t1 <= cut) past.push(seg);
    else if (seg.t0 > cut) {
      if (hindsight) future.push(seg);
    } else {
      // The ship is inside this passage: split it at the playhead.
      const n = Math.max(2, Math.round((seg.path.length * (cut - seg.t0)) / Math.max(1, seg.t1 - seg.t0)));
      past.push({ path: seg.path.slice(0, n), t0: seg.t0, t1: cut });
      if (hindsight) future.push({ path: seg.path.slice(n - 1), t0: cut, t1: seg.t1 });
    }
  }
  return { past, future };
}

/** Events that crossed the playhead recently, for the expanding ring and the ticker. */
interface Ping {
  event: Event;
  at: number;
}
const PING_MS = 1600;
const pingProgress = (p: Ping, lag: number) => Math.min(1, Math.max(0, (performance.now() - p.at) / PING_MS - lag) / (1 - lag));
const pingEase = (p: Ping, lag: number) => 1 - (1 - pingProgress(p, lag)) ** 3;

function usePings(dossier: Dossier | null, asOf: number) {
  const [pings, setPings] = useState<Ping[]>([]);
  const prev = useRef(asOf);
  const hull = dossier?.hull_id;
  useEffect(() => {
    setPings([]);
    prev.current = asOf;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hull]);
  useEffect(() => {
    const from = prev.current;
    prev.current = asOf;
    if (!dossier || asOf <= from) return;
    const fresh = crossedEvents(dossier.events, from, asOf);
    if (fresh.length === 0) return;
    const now = performance.now();
    setPings((cur) => [...cur.filter((p) => now - p.at < PING_MS), ...fresh.map((event) => ({ event, at: now }))]);
    const id = setTimeout(() => setPings((cur) => cur.filter((p) => performance.now() - p.at < PING_MS)), PING_MS + 60);
    return () => clearTimeout(id);
  }, [asOf, dossier]);
  return pings;
}

function eventsAll(d: Dossier, asOf: number, hindsight: boolean): Event[] {
  return d.events.filter((e) => e.lat != null && e.lon != null && (hindsight || isKnown(e, asOf)));
}

export function MapView({ dossier }: { dossier: Dossier | null }) {
  const s = useConsole();
  const [view, setView] = useState<MapViewState>(HOME);
  const canvas = useRef<HTMLDivElement>(null);
  const land = useLand(view.zoom);

  useEffect(() => {
    if (!s.fly) return;
    setView((v) => ({
      ...v,
      longitude: s.fly!.lon,
      latitude: s.fly!.lat,
      zoom: s.fly!.zoom ?? Math.max(v.zoom, 6.5),
      transitionDuration: 1200,
      transitionInterpolator: new FlyToInterpolator({ speed: 1.6 }),
    }));
  }, [s.fly]);

  const allSegments = useSegments(dossier);
  const track = useMemo(() => clip(allSegments, dossier, s.asOf, s.hindsight), [allSegments, dossier, s.asOf, s.hindsight]);
  const fix: Fix | null = useMemo(() => (dossier ? positionAt(dossier.track, s.asOf) : null), [dossier, s.asOf]);
  const pings = usePings(dossier, s.asOf);

  // Tell the clock when the ship is next seen, so playback can skip coverage gaps.
  const setNextFix = s.setNextFix;
  useEffect(() => {
    setNextFix(dossier && (!fix || fix.stale) ? nextFixTime(dossier.track, s.asOf) : null);
  }, [dossier, fix, s.asOf, setNextFix]);
  const [fwLo] = useMemo(() => featureWindow(s.cutoff), [s.cutoff]);

  // Keep the ship in view during playback. No transition: the fixes are dense enough to read as motion.
  useEffect(() => {
    if (!s.playing || !s.follow || !fix || fix.stale) return;
    setView((v) => ({ ...v, longitude: fix.lon, latitude: fix.lat, transitionDuration: 0 }));
  }, [fix, s.playing, s.follow]);

  const events = useMemo(() => {
    if (!dossier) return { known: [] as Event[], future: [] as Event[] };
    const vis = dossier.events.filter((e) => e.lat != null && e.lon != null && !s.hiddenTypes.has(e.type));
    return {
      known: vis.filter((e) => isKnown(e, s.asOf)),
      future: s.hindsight ? vis.filter((e) => !isKnown(e, s.asOf)) : [],
    };
  }, [dossier, s.asOf, s.hindsight, s.hiddenTypes]);

  const overlays = s.manifest?.overlays ?? [];
  const focused = dossier?.events.find((e) => e.id === s.focusEvent) ?? null;
  const evColor = (t: EventType, a: number) => rgba(`--ev-${t}`, a);

  // Basemap: heavy and never changes. Built once, so panning does not rebuild the land geometry.
  const baseLayers = useMemo(() => [
    new PathLayer({ id: "graticule", data: GRATICULE, getPath: (d) => d, getColor: rgba("--map-graticule", 18), widthMinPixels: 1 }),
    // One pass over the coastline, not three: Natural Earth 10m is ~3 MB of geometry and this layer is
    // redrawn on every pan frame. ponytail: if it still drags on a weak GPU, drop to world-atlas land-50m.
    land && new GeoJsonLayer({
      id: "land",
      data: land,
      filled: true,
      stroked: true,
      getFillColor: rgba("--map-land"),
      getLineColor: rgba("--map-land-edge", 200),
      lineWidthMinPixels: 1,
    }),
    new PolygonLayer({
      id: "overlay-box",
      data: overlays.filter((o) => o.bbox),
      getPolygon: (o) => {
        const [a, b, c, d] = o.bbox!;
        return [[a, b], [c, b], [c, d], [a, d]];
      },
      filled: true,
      stroked: true,
      getFillColor: rgba("--map-overlay", 14),
      getLineColor: rgba("--map-overlay", 150),
      lineWidthMinPixels: 1,
    }),
    new ScatterplotLayer({
      id: "ports",
      data: overlays.filter((o) => o.kind === "port"),
      getPosition: (o) => [o.lon, o.lat],
      getRadius: 5,
      radiusUnits: "pixels",
      filled: false,
      stroked: true,
      getLineColor: rgba("--map-port", 220),
      lineWidthMinPixels: 1.5,
    }),
    new TextLayer({
      id: "overlay-labels",
      data: overlays,
      getPosition: (o) => [o.bbox ? o.bbox[2] : o.lon, o.bbox ? o.bbox[3] : o.lat],
      getText: (o) => `${o.name.toUpperCase()}${o.placeholder ? " *" : ""}`,
      getSize: 11,
      getColor: (o) => (o.kind === "port" ? rgba("--map-port", 220) : rgba("--map-overlay", 220)),
      getPixelOffset: [8, -8],
      getTextAnchor: "start",
      fontFamily: "JetBrains Mono, monospace",
      characterSet: "auto",
    }),
  ], [land, overlays]);

  const layers = [
    ...baseLayers,
    new PathLayer<Segment>({
      id: "track-future",
      data: track.future,
      getPath: (d) => d.path,
      getColor: rgba("--map-future", 70),
      widthMinPixels: 1,
    }),
    new PathLayer<Segment>({
      id: "track-glow",
      data: track.past,
      getPath: (d) => d.path,
      getColor: (d) => (d.t1 >= fwLo ? rgba("--map-track", 34) : rgba("--map-track-old", 16)),
      widthMinPixels: 5,
      capRounded: true,
      jointRounded: true,
      updateTriggers: { getColor: [fwLo] },
    }),
    new PathLayer<Segment>({
      id: "track",
      data: track.past,
      getPath: (d) => d.path,
      getColor: (d) => (d.t1 >= fwLo ? rgba("--map-track", 230) : rgba("--map-track-old", 150)),
      widthMinPixels: 1.4,
      capRounded: true,
      jointRounded: true,
      updateTriggers: { getColor: [fwLo] },
    }),
    new ScatterplotLayer<Event>({
      id: "events-future",
      data: events.future,
      getPosition: (e) => [e.lon!, e.lat!],
      getRadius: 5,
      radiusUnits: "pixels",
      filled: false,
      stroked: true,
      getLineColor: rgba("--map-future", 120),
      lineWidthMinPixels: 1,
    }),
    new ScatterplotLayer<Event>({
      id: "events-halo",
      data: events.known,
      getPosition: (e) => [e.lon!, e.lat!],
      getRadius: 14,
      radiusUnits: "pixels",
      getFillColor: (e) => evColor(e.type, 30),
    }),
    new ScatterplotLayer<Event>({
      id: "events",
      data: events.known,
      pickable: true,
      getPosition: (e) => [e.lon!, e.lat!],
      getRadius: (e) => (e.id === s.focusEvent ? 7 : 4),
      radiusUnits: "pixels",
      getFillColor: (e) => evColor(e.type, 235),
      stroked: true,
      getLineColor: rgba("--map-bg"),
      lineWidthMinPixels: 1,
      onClick: (info: PickingInfo<Event>) => info.object && s.focus(info.object.id),
      updateTriggers: { getRadius: [s.focusEvent] },
    }),
    focused && focused.lon != null && new ScatterplotLayer({
      id: "focus-ring",
      data: [focused],
      getPosition: (e: Event) => [e.lon!, e.lat!],
      getRadius: 13,
      radiusUnits: "pixels",
      filled: false,
      stroked: true,
      getLineColor: evColor(focused.type, 220),
      lineWidthMinPixels: 1.5,
    }),
    fix && new ScatterplotLayer({
      id: "head-ring",
      data: [10, 17],
      getPosition: () => [fix.lon, fix.lat],
      getRadius: (r: number) => r,
      radiusUnits: "pixels",
      filled: false,
      stroked: true,
      getLineColor: (r: number) => rgba("--map-track", r > 12 ? 70 : 150),
      lineWidthMinPixels: 1,
      updateTriggers: { getPosition: [fix.lon, fix.lat] },
    }),
    fix && new ScatterplotLayer({
      id: "head",
      data: [fix],
      getPosition: (f: Fix) => [f.lon, f.lat],
      getRadius: 5,
      radiusUnits: "pixels",
      getFillColor: fix.stale ? rgba("--map-track-old") : rgba("--ink"),
      stroked: true,
      getLineColor: rgba("--map-track"),
      lineWidthMinPixels: 2,
    }),
    // Incidents announce themselves as the clock passes them.
    // Two rings per incident, the second a beat behind, each fast out and slow to settle (ease-out cubic).
    pings.length > 0 && new ScatterplotLayer<[Ping, number]>({
      id: "pings",
      data: pings.filter((p) => p.event.lon != null).flatMap((p) => [[p, 0], [p, 0.22]] as [Ping, number][]),
      getPosition: ([p]) => [p.event.lon!, p.event.lat!],
      getRadius: ([p, lag]) => 6 + 46 * pingEase(p, lag),
      radiusUnits: "pixels",
      filled: false,
      stroked: true,
      getLineColor: ([p, lag]) => evColor(p.event.type, 255 * (1 - pingProgress(p, lag)) * (lag ? 0.6 : 1)),
      lineWidthMinPixels: 2,
      updateTriggers: { getRadius: s.asOf, getLineColor: s.asOf },
    }),
  ];

  /** Frame a set of points in the actual canvas, rather than guessing a zoom from a longitude span. */
  const fitTo = (pts: [number, number][]) => {
    const box = canvas.current?.getBoundingClientRect();
    const bounds = boundsOf(pts);
    if (!box || !bounds) return;
    const fitted = new WebMercatorViewport({ width: box.width, height: box.height }).fitBounds(bounds, { padding: 48 });
    s.flyTo(fitted.longitude, fitted.latitude, Math.min(fitted.zoom, 11));
  };

  /** "Fit this ship" means where the ship is, i.e. its track. Its GFW events are global and would frame Europe. */
  const fitShip = () => {
    if (!dossier) return;
    const n = dossier.track.t.length;
    const visible = track.past.flatMap((seg) => seg.path);
    const all: [number, number][] = Array.from({ length: n }, (_, i) => [dossier.track.lon[i], dossier.track.lat[i]]);
    const pts = visible.length ? visible : all;
    if (pts.length) fitTo(pts);
    else fitTo(dossier.events.filter((e) => e.lon != null).map((e) => [e.lon!, e.lat!] as [number, number]));
  };

  /** Everything known about the hull, worldwide: gaps, encounters, port calls. */
  const fitWorld = () => {
    if (!dossier) return;
    const evs = eventsAll(dossier, s.asOf, s.hindsight).map((e) => [e.lon!, e.lat!] as [number, number]);
    const trk = track.past.flatMap((seg) => seg.path);
    fitTo([...evs, ...trk]);
  };

  // A new ship means a new place: frame it. Keyed on the hull so scrubbing never moves the camera.
  const hull = dossier?.hull_id;
  useEffect(() => {
    if (hull) fitShip();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hull]);

  // The key starts closed on small screens, where it would cover most of the map.
  const [keyOpen, setKeyOpen] = useState(() => !matchMedia("(max-width: 1150px)").matches);
  const hidden = s.hiddenTypes.size;

  return (
    <Panel
      title="Map"
      code="02"
      help="map"
      className="map"
      right={
        <span className="map-tools">
          <button onClick={() => s.flyTo(HOME.longitude, HOME.latitude, HOME.zoom)} data-tip="Back to the Danish straits, where the tracks come from">Straits</button>
          <button onClick={fitShip} disabled={!dossier} data-tip="Frame this ship's track in Danish waters">Fit ship</button>
          <button onClick={fitWorld} disabled={!dossier} data-tip="Also include its worldwide events: port calls, gaps, meetings at sea">Fit all</button>
          <button
            className={s.follow ? "on" : ""}
            aria-pressed={s.follow}
            onClick={() => s.setFollow(!s.follow)}
            data-tip="Keep the ship centred while playback runs"
          >
            Follow
          </button>
        </span>
      }
    >
      <div className="map-canvas" ref={canvas}>
        <DeckGL
          viewState={view}
          onViewStateChange={({ viewState }) => setView(viewState as MapViewState)}
          controller={{ dragRotate: false, touchRotate: false, inertia: 300, scrollZoom: { speed: 0.012, smooth: true } }}
          layers={layers.filter(Boolean)}
          getCursor={({ isHovering }) => (isHovering ? "pointer" : "crosshair")}
          getTooltip={({ object, layer }: PickingInfo) =>
            layer?.id === "events" && object
              ? {
                  html: tooltip(object as Event),
                  style: { background: "transparent", padding: "0", border: "none" },
                }
              : null
          }
        />
        <div className="map-hud tl mono">
          <div>LAT {view.latitude.toFixed(3)} LON {view.longitude.toFixed(3)}</div>
          <div>ZOOM {view.zoom.toFixed(1)}</div>
        </div>
        <div className="map-hud bl">
          <button className="legend key-toggle" aria-expanded={keyOpen} onClick={() => setKeyOpen(!keyOpen)} data-tip="Colour key. Click a type to hide it on the map and in the timeline.">
            {keyOpen ? "▾" : "▸"} Event key{hidden ? ` · ${hidden} hidden` : ""}
          </button>
          {keyOpen && (
            <div className="key-list view-in">
              {EVENT_TYPES.map((t) => (
                <button key={t} className={`legend ${s.hiddenTypes.has(t) ? "off" : ""}`} aria-pressed={!s.hiddenTypes.has(t)}
                  style={{ ["--c" as string]: `var(--ev-${t})` }} onClick={() => s.toggleType(t)} data-tip={EVENT_HELP[t]}>
                  <i />
                  {EVENT_LABEL[t]}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="map-hud br mono">
          {fix ? (
            <>
              <div>
                {fix.stale ? "last seen" : "position"} {fix.lat.toFixed(3)}N {fix.lon.toFixed(3)}E
              </div>
              <div>
                {fix.stale ? "outside Danish coverage" : `speed ${fix.sog ?? "—"} kn`} · {track.past.length} passages
              </div>
            </>
          ) : (
            <div>{dossier ? "no position yet at this date" : "no ship selected"}</div>
          )}
          {pings.slice(-3).map((p) => (
            <div key={p.event.id + p.at} className="ping-line" style={{ ["--c" as string]: `var(--ev-${p.event.type})` }}>
              ▸ {EVENT_LABEL[p.event.type]} · {p.event.summary.slice(0, 44)}
            </div>
          ))}
          <div className="dim">bright track = scored window · dim = older{s.hindsight ? " · tan = after the cutoff" : ""}</div>
        </div>
        <div className="vignette" />
      </div>
    </Panel>
  );
}

// Vessel names and destinations come from AIS, i.e. from anyone with a transponder: escape everything.
const esc = (v: unknown) =>
  String(v).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

function tooltip(e: Event) {
  const attrs = Object.entries(e.attrs)
    .slice(0, 6)
    .map(([k, v]) => `<div><span>${esc(k)}</span><b>${esc(v)}</b></div>`)
    .join("");
  return `<div class="tip" style="--c: var(--ev-${e.type})">
    <div class="tip-h">${EVENT_LABEL[e.type]} <small>${SOURCE_LABEL[e.source]} · ${esc(e.id)}</small></div>
    <div class="tip-s">${esc(e.summary)}</div>
    <div class="tip-t">${fmtDateTime(Date.parse(e.start))} → ${fmtDateTime(Date.parse(e.end))}</div>
    <div class="tip-t">observed ${fmtDateTime(Date.parse(e.observed_at))}</div>
    <div class="tip-a">${attrs}</div>
  </div>`;
}
