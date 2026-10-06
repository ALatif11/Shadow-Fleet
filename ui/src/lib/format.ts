import type { EventType } from "../contract";

export const fmtDate = (ms: number) => new Date(ms).toISOString().slice(0, 10);
export const fmtDateTime = (ms: number) => new Date(ms).toISOString().slice(0, 16).replace("T", " ") + "Z";
export const fmtNum = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : Number.isInteger(v) ? String(v) : v.toFixed(digits);
export const fmtPct = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(0)}%`);

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
  b1_russia_port: "B1 Russia-port",
};

export const MODEL_HELP: Record<string, string> = {
  lightgbm: "LightGBM: a trained model (gradient-boosted trees). The primary model.",
  logreg: "LogReg: a simple trained model (logistic regression). A sanity check on LightGBM.",
  b2_rules: "B2 Rules: a hand-weighted checklist written before any answers were seen. The baseline to beat.",
  isoforest: "IsoForest: flags unusual behaviour without being told which ships were sanctioned.",
  b1_russia_port: "B1 Russia-port: flag every ship that called at a Russian export port or declared one as its destination. The simplest baseline.",
};

export const LABEL_SET_LABEL: Record<string, string> = {
  ofac_eu_uk: "US + EU + UK",
  ofac_only: "US only",
};
export const LABEL_SET_HELP: Record<string, string> = {
  ofac_eu_uk: "A ship counts as a correct flag if the US (OFAC), the EU or the UK sanctioned it within 6 months.",
  ofac_only: "Stricter: only a US Treasury (OFAC) listing counts.",
};

export const fmtMonth = (iso: string) =>
  new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" }).toUpperCase();
export const fmtDay = (ms: number) =>
  new Date(ms).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

// Registries these tankers actually fly. Unknown codes fall back to the code itself.
const FLAG_NAME: Record<string, string> = {
  ATG: "Antigua and Barbuda", ARE: "UAE", BHS: "Bahamas", BLZ: "Belize", BRB: "Barbados", CMR: "Cameroon",
  CHN: "China", COK: "Cook Islands", COM: "Comoros", CYP: "Cyprus", DNK: "Denmark", GAB: "Gabon",
  GBR: "United Kingdom", GNQ: "Equatorial Guinea", GRC: "Greece", GUY: "Guyana", HKG: "Hong Kong", HND: "Honduras",
  IND: "India", IRN: "Iran", KNA: "St Kitts and Nevis", LBR: "Liberia", MDA: "Moldova", MHL: "Marshall Islands",
  MLT: "Malta", MNG: "Mongolia", NOR: "Norway", PAN: "Panama", PLW: "Palau", RUS: "Russia", SGP: "Singapore",
  SLE: "Sierra Leone", STP: "São Tomé and Príncipe", SWZ: "Eswatini", TGO: "Togo", TUR: "Turkey", TZA: "Tanzania",
  VCT: "St Vincent and the Grenadines", VUT: "Vanuatu",
};
export const flagName = (iso3: string | null | undefined) => (iso3 ? FLAG_NAME[iso3] ?? iso3 : "unknown flag");

/**
 * A feature value with its unit, read off the feature's name (the naming convention in features/). Formatting
 * only: the value itself is the bundle's.
 */
export function fmtFeature(name: string, v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  const n = (x: number, d = 0) => x.toLocaleString("en-GB", { maximumFractionDigits: d });
  if (name.startsWith("flag_to_") || name.startsWith("is_")) return v ? "yes" : "no";
  if (name.startsWith("share_")) return `${n(v * 100)}%`;
  if (name.endsWith("_km")) return `${n(v)} km`;
  if (name.includes("hours")) return `${n(v)} h`;
  if (name.startsWith("days_")) return `${n(v)} days`;
  if (name.endsWith("_years")) return `${n(v)} yrs`;
  if (name.startsWith("n_")) return `${n(v)}×`;
  return n(v, 2);
}
