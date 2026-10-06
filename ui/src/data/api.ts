// Read-only access to the JSON bundle (ADR-18). The UI never computes a metric; it only renders these files.
import { CONTRACT_VERSION, type Dossier, type Manifest, type Watchlist } from "../contract";

const BASE = String(import.meta.env.VITE_UI_DATA ?? "ui_data").replace(/\/$/, "");

async function getJson<T>(path: string): Promise<T | null> {
  const res = await fetch(`${BASE}/${path}`, { cache: "no-cache" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
  const type = res.headers.get("content-type") ?? "";
  if (!type.includes("json")) return null; // dev servers answer unknown paths with index.html
  return (await res.json()) as T;
}

export const major = (v: string) => v.split(".")[0];

export function assertCompatible(found: string, file: string) {
  if (major(found) !== major(CONTRACT_VERSION)) {
    throw new Error(
      `${file} uses contract ${found}, this console expects ${CONTRACT_VERSION}. Rebuild the bundle (make ui-fixtures / make ui-export) or update the console.`,
    );
  }
}

export async function loadManifest(): Promise<Manifest> {
  const m = await getJson<Manifest>("manifest.json");
  if (!m) {
    throw new Error("No bundle found at ui_data/manifest.json. Run `make ui-fixtures` (synthetic) or `make ui-export` (live).");
  }
  assertCompatible(m.contract_version, "manifest.json");
  return m;
}

export const watchlistFile = (cutoff: string, model: string, labelSet: string) => `watchlist/${cutoff}__${model}__${labelSet}.json`;

/** Same rule as contract.file_key in Python. */
export const fileKey = (hullId: string) => hullId.replace(/[^A-Za-z0-9_.-]/g, "_");

const wlCache = new Map<string, Promise<Watchlist | null>>();
const dsCache = new Map<string, Promise<Dossier | null>>();

// The bundle is a fixed set of files, so the cache is bounded by the bundle itself.
function remember<T>(cache: Map<string, Promise<T>>, key: string, load: () => Promise<T>): Promise<T> {
  let p = cache.get(key);
  if (!p) {
    p = load();
    p.catch(() => cache.delete(key));
    cache.set(key, p);
  }
  return p;
}

/** null when that model was not scored at that cutoff (e.g. supervised models before any horizon closed). */
export function loadWatchlist(cutoff: string, model: string, labelSet: string): Promise<Watchlist | null> {
  const f = watchlistFile(cutoff, model, labelSet);
  return remember(wlCache, f, async () => {
    const w = await getJson<Watchlist>(f);
    if (w) assertCompatible(w.contract_version, f);
    return w;
  });
}

export function loadDossier(hullId: string): Promise<Dossier | null> {
  const f = `vessels/${fileKey(hullId)}.json`;
  return remember(dsCache, f, async () => {
    const d = await getJson<Dossier>(f);
    if (d) assertCompatible(d.contract_version, f);
    return d;
  });
}
