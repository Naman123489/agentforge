import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { useId, useState } from "react";

export function Button({ variant = "primary", loading, children, className = "", ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger"; loading?: boolean }) {
  return (
    <button type="button" {...rest} className={`btn btn-${variant} ${className}`} disabled={rest.disabled || loading} aria-busy={loading || undefined}>
      {loading && <span className="spinner" aria-hidden />}
      {children}
    </button>
  );
}

export function Field({ label, error, hint, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: string; error?: string; hint?: string }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} aria-invalid={!!error || undefined} aria-describedby={error || hint ? `${id}-msg` : undefined} {...rest} />
      {(error || hint) && <small id={`${id}-msg`} className={error ? "field-error" : "field-hint"}>{error ?? hint}</small>}
    </div>
  );
}

function FieldMessage({ id, error, hint }: { id: string; error?: string; hint?: string }) {
  return error || hint ? <small id={`${id}-msg`} className={error ? "field-error" : "field-hint"}>{error ?? hint}</small> : null;
}

/** Multi-line text input with the same label / error / hint handling as Field. */
export function TextArea({ label, error, hint, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string; error?: string; hint?: string }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <textarea id={id} rows={4} aria-invalid={!!error || undefined} aria-describedby={error || hint ? `${id}-msg` : undefined} {...rest} />
      <FieldMessage id={id} error={error} hint={hint} />
    </div>
  );
}

/** Dropdown: options as [value, label] pairs. */
export function Select({ label, error, hint, options, ...rest }: SelectHTMLAttributes<HTMLSelectElement> & { label: string; error?: string; hint?: string; options: [string, string][] }) {
  const id = useId();
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select id={id} aria-invalid={!!error || undefined} aria-describedby={error || hint ? `${id}-msg` : undefined} {...rest}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
      <FieldMessage id={id} error={error} hint={hint} />
    </div>
  );
}

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || actions) && <header className="card-header">{title && <h2>{title}</h2>}{actions}</header>}
      <div className="card-body">{children}</div>
    </section>
  );
}

export function Alert({ tone = "error", children }: { tone?: "error" | "success" | "info"; children: ReactNode }) {
  return <div role={tone === "error" ? "alert" : "status"} className={`alert alert-${tone}`}>{children}</div>;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <div role="status" className="loading"><span className="spinner" aria-hidden /> {label}…</div>;
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return <div className="empty"><p className="empty-title">{title}</p>{children && <p>{children}</p>}{action}</div>;
}

// ---- layout (design system v2): build every screen from these, not from bare <div>s ----

export type NavItem = { href: string; label: string };

/** Signed-in app frame: sticky header with brand, nav (collapses to a menu on phones) and an optional right side. */
export function AppShell({ brand, nav = [], current, right, children }: {
  brand: string; nav?: NavItem[]; current?: string; right?: ReactNode; children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const isCurrent = (href: string) => (current ?? "") === href.replace(/^#\/?/, "");
  return (
    <>
      <header className="shell-header">
        <div className="container shell-bar">
          <a className="brand" href="#/"><span className="brand-mark" aria-hidden>{brand.slice(0, 1).toUpperCase()}</span>{brand}</a>
          <nav className="nav" aria-label="Main">
            {nav.map((n) => <a key={n.href} href={n.href} aria-current={isCurrent(n.href) ? "page" : undefined}>{n.label}</a>)}
          </nav>
          <div className="spacer" />
          {right && <div className="row shell-right">{right}</div>}
          {(nav.length > 0 || right) && (
            <button type="button" className="btn btn-ghost icon-btn menu-btn" aria-expanded={open} aria-label="Menu" onClick={() => setOpen((o) => !o)}>☰</button>
          )}
        </div>
        {open && (
          <nav className="container mobile-nav" aria-label="Main">
            {nav.map((n) => <a key={n.href} href={n.href} onClick={() => setOpen(false)} aria-current={isCurrent(n.href) ? "page" : undefined}>{n.label}</a>)}
            {right && <div className="shell-right-mobile">{right}</div>}
          </nav>
        )}
      </header>
      <main className="shell-main"><div className="container fade-in">{children}</div></main>
    </>
  );
}

/** One screen inside the shell: a title, an optional one-line description and primary actions. */
export function Page({ title, subtitle, actions, children }: { title: string; subtitle?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <>
      <div className="page-header">
        <div className="titles"><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
        {actions && <div className="row">{actions}</div>}
      </div>
      <div className="stack">{children}</div>
    </>
  );
}

/** Centered, branded sign-in / sign-up screen. */
export function AuthLayout({ brand, title, subtitle, footer, children }: { brand: string; title: string; subtitle?: string; footer?: ReactNode; children: ReactNode }) {
  return (
    <div className="auth">
      <div className="auth-card fade-in">
        <div className="auth-head">
          <span className="brand-mark" aria-hidden>{brand.slice(0, 1).toUpperCase()}</span>
          <h1>{title}</h1>
          {subtitle && <p className="muted">{subtitle}</p>}
        </div>
        <section className="card"><div className="card-body">{children}</div></section>
        {footer && <p className="auth-foot">{footer}</p>}
      </div>
    </div>
  );
}

/** Responsive grid: as many columns as fit at `min` px each, one column on phones. */
export function Grid({ min = 260, children }: { min?: number; children: ReactNode }) {
  return <div className="grid" style={{ ["--min" as string]: `${min}px` }}>{children}</div>;
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return <div className="card stat"><div className="stat-label">{label}</div><div className="stat-value">{value}</div>{hint && <div className="stat-hint">{hint}</div>}</div>;
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "primary" | "success" | "danger" | "warning"; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

/** Data table that scrolls sideways on small screens instead of breaking the layout. */
export function DataTable<T>({ columns, rows, rowKey, empty }: {
  columns: { header: string; cell: (row: T) => ReactNode }[]; rows: T[]; rowKey: (row: T) => string | number; empty?: ReactNode;
}) {
  if (rows.length === 0) return <>{empty ?? <EmptyState title="Nothing here yet" />}</>;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr>{columns.map((c) => <th key={c.header} scope="col">{c.header}</th>)}</tr></thead>
        <tbody>{rows.map((r) => <tr key={rowKey(r)}>{columns.map((c) => <td key={c.header}>{c.cell(r)}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return <div role="status" aria-label="Loading" className="stack">{Array.from({ length: lines }, (_, i) => <div key={i} className="skeleton" style={{ width: `${90 - i * 12}%` }} />)}</div>;
}

export function Toolbar({ children }: { children: ReactNode }) {
  return <div className="toolbar">{children}</div>;
}

export function Checkbox({ label, ...rest }: InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return <label className="check"><input type="checkbox" {...rest} /> {label}</label>;
}
