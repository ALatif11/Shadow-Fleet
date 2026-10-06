import { useEffect, useState } from "react";
import type { Dossier, Watchlist } from "../contract";
import { loadDossier, loadWatchlist } from "./api";

type Loaded<T> = { data: T | null; loading: boolean; error: string | null };

function useLoad<T>(key: string | null, load: () => Promise<T | null>): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ data: null, loading: !!key, error: null });
  useEffect(() => {
    if (!key) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    let live = true;
    setState((s) => ({ data: s.data, loading: true, error: null }));
    load().then(
      (data) => live && setState({ data, loading: false, error: null }),
      (e: unknown) => live && setState({ data: null, loading: false, error: String((e as Error).message ?? e) }),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return state;
}

export function useWatchlist(cutoff: string | null, model: string, labelSet: string) {
  const key = cutoff ? `${cutoff}|${model}|${labelSet}` : null;
  return useLoad<Watchlist>(key, () => loadWatchlist(cutoff!, model, labelSet));
}

export function useDossier(hull: string | null) {
  return useLoad<Dossier>(hull, () => loadDossier(hull!));
}
