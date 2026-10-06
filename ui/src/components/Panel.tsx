import { createContext, useContext, type ReactNode } from "react";
import { DOCK, useConsole, usePick, type DockId } from "../state/store";
import { PANEL_HELP } from "../lib/plain";

/** Tells a panel it is living in the dock, so its header grows the pin and hide controls. */
const DockCtx = createContext<{ id: DockId; pinned?: boolean; canPin?: boolean } | null>(null);

export function DockSlot({ id, pinned, canPin, children }: { id: DockId; pinned?: boolean; canPin?: boolean; children: ReactNode }) {
  return (
    <DockCtx.Provider value={{ id, pinned, canPin }}>
      <div className={`dock ${pinned ? "pinned" : ""}`} key={id}>{children}</div>
    </DockCtx.Provider>
  );
}

function DockControls() {
  const slot = useContext(DockCtx);
  const { pin, setLayout } = usePick("pin", "setLayout");
  if (!slot) return null;
  const key = DOCK.find((d) => d.id === slot.id)?.key;
  return (
    <span className="dock-tools">
      {slot.pinned ? (
        <button onClick={() => setLayout({ pinned: null })} data-tip="Unpin: close this pinned panel">Unpin</button>
      ) : (
        slot.canPin && <button onClick={pin} data-tip="Pin: keep this panel open and open another one beside it">Pin</button>
      )}
      <button
        aria-label="Hide panel"
        data-tip={`Hide this panel (${key}). The map takes the space.`}
        onClick={() => setLayout(slot.pinned ? { pinned: null } : { dock: null })}
      >
        ×
      </button>
    </span>
  );
}

/** Framed unit. `help` names an entry in PANEL_HELP; it shows when explain mode is on. */
export function Panel({ title, code, help, right, className = "", children }: {
  title: string;
  code?: string;
  help?: keyof typeof PANEL_HELP | string;
  right?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  const explain = useConsole((s) => s.explain);
  const text = help ? PANEL_HELP[help] : undefined;
  return (
    <section className={`panel view-in ${className}`}>
      <header className="panel-head">
        <span className="panel-title">
          {code && <span className="panel-code">{code}</span>}
          {title}
        </span>
        <span className="panel-right">
          {right}
          <DockControls />
        </span>
      </header>
      {explain && text && <p className="panel-help view-in">{text}</p>}
      <div className="panel-body">{children}</div>
    </section>
  );
}
