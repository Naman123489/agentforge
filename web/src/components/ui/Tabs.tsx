import type { ReactNode } from "react";

export interface TabDef<K extends string> {
  key: K;
  label: string;
  icon?: ReactNode;
  badge?: ReactNode;
}

/** Accessible tablist; arrow keys move between tabs. The active key lives in the URL (?tab=). */
export function Tabs<K extends string>({ tabs, active, onChange, label }: {
  tabs: TabDef<K>[]; active: K; onChange: (k: K) => void; label: string;
}) {
  const onKey = (e: React.KeyboardEvent, i: number) => {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const next = tabs[(i + d + tabs.length) % tabs.length];
    onChange(next.key);
    document.getElementById(`tab-${next.key}`)?.focus();
  };
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b border-line px-2">
      {tabs.map((t, i) => {
        const sel = t.key === active;
        return (
          <button
            key={t.key}
            id={`tab-${t.key}`}
            role="tab"
            type="button"
            aria-selected={sel}
            aria-controls={`panel-${t.key}`}
            tabIndex={sel ? 0 : -1}
            onKeyDown={(e) => onKey(e, i)}
            onClick={() => onChange(t.key)}
            className={`relative flex shrink-0 items-center gap-1.5 px-3 py-2.5 text-sm transition ${
              sel ? "text-fg" : "text-subtle hover:text-muted"}`}
          >
            {t.icon}
            {t.label}
            {t.badge}
            {sel && <span aria-hidden className="brand-gradient absolute inset-x-2 -bottom-px h-0.5 rounded-full" />}
          </button>
        );
      })}
    </div>
  );
}
