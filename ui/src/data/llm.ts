// The console talks to llama.cpp running on this machine (SETUP.md §6). Nothing here reaches the internet,
// and none of it produces numbers: the model only explains records the bundle already contains.
import { useCallback, useEffect, useState } from "react";
import type { Dossier, WatchlistRow } from "../contract";
import { eventsAsOf, identityAt } from "./asof";
import { EVENT_LABEL, fmtDate, fmtDateTime } from "../lib/format";

export const LLM_URL = String(import.meta.env.VITE_LLM_URL ?? "http://127.0.0.1:8080").replace(/\/$/, "");

export type LlmStatus = "checking" | "online" | "offline";

export async function probe(): Promise<LlmStatus> {
  try {
    const r = await fetch(`${LLM_URL}/v1/models`, { signal: AbortSignal.timeout(2500) });
    return r.ok ? "online" : "offline";
  } catch {
    return "offline";
  }
}

/**
 * One chat completion. With `onText`, the answer streams (server-sent events, which llama-server supports)
 * and `onText` receives the whole text so far after every chunk; the return value is the finished text.
 */
export async function ask(
  messages: { role: string; content: string }[],
  onText?: (soFar: string) => void,
  signal?: AbortSignal,
): Promise<string> {
  const r = await fetch(`${LLM_URL}/v1/chat/completions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    // thinking off: Gemma 4 and Qwen3 otherwise spend the token budget thinking and return empty content
    body: JSON.stringify({ messages, temperature: 0.2, max_tokens: 700, stream: !!onText,
      chat_template_kwargs: { enable_thinking: false } }),
    signal,
  });
  if (!r.ok) throw new Error(`llama-server ${r.status}`);
  if (!onText || !r.body || !(r.headers.get("content-type") ?? "").includes("text/event-stream")) {
    const j = await r.json();
    const text = j.choices?.[0]?.message?.content?.trim() ?? "(empty response)";
    onText?.(text);
    return text;
  }
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  let text = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    const lines = buf.split("\n");
    buf = lines.pop()!;
    for (const line of lines) {
      const data = line.startsWith("data:") ? line.slice(5).trim() : "";
      if (!data || data === "[DONE]") continue;
      const delta = JSON.parse(data).choices?.[0]?.delta?.content;
      if (delta) onText((text += delta));
    }
  }
  return text.trim() || "(empty response)";
}

/** Everything the model is allowed to know about one hull, at one moment. Phase 8's bundler replaces this. */
export function evidence(d: Dossier, row: WatchlistRow | null, asOf: number, cutoff: string, model: string): string {
  const id = identityAt(d.identity, asOf);
  const events = eventsAsOf(d.events, asOf).slice(-40);
  const lines = [
    `HULL ${d.hull_id}  IMO ${d.imo ?? "unknown"}`,
    `As of ${fmtDate(asOf)}, name ${id?.name ?? "?"}, flag ${id?.flag_iso3 ?? "?"}, MMSI ${id?.mmsi ?? "?"}`,
    `${d.header.ship_type ?? "vessel"}, ${d.header.length_m ?? "?"} m, built ${d.header.built_year ?? "?"}`,
    `Identity intervals known: ${d.identity.filter((x) => Date.parse(x.start) <= asOf).length}`,
    "",
    row
      ? `At cutoff ${cutoff} the ${model} model ranked this hull ${row.rank} of the watchlist, score ${row.score.toFixed(3)}.` +
        `\nTop contributions:\n` +
        row.drivers.map((x) => `  ${x.feature} = ${x.value ?? "n/a"} (contribution ${x.contribution.toFixed(3)})`).join("\n")
      : `This hull is not in the top list at cutoff ${cutoff}.`,
    "",
    `EVIDENCE RECORDS (${events.length}, each with an id you must cite):`,
    ...events.map(
      (e) =>
        `  [${e.id}] ${EVENT_LABEL[e.type]} (${e.source}) ${fmtDateTime(Date.parse(e.start))} ` +
        `${e.lat != null ? `at ${e.lat.toFixed(2)},${e.lon!.toFixed(2)} ` : ""}- ${e.summary}`,
    ),
  ];
  return lines.join("\n");
}

export const SYSTEM = [
  "You are a sanctions-risk analyst assistant inside a maritime console.",
  "You may only state facts that appear in the EVIDENCE RECORDS or the score summary you are given.",
  "Cite the evidence id in square brackets after every factual claim, like [E000123].",
  "If the evidence does not answer the question, say so plainly. Never guess ownership, cargo or intent.",
  "Never claim a vessel is sanctioned or guilty; the score is a ranking, not a finding.",
  "Keep answers under 150 words unless asked for more.",
].join(" ");

export const BRIEF_PROMPT =
  "Write a short analyst brief on this hull: one sentence of summary, then 3 to 5 findings as bullet points, " +
  "each citing evidence ids, then one line of caveats about what the evidence cannot show. " +
  "Plain English, no jargon, no speculation beyond the records.";

/** Shared by the brief and chat panels: server status plus one call that scopes the model to a hull. */
export function useModel() {
  const [status, setStatus] = useState<LlmStatus>("checking");
  useEffect(() => {
    probe().then(setStatus);
  }, []);

  const run = useCallback(
    async (d: Dossier, row: WatchlistRow | null, asOf: number, cutoff: string, model: string,
           messages: { role: string; content: string }[], onText?: (soFar: string) => void) => {
      try {
        const reply = await ask([{ role: "system", content: `${SYSTEM}\n\n${evidence(d, row, asOf, cutoff, model)}` }, ...messages], onText);
        setStatus("online");
        return reply;
      } catch (e) {
        setStatus("offline");
        throw e;
      }
    },
    [],
  );

  return { status, run };
}
