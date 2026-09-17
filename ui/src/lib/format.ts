import type { EventType } from "../contract";

const DAY = 86_400_000;

export const fmtDate = (ms: number) => new Date(ms).toISOString().slice(0, 10);
export const fmtDateTime = (ms: number) => new Date(ms).toISOString().slice(0, 16).replace("T", " ") + "Z";
export const fmtNum = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : Number.isInteger(v) ? String(v) : v.toFixed(digits);
export const fmtPct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(0)}%`);
export const daysBetween = (a: number, b: number) => Math.round((b - a) / DAY);
export const hours = (startIso: string, endIso: string) => (Date.parse(endIso) - Date.parse(startIso)) / 3_600_000;

export const EVENT_LABEL: Record<EventType, string> = {
  gap: "AIS GAP",
  encounter: "ENCOUNTER",
  loitering: "LOITERING",
  port_visit: "PORT VISIT",
  sts_candidate: "STS CANDIDATE",
  anchorage_loitering: "ANCHORAGE LOITER",
  draught_inconsistency: "DRAUGHT ANOMALY",
  identity_change: "IDENTITY CHANGE",
  spoof_day: "SPOOF ARTEFACT",
};

export const EVENT_TYPES = Object.keys(EVENT_LABEL) as EventType[];

export const SOURCE_LABEL: Record<string, string> = {
  dma: "DMA AIS",
  gfw: "GFW",
  self_built: "SELF-BUILT",
  sanctions: "SANCTIONS",
};

export const MODEL_LABEL: Record<string, string> = {
  lightgbm: "LightGBM",
  logreg: "LogReg",
  b2_rules: "B2 Rules",
  isoforest: "IsoForest",
};

export const LABEL_SET_LABEL: Record<string, string> = {
  ofac_eu_uk: "OFAC ∪ EU ∪ UK",
  ofac_only: "OFAC only",
};
