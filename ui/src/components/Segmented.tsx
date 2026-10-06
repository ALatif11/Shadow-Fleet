import { useLayoutEffect, useRef, useState } from "react";

/** Radio group whose highlight slides to the chosen option instead of blinking across. */
export function Segmented<T extends string | number>({ value, options, label, tip, onChange, className = "seg", ariaLabel }: {
  value: T;
  options: readonly T[];
  label: (v: T) => string;
  tip?: (v: T) => string | undefined;
  onChange: (v: T) => void;
  className?: string;
  ariaLabel?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState<{ x: number; w: number; live: boolean } | null>(null);
  useLayoutEffect(() => {
    const on = ref.current?.querySelector<HTMLElement>("[aria-checked='true']");
    // `live` turns the slide on only after the first placement, so the highlight never flies in from the left.
    if (on) setBox((b) => ({ x: on.offsetLeft, w: on.offsetWidth, live: b !== null }));
  }, [value, options]);
  return (
    <div className={`${className} slide`} role="radiogroup" aria-label={ariaLabel} ref={ref}>
      {box && <span className={`seg-ind ${box.live ? "live" : ""}`} style={{ transform: `translateX(${box.x}px)`, width: box.w }} aria-hidden="true" />}
      {options.map((o) => (
        <button key={o} role="radio" aria-checked={o === value} className={o === value ? "on" : ""} data-tip={tip?.(o)} onClick={() => onChange(o)}>
          {label(o)}
        </button>
      ))}
    </div>
  );
}
