import { useEffect, useMemo, useRef } from "react";
import type { Watchlist as WL } from "../contract";
import { useConsole } from "../state/store";
import { Panel } from "./Panel";
import { fmtDate, fmtNum, fmtPct, MODEL_LABEL } from "../lib/format";

export function Watchlist({ data, previous, loading, error }: {
  data: WL | null;
  previous: WL | null;
  loading: boolean;
  error: string | null;
}) {
  const { selected, select, hindsight, b1Only, setB1Only, query, setQuery, model, cutoff, manifest } = useConsole();
  const listRef = useRef<HTMLDivElement>(null);

  const prevRank = useMemo(() => new Map(previous?.rows.map((r) => [r.hull_id, r.rank]) ?? []), [previous]);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data?.rows ?? []).filter(
      (r) => (!b1Only || r.b1_stratum) && (!q || `${r.hull_id} ${r.name ?? ""} ${r.flag_iso3 ?? ""} ${r.imo ?? ""}`.toLowerCase().includes(q)),
    );
  }, [data, b1Only, query]);

  useEffect(() => {
    listRef.current?.querySelector(".row.sel")?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  const hits = data?.rows.filter((r) => r.outcome.label === 1).length ?? 0;
  const supervisedMissing = !data && !loading && !error && manifest?.cutoffs.find((c) => c.cutoff === cutoff)?.supervised === false;

  return (
    <Panel
      title="WATCHLIST"
      code="01"
      className={`watchlist ${hindsight ? "hs" : ""}`}
      right={<span className="mono">{data ? `TOP ${data.rows.length}` : ""}{hindsight && data ? ` · ${hits} LISTED ≤182D` : ""}</span>}
    >
      <div className="wl-tools">
        <input placeholder="FILTER HULL / NAME / FLAG" value={query} onChange={(e) => setQuery(e.target.value)} spellCheck={false} />
        <button className={`chip ${b1Only ? "on" : ""}`} onClick={() => setB1Only(!b1Only)} title="Only hulls where the B1 Russia-port rule fired">
          B1 ONLY
        </button>
      </div>
      <Metrics data={data} />
      <div className="wl-head">
        <span>#</span>
        <span>HULL</span>
        <span>FLAG</span>
        <span>SCORE</span>
        <span title="rank change vs previous cutoff">Δ</span>
        {hindsight && <span>OUTCOME</span>}
      </div>
      <div className="wl-rows" ref={listRef}>
        {loading && <div className="empty blink">LOADING…</div>}
        {error && <div className="empty err">{error}</div>}
        {supervisedMissing && (
          <div className="empty">
            {MODEL_LABEL[model] ?? model} NOT SCORED AT {cutoff}
            <small>No earlier cutoff's 182-day horizon had closed, so expanding-window training has nothing to learn from.</small>
          </div>
        )}
        {!loading && !data && !error && !supervisedMissing && <div className="empty">NO WATCHLIST FOR THIS SELECTION</div>}
        {rows.map((r) => {
          const pr = prevRank.get(r.hull_id);
          const delta = pr === undefined ? null : pr - r.rank;
          return (
            <div
              key={r.hull_id}
              className={`row ${r.hull_id === selected ? "sel" : ""} ${hindsight && r.outcome.label === 1 ? "hit" : ""}`}
              onClick={() => select(r.hull_id)}
            >
              <span className="mono rank">{String(r.rank).padStart(2, "0")}</span>
              <span className="hull">
                <b>{r.name ?? "UNKNOWN"}</b>
                <small className="mono">{r.hull_id}{r.b1_stratum && <em className="b1">B1</em>}</small>
              </span>
              <span className="mono flag">{r.flag_iso3 ?? "—"}</span>
              <span className="score">
                <em className="mono">{r.score.toFixed(3)}</em>
                <b><i style={{ width: `${Math.round(r.score * 100)}%` }} /></b>
              </span>
              <span className={`mono delta ${delta === null ? "new" : delta > 0 ? "up" : delta < 0 ? "down" : ""}`}>
                {delta === null ? "NEW" : delta === 0 ? "·" : `${delta > 0 ? "▲" : "▼"}${Math.abs(delta)}`}
              </span>
              {hindsight && (
                <span className={`outcome mono ${r.outcome.label === 1 ? "hit" : ""}`}>
                  {r.outcome.designation_date && (
                    <>
                      <b>{r.outcome.label === 1 ? r.outcome.designation_authorities.join("+") : "LATER"}</b>
                      {r.outcome.label === 1 ? fmtDate(Date.parse(r.outcome.designation_date)) : r.outcome.designation_date.slice(0, 7)}
                    </>
                  )}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

/** Backtest numbers for this cutoff, straight from the bundle (never computed in the browser). */
function Metrics({ data }: { data: WL | null }) {
  const all = data?.metrics.find((x) => x.stratum === "all");
  const b1 = data?.metrics.find((x) => x.stratum === "b1");
  const cells: [string, string, boolean?][] = [
    ["POP", data ? data.population_size.toLocaleString() : "—"],
    ["POS", fmtNum(all?.n_positives)],
    ["P@50", fmtPct(all?.precision_at["50"]), true],
    ["P@50·B1", fmtPct(b1?.precision_at["50"]), true],
    ["R@100", fmtPct(all?.recall_at["100"])],
    ["PR-AUC", fmtNum(all?.pr_auc, 3)],
  ];
  return (
    <div className="metrics" title={data?.origin === "synthetic" ? "Synthetic fixture: these numbers are invented" : "From reports/ via make ui-export"}>
      {cells.map(([k, v, accent]) => (
        <div key={k} className={`readout ${accent ? "accent" : ""}`}>
          <span className="rk">{k}</span>
          <span className="rv mono">{v}</span>
        </div>
      ))}
    </div>
  );
}
