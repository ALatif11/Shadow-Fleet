// Motion that CSS cannot do on its own: count-ups, list reordering, the name decode. Every duration is read
// from theme.css, and every helper here collapses to "jump to the end" under prefers-reduced-motion,
// because the CSS reduced-motion guard does not reach JavaScript-driven animation.
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";

export function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = matchMedia(query);
      m.addEventListener("change", cb);
      return () => m.removeEventListener("change", cb);
    },
    () => matchMedia(query).matches,
    () => false,
  );
}

const reduced = () => typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** A duration token from theme.css, in ms; 0 when the viewer asked for reduced motion. */
export function dur(name: string): number {
  if (reduced() || typeof document === "undefined") return 0;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v.endsWith("ms") ? parseFloat(v) : parseFloat(v) * 1000 || 0;
}
export function ease(name = "--ease-emph"): string {
  return typeof document === "undefined" ? "ease-out" : getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "ease-out";
}

const outCubic = (p: number) => 1 - (1 - p) ** 3;

/**
 * Show `value`, but glide to it from the previous value. Display only: the number that lands is exactly the
 * bundle's; the frames in between exist for a few hundred milliseconds and are never read by anything.
 */
export function useCountUp(value: number | null | undefined): number | null {
  const [shown, setShown] = useState<number | null>(value ?? null);
  const from = useRef<number | null>(value ?? null);
  useEffect(() => {
    if (value == null) {
      from.current = null;
      setShown(null);
      return;
    }
    const start = from.current;
    const ms = dur("--t-count");
    if (start == null || ms === 0 || start === value) {
      from.current = value;
      setShown(value);
      return;
    }
    let raf = 0;
    const t0 = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / ms);
      const v = start + (value - start) * outCubic(p);
      from.current = v;
      setShown(p < 1 ? v : value);
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return shown;
}

/**
 * FLIP for a list: children carrying data-flip="<key>" slide from where they were to where they are now.
 * Rows that were not there before fade in. `deps` should change exactly when the list's order can change.
 */
export function useFlip(container: React.RefObject<HTMLElement | null>, deps: unknown[]) {
  const last = useRef(new Map<string, number>());
  useLayoutEffect(() => {
    const el = container.current;
    if (!el) return;
    const ms = dur("--t-stage");
    const rows = [...el.querySelectorAll<HTMLElement>("[data-flip]")];
    const next = new Map<string, number>();
    const scroll = el.scrollTop;
    rows.forEach((r, i) => {
      const k = r.dataset.flip!;
      const y = r.offsetTop;
      next.set(k, y);
      if (ms === 0) return;
      const before = last.current.get(k);
      // Only animate what the viewer can see; off-screen rows just land.
      if (y + r.offsetHeight < scroll || y > scroll + el.clientHeight) return;
      if (before === undefined) {
        r.animate([{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }],
          { duration: ms, easing: ease(), delay: Math.min(i, 12) * dur("--t-stagger"), fill: "backwards" });
      } else if (before !== y) {
        r.animate([{ transform: `translateY(${before - y}px)` }, { transform: "none" }], { duration: ms, easing: ease() });
      }
    });
    last.current = next;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

const GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#/";

/** The ship's name resolves out of noise when the selection changes: one short beat, then plain text. */
export function useDecode(text: string): string {
  const [out, setOut] = useState(text);
  useEffect(() => {
    const ms = dur("--t-decode");
    if (ms === 0) return setOut(text);
    let raf = 0;
    const t0 = performance.now();
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / ms);
      const fixed = Math.floor(text.length * outCubic(p));
      setOut(
        text.slice(0, fixed) +
          [...text.slice(fixed)].map((c) => (c === " " ? " " : GLYPHS[(Math.random() * GLYPHS.length) | 0])).join(""),
      );
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [text]);
  return out;
}
