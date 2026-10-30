import type { ReactNode } from "react";

export function Panel({ title, actions, children, className = "", bodyClassName = "", id }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; bodyClassName?: string; id?: string;
}) {
  return (
    <section className={`panel min-w-0 ${className}`} aria-labelledby={title && id ? `${id}-title` : undefined}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-line/70 px-4 py-2.5">
          {title && <h2 id={id ? `${id}-title` : undefined} className="panel-title">{title}</h2>}
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={bodyClassName || "p-4"}>{children}</div>
    </section>
  );
}

export function Metric({ label, value, sub, tone = "fg" }: {
  label: string; value: ReactNode; sub?: ReactNode; tone?: "fg" | "ok" | "warn" | "bad" | "live" | "brand";
}) {
  const color = { fg: "text-fg", ok: "text-emerald-700", warn: "text-amber-700", bad: "text-rose-700",
    live: "text-amber-700", brand: "text-nv-700" }[tone];
  return (
    <div className="min-w-0">
      <div className="text-[11px] font-medium uppercase tracking-wider text-subtle">{label}</div>
      <div className={`num mt-0.5 truncate text-lg font-semibold ${color}`}>{value}</div>
      {sub && <div className="truncate text-xs text-subtle">{sub}</div>}
    </div>
  );
}
