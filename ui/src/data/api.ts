// Fetching the static bundle. There is no API server (ADR-18): these are file reads over HTTP, and the
// file names must match what Python writes in shadowfleet/ui_export/contract.py. `contract.test.ts`
// checks that they still do.
import type { Dossier, Manifest, Watchlist } from "../contract";
import { CONTRACT_VERSION } from "../contract/types.gen";

/** Vite serves public/ui_data at <base>ui_data/. `make ui-fixtures` / `make ui-export` write it. */
const ROOT = `${import.meta.env.BASE_URL}ui_data`;

/** Mirrors contract.file_key: ':' is not a legal filename character on Windows. */
export function fileKey(hullId: string): string {
  return hullId.replace(/[^A-Za-z0-9_.-]/g, "_");
}

/** Mirrors contract.watchlist_filename. */
export function watchlistFile(cutoff: string, model: string, labelSet: string): string {
  return `watchlist/${cutoff}__${model}__${labelSet}.json`;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${ROOT}/${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(res.status === 404
      ? `${path} is not in the bundle. Run \`make ui-fixtures\` (synthetic) or \`make ui-export\` (live).`
      : `${path}: HTTP ${res.status}`);
  }
  return (await res.json()) as T;
}

export async function loadManifest(): Promise<Manifest> {
  const m = await getJson<Manifest>("manifest.json");
  // Fail loudly rather than render a stale shape: the browser's types are generated from the schema, so a
  // bundle written by a different contract version can disagree field by field.
  if (m.contract_version !== CONTRACT_VERSION) {
    throw new Error(`bundle is contract ${m.contract_version}, console is ${CONTRACT_VERSION}. ` +
      "Run `make ui-schema` and rebuild, or regenerate the bundle.");
  }
  return m;
}

export const loadWatchlist = (cutoff: string, model: string, labelSet: string): Promise<Watchlist> =>
  getJson<Watchlist>(watchlistFile(cutoff, model, labelSet));

export const loadDossier = (hullId: string): Promise<Dossier> =>
  getJson<Dossier>(`vessels/${fileKey(hullId)}.json`);
