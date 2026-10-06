import { useEffect, useMemo, useRef } from "react";
import type { Watchlist as WL } from "../contract";
import { usePick } from "../state/store";
import { Panel } from "./Panel";
import { flagName, fmtDate, fmtNum, MODEL_LABEL } from "../lib/format";
import { GLOSSARY } from "../lib/plain";
import { useCountUp, useFlip } from "../lib/motion";

export function Watchlist({ data, previous, loading, error }: {
  data: WL | null;
  previous: WL | null;
  loading: boolean;
  error: string | null;
}) {
  const { selected, select, hindsight, b1Only, setB1Only, query, setQuery, model, cutoff, manifest, explain } = usePick("selected", "select", "hindsight", "b1Only", "setB1Only", "query", "setQuery", "model", "cutoff", "manifest", "explain");
  const listRef = useRef<HTMLDivElement>(null);

  const prevRank = useMemo(() => new Map(previous?.rows.map((r) => [r.hull_id, r.rank]) ?? []), [previous]);
  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (data?.rows ?? []).filter(
      (r) => (!b1Only || r.b1_stratum) && (!q || `${r.hull_id} ${r.name ?? ""} ${r.flag_iso3 ?? ""} ${flagName(r.flag_iso3)} ${r.imo ?? ""}`.toLowerCase().includes(q)),
    );
  }, [data, b1Only, query]);

  // Changing the month or the model re-ranks the list: rows slide to their new places.
  useFlip(listRef, [rows]);

  useEffect(() => {
    listRef.current?.querySelector(".row.sel")?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selected]);

  const hits = data?.rows.filter((r) => r.outcome.label === 1).length ?? 0;
  const supervisedMissing = !data && !loading && !error && manifest?.cutoffs.find((c) => c.cutoff === cutoff)?.supervised === false;

  return (
    <Panel
      title="Watchlist"
      code="01"
      help="watchlist"
      className={`watchlist ${hindsight ? "hs" : ""}`}
      right={<span className="mono">{data ? `TOP ${data.rows.length}` : ""}{hindsight && data ? ` · ${hits} later listed` : ""}</span>}
    >
      <div className="wl-tools">
        <input placeholder="Find a ship, flag or IMO" value={query} onChange={(e) => setQuery(e.target.value)} spellCheck={false} aria-label="Filter the watchlist" />
        <button className={`chip ${b1Only ? "on" : ""}`} aria-pressed={b1Only} onClick={() => setB1Only(!b1Only)} data-tip={GLOSSARY.b1.long}>
          Russia trade only
        </button>
      </div>
      <Scorecard data={data} explain={explain} />
      <div className="wl-head">
        <span data-tip="Rank: 1 is the ship the model would put in front of an analyst first">#</span>
        <span>Ship</span>
        <span data-tip={GLOSSARY.flag.short}>Flag</span>
        <span data-tip={`${GLOSSARY.score.short} ${GLOSSARY.score.long}`}>Score</span>
        <span data-tip={GLOSSARY.rank_delta.long}>Move</span>
        {hindsight && <span data-tip={GLOSSARY.designation.long}>Outcome</span>}
      </div>
      <div className="wl-rows" role="group" aria-label="Ranked ships" ref={listRef}>
        {loading && !data && <div className="empty wait">Loading the list…</div>}
        {error && <div className="empty err">{error}</div>}
        {supervisedMissing && (
          <div className="empty">
            {MODEL_LABEL[model] ?? model} has no list for this month
            <small>{GLOSSARY.supervised.long} Switch to B2 Rules or IsoForest, or step forward a few months.</small>
          </div>
        )}
        {!loading && !data && !error && !supervisedMissing && <div className="empty">No watchlist for this selection.</div>}
        {data && rows.length === 0 && <div className="empty">Nothing matches that filter.</div>}
        {rows.map((r, i) => {
          const pr = prevRank.get(r.hull_id);
          const delta = pr === undefined ? null : pr - r.rank;
          const moveTip =
            delta === null ? "New on the list this month" : delta === 0 ? "Same rank as last month" : `${delta > 0 ? "Up" : "Down"} ${Math.abs(delta)} since last month`;
          return (
            <button
              type="button"
              key={r.hull_id}
              data-flip={r.hull_id}
              aria-pressed={r.hull_id === selected}
              tabIndex={r.hull_id === selected ? 0 : -1}
              className={`row ${r.hull_id === selected ? "sel" : ""} ${hindsight && r.outcome.label === 1 ? "hit" : ""}`}
              onClick={() => select(r.hull_id)}
              style={{ ["--i" as string]: Math.min(i, 12) }}
            >
              <span className="mono rank">{String(r.rank).padStart(2, "0")}</span>
              <span className="hull">
                <b>{r.name ?? "UNKNOWN"}</b>
                <small className="mono">
                  {r.hull_id}
                  {r.b1_stratum && <em className="b1" data-tip={GLOSSARY.b1.short}>RU</em>}
                </small>
              </span>
              <span className="mono flag" data-tip={flagName(r.flag_iso3)}>{r.flag_iso3 ?? "—"}</span>
              <span className="score">
                <em className="mono">{r.score.toFixed(3)}</em>
                <b><i style={{ transform: `scaleX(${r.score})` }} /></b>
              </span>
              <span className={`mono delta ${delta === null ? "new" : delta > 0 ? "up" : delta < 0 ? "down" : ""}`} data-tip={moveTip}>
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
            </button>
          );
        })}
      </div>
    </Panel>
  );
}

/** A number from the bundle that glides to its new value when the month changes. */
function Rolling({ value, pct, digits = 0 }: { value: number | null | undefined; pct?: boolean; digits?: number }) {
  const v = useCountUp(value);
  if (v == null) return <>—</>;
  return <>{pct ? `${Math.round(v * 100)}%` : v.toLocaleString("en-GB", { maximumFractionDigits: digits, minimumFractionDigits: digits })}</>;
}

/**
 * How good the ranking is at this month, as sentences. Every number is read from the bundle as is
 * (rule 11); only the wording is the console's.
 */
function Scorecard({ data, explain }: { data: WL | null; explain: boolean }) {
  const all = data?.metrics.find((x) => x.stratum === "all");
  const b1 = data?.metrics.find((x) => x.stratum === "b1");
  const code = (t: keyof typeof GLOSSARY) => explain && <code className="term">{GLOSSARY[t].term}</code>;
  return (
    <div className="scorecard" data-tip={data?.origin === "synthetic" ? "Synthetic bundle: these numbers are invented to exercise the screen." : undefined}>
      <div className="sc-hero" data-tip={GLOSSARY.precision_at.long}>
        <b className="accent"><Rolling value={all?.precision_at["50"]} pct /></b>
        <span>of the top 50 were sanctioned within 6 months {code("precision_at")}</span>
      </div>
      <div className="sc-line" data-tip={GLOSSARY.precision_b1.long}>
        <b><Rolling value={b1?.precision_at["50"]} pct /></b>
        <span>among ships that trade with Russia {code("precision_b1")}</span>
      </div>
      <div className="sc-line" data-tip={GLOSSARY.recall_at.long}>
        <b><Rolling value={all?.recall_at["100"]} pct /></b>
        <span>of every ship sanctioned later made the top 100 {code("recall_at")}</span>
      </div>
      <div className="sc-foot mono">
        <span data-tip={GLOSSARY.population.long}><Rolling value={data?.population_size} /> ships eligible</span>
        <span data-tip={GLOSSARY.positives.long}><Rolling value={all?.n_positives} /> sanctioned later</span>
        <span data-tip={GLOSSARY.pr_auc.long}>ranking quality {all?.pr_auc == null ? "—" : fmtNum(all.pr_auc, 2)}</span>
      </div>
    </div>
  );
}
