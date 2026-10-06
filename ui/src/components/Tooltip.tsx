import { useEffect, useRef, useState } from "react";

// One tooltip for the whole console. Any element with data-tip="..." gets it on hover and on keyboard focus,
// styled like the rest of the interface and without the browser's one-second title delay.
const OPEN_DELAY = 320; // ms before the first tip opens
const WARM = 500; // ms after one closes during which the next opens at once

interface Tip {
  text: string;
  x: number;
  y: number;
  below: boolean;
}

export function Tooltip() {
  const [tip, setTip] = useState<Tip | null>(null);
  const timer = useRef(0);
  const closedAt = useRef(0);
  const target = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const place = (el: HTMLElement) => {
      const r = el.getBoundingClientRect();
      const below = r.bottom + 90 < innerHeight;
      setTip({ text: el.dataset.tip!, x: Math.min(Math.max(r.left + r.width / 2, 170), innerWidth - 170), y: below ? r.bottom + 8 : r.top - 8, below });
      el.setAttribute("aria-describedby", "sf-tip");
    };
    const open = (e: Event) => {
      const el = (e.target as Element | null)?.closest?.<HTMLElement>("[data-tip]");
      if (el === target.current) return;
      close();
      if (!el || !el.dataset.tip) return;
      target.current = el;
      const warm = performance.now() - closedAt.current < WARM;
      timer.current = window.setTimeout(() => place(el), warm ? 0 : OPEN_DELAY);
    };
    const close = () => {
      clearTimeout(timer.current);
      if (target.current) {
        target.current.removeAttribute("aria-describedby");
        closedAt.current = performance.now();
      }
      target.current = null;
      setTip(null);
    };
    const leave = (e: Event) => {
      const to = (e as PointerEvent).relatedTarget as Element | null;
      if (target.current && !target.current.contains(to)) close();
    };
    const key = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("pointerover", open);
    document.addEventListener("pointerout", leave);
    document.addEventListener("focusin", open);
    document.addEventListener("focusout", close);
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", key);
    window.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("pointerover", open);
      document.removeEventListener("pointerout", leave);
      document.removeEventListener("focusin", open);
      document.removeEventListener("focusout", close);
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", key);
      window.removeEventListener("scroll", close, true);
    };
  }, []);

  if (!tip) return null;
  return (
    <div id="sf-tip" role="tooltip" className={`tooltip ${tip.below ? "below" : "above"}`} style={{ left: tip.x, top: tip.y }}>
      {tip.text}
    </div>
  );
}
