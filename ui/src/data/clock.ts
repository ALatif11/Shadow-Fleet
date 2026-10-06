// Playback clock. One requestAnimationFrame loop for the whole console: it advances the as-of moment at
// `speed` days per real second and stops at the bound (the cutoff, or the window end under hindsight).
// Updates are throttled to ~20 Hz; motion still looks continuous because positions are interpolated
// between fixes, and a lower update rate keeps React and deck.gl off the critical path.
import { useEffect } from "react";
import { asOfBounds, useConsole } from "../state/store";
import { DAY_MS } from "./asof";

const TICK_MS = 50;

export function useClock() {
  const playing = useConsole((s) => s.playing);
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const step = (now: number) => {
      raf = requestAnimationFrame(step);
      const dt = now - last;
      if (dt < TICK_MS) return;
      last = now;
      const st = useConsole.getState();
      const [, hi] = asOfBounds(st);
      // Out of coverage the ship is a parked dot for weeks, so run those stretches at 8x and stop
      // exactly when it is seen again.
      const skipping = st.nextFix != null && st.nextFix > st.asOf;
      const rate = st.speed * (skipping ? 8 : 1);
      let next = st.asOf + (dt / 1000) * rate * DAY_MS;
      if (skipping && next > st.nextFix!) next = st.nextFix!;
      if (next >= hi) {
        st.setAsOf(hi);
        st.setPlaying(false);
        return;
      }
      st.setAsOf(next);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing]);
}
