import { useEffect, useMemo, useState } from "react";
import DeckGL from "@deck.gl/react";
import { FlyToInterpolator, type MapViewState, type PickingInfo } from "@deck.gl/core";
import { GeoJsonLayer, PathLayer, PolygonLayer, ScatterplotLayer, TextLayer } from "@deck.gl/layers";
import type { FeatureCollection } from "geojson";
import type { Dossier, Event, EventType } from "../contract";
import { useConsole } from "../state/store";
import { featureWindow, isKnown, segments, trackCount, type Segment } from "../data/asof";
import { rgba } from "../lib/colors";
import { EVENT_LABEL, EVENT_TYPES, fmtDateTime, SOURCE_LABEL } from "../lib/format";
import { Panel } from "./Panel";

const HOME: MapViewState = { longitude: 11.2, latitude: 56.3, zoom: 5.4, pitch: 0, bearing: 0 };

function useLand() {
  const [land, setLand] = useState<FeatureCollection | null>(null);
  useEffect(() => {
    // Natural Earth 1:10m land (public domain), bundled via world-atlas; no tile server, works offline.
    Promise.all([import("world-atlas/land-10m.json"), import("topojson-client")]).then(([topo, tc]) => {
      const t = (topo as { default: unknown }).default as Parameters<typeof tc.feature>[0];
      const obj = (t as unknown as { objects: { land: Parameters<typeof tc.feature>[1] } }).objects.land;
      setLand(tc.feature(t, obj) as unknown as FeatureCollection);
    });
  }, []);
  return land;
}

const GRATICULE: [number, number][][] = (() => {
  const lines: [number, number][][] = [];
  for (let lon = -180; lon <= 180; lon += 2) lines.push([[lon, -80], [lon, 0], [lon, 80]]);
  for (let lat = -80; lat <= 80; lat += 1) lines.push([[-180, lat], [0, lat], [180, lat]]);
  return lines;
})();

const reducedMotion = () => typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Clock for the pulsing rings, ~30 fps; frozen when the OS asks for reduced motion. */
function usePulse(active: boolean) {
  const [t, setT] = useState(0);
  useEffect(() => {
    if (!active || reducedMotion()) return;
    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      if (now - last > 33) {
        last = now;
        setT(now / 1000);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [active]);
  return t;
}

interface Split {
  past: Segment[];
  future: Segment[];
  head: [number, number] | null;
  headSog: number | null;
}

function splitTrack(d: Dossier | null, asOf: number, hindsight: boolean): Split {
  if (!d || d.track.t.length === 0) return { past: [], future: [], head: null, headSog: null };
  const n = trackCount(d.track, asOf);
  const past = segments(d.track, 0, n);
  const future = hindsight ? segments(d.track, Math.max(0, n - 1), d.track.t.length) : [];
  const head: [number, number] | null = n > 0 ? [d.track.lon[n - 1], d.track.lat[n - 1]] : null;
  return { past, future, head, headSog: n > 0 ? d.track.sog[n - 1] : null };
}

export function MapView({ dossier }: { dossier: Dossier | null }) {
  const s = useConsole();
  const land = useLand();
  const [view, setView] = useState<MapViewState>(HOME);
  const pulse = usePulse(!!dossier);

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

  const track = useMemo(() => splitTrack(dossier, s.asOf, s.hindsight), [dossier, s.asOf, s.hindsight]);
  const [fwLo] = useMemo(() => featureWindow(s.cutoff), [s.cutoff]);

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
  const wave = (pulse * 0.8) % 1;

  const layers = [
    new PathLayer({ id: "graticule", data: GRATICULE, getPath: (d) => d, getColor: rgba("--map-graticule", 18), widthMinPixels: 1 }),
    land && new GeoJsonLayer({ id: "land", data: land, filled: true, stroked: false, getFillColor: rgba("--map-land") }),
    land && new GeoJsonLayer({ id: "land-glow", data: land, filled: false, stroked: true, getLineColor: rgba("--map-land-edge", 40), lineWidthMinPixels: 4 }),
    land && new GeoJsonLayer({ id: "land-edge", data: land, filled: false, stroked: true, getLineColor: rgba("--map-land-edge", 200), lineWidthMinPixels: 1 }),
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
      getColor: (d) => (d.t1 >= fwLo ? rgba("--map-track", 45) : rgba("--map-track-old", 20)),
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
      getFillColor: (e) => evColor(e.type, 38),
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
      getLineColor: [2, 6, 10, 255],
      lineWidthMinPixels: 1,
      onClick: (info: PickingInfo<Event>) => info.object && s.focus(info.object.id),
      updateTriggers: { getRadius: [s.focusEvent] },
    }),
    focused && focused.lon != null && new ScatterplotLayer({
      id: "focus-ring",
      data: [focused],
      getPosition: (e: Event) => [e.lon!, e.lat!],
      getRadius: 10 + 18 * wave,
      radiusUnits: "pixels",
      filled: false,
      stroked: true,
      getLineColor: evColor(focused.type, 255 * (1 - wave)),
      lineWidthMinPixels: 1.5,
      updateTriggers: { getRadius: wave, getLineColor: wave },
    }),
    track.head && new ScatterplotLayer({
      id: "head-ring",
      data: [0, 0.5],
      getPosition: () => track.head!,
      getRadius: (k: number) => 8 + 26 * ((wave + k) % 1),
      radiusUnits: "pixels",
      filled: false,
      stroked: true,
      getLineColor: (k: number) => rgba("--map-track", 220 * (1 - ((wave + k) % 1))),
      lineWidthMinPixels: 1.2,
      updateTriggers: { getRadius: wave, getLineColor: wave },
    }),
    track.head && new ScatterplotLayer({
      id: "head",
      data: [track.head],
      getPosition: (p: [number, number]) => p,
      getRadius: 5,
      radiusUnits: "pixels",
      getFillColor: [255, 255, 255, 255],
      stroked: true,
      getLineColor: rgba("--map-track"),
      lineWidthMinPixels: 2,
    }),
  ];

  const fitAll = () => {
    if (!dossier) return;
    const lons = [...dossier.events.map((e) => e.lon ?? NaN), ...dossier.track.lon].filter(Number.isFinite);
    const lats = [...dossier.events.map((e) => e.lat ?? NaN), ...dossier.track.lat].filter(Number.isFinite);
    if (!lons.length) return;
    const span = Math.max(Math.max(...lons) - Math.min(...lons), (Math.max(...lats) - Math.min(...lats)) * 1.6, 1);
    s.flyTo((Math.max(...lons) + Math.min(...lons)) / 2, (Math.max(...lats) + Math.min(...lats)) / 2, Math.max(1.5, Math.log2(360 / span) + 0.2));
  };

  return (
    <Panel
      title="TACTICAL PLOT"
      code="02"
      className="map"
      right={
        <span className="map-tools">
          <button onClick={() => s.flyTo(HOME.longitude, HOME.latitude, HOME.zoom)}>STRAITS</button>
          <button onClick={fitAll} disabled={!dossier}>FIT HULL</button>
        </span>
      }
    >
      <div className="map-canvas">
        <DeckGL
          viewState={view}
          onViewStateChange={({ viewState }) => setView(viewState as MapViewState)}
          controller={{ dragRotate: false, touchRotate: false }}
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
          {EVENT_TYPES.map((t) => (
            <button key={t} className={`legend ${s.hiddenTypes.has(t) ? "off" : ""}`} style={{ ["--c" as string]: `var(--ev-${t})` }} onClick={() => s.toggleType(t)}>
              <i />
              {EVENT_LABEL[t]}
            </button>
          ))}
        </div>
        <div className="map-hud br mono">
          {track.head ? (
            <>
              <div>
                LAST FIX {track.head[1].toFixed(3)}N {track.head[0].toFixed(3)}E
              </div>
              <div>SOG {track.headSog ?? "—"} KN · {track.past.length} TRANSIT SEG</div>
            </>
          ) : (
            <div>{dossier ? "NO DMA FIX BEFORE AS-OF" : "NO HULL SELECTED"}</div>
          )}
          <div className="dim">FEATURE WINDOW TRACK BRIGHT · OLDER DIM{s.hindsight ? " · FUTURE AMBER" : ""}</div>
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
