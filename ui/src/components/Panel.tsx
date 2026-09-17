import type { ReactNode } from "react";

/** HUD frame: corner brackets, title strip, optional right-hand readout. */
export function Panel({ title, code, right, className = "", children }: {
  title: string;
  code?: string;
  right?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`panel ${className}`}>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
      <header className="panel-head">
        <span className="panel-title">
          {code && <span className="panel-code">{code}</span>}
          {title}
        </span>
        {right && <span className="panel-right">{right}</span>}
      </header>
      <div className="panel-body">{children}</div>
    </section>
  );
}
