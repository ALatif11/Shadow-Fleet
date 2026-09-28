// One hook per bundle file kind. Both cache by URL key: stepping cutoffs with `[` and `]` walks back over
// files already fetched, and the watchlist for the previous cutoff is requested on every render.
import { useEffect, useState } from "react";
import type { Dossier, Watchlist } from "../contract";
import { loadDossier, loadWatchlist } from "./api";

export interface Async<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

const EMPTY: Async<never> = { data: null, loading: false, error: null };

/**
 * `load` is keyed by `key`; a null key means "nothing to load".
 *
 * ponytail: unbounded Map, one bundle per session. Evict if a live bundle's dossiers get large enough to
 * matter (ADR-18 puts that at ~200 MB, where dossiers move behind an endpoint anyway).
 */
function useBundleFile<T>(cache: Map<string, T>, key: string | null, load: () => Promise<T>): Async<T> {
  const [state, setState] = useState<Async<T>>(EMPTY);

  useEffect(() => {
    if (key === null) {
      setState(EMPTY);
      return;
    }
    const hit = cache.get(key);
    if (hit) {
      setState({ data: hit, loading: false, error: null });
      return;
    }
    let live = true;
    setState({ data: null, loading: true, error: null });
    load().then(
      (data) => {
        cache.set(key, data);
        if (live) setState({ data, loading: false, error: null });
      },
      (e: Error) => {
        if (live) setState({ data: null, loading: false, error: e.message });
      },
    );
    return () => {
      live = false; // a fast scrub through cutoffs must not let an early response win
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return state;
}

const watchlists = new Map<string, Watchlist>();
const dossiers = new Map<string, Dossier>();

/** A null cutoff (no earlier cutoff exists) resolves to no data rather than a failed fetch. */
export function useWatchlist(cutoff: string | null, model: string, labelSet: string): Async<Watchlist> {
  const key = cutoff && model && labelSet ? `${cutoff}__${model}__${labelSet}` : null;
  return useBundleFile(watchlists, key, () => loadWatchlist(cutoff!, model, labelSet));
}

/** A supervised model has no watchlist before its first supervised cutoff; that 404 is expected. */
export function useDossier(hullId: string | null): Async<Dossier> {
  return useBundleFile(dossiers, hullId, () => loadDossier(hullId!));
}
