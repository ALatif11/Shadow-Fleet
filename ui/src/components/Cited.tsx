import type { Dossier } from "../contract";
import { useConsole } from "../state/store";
import { EVENT_LABEL } from "../lib/format";

/**
 * Model prose with its citations made live: [E000123] becomes a chip that flies the map to that record.
 * An id that is not one of this ship's records is marked, because the model invented it.
 */
export function Cited({ text, dossier }: { text: string; dossier: Dossier }) {
  const focus = useConsole((s) => s.focus);
  const byId = new Map(dossier.events.map((e) => [e.id, e]));
  return (
    <>
      {text.split(/\[(E\d+)\]/g).map((part, i) => {
        if (i % 2 === 0) return part;
        const e = byId.get(part);
        return e ? (
          <button key={i} className="cite" style={{ ["--c" as string]: `var(--ev-${e.type})` }}
            data-tip={`${EVENT_LABEL[e.type]}: ${e.summary}. Click to show it on the map.`} onClick={() => focus(e.id, e.lon, e.lat)}>
            {part}
          </button>
        ) : (
          <span key={i} className="cite bad" data-tip="Not one of this ship's records: the model made this citation up.">{part}</span>
        );
      })}
    </>
  );
}
