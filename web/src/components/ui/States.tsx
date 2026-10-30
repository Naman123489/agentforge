import type { ReactNode } from "react";
import { AlertTriangle, Loader2, PlugZap, RefreshCw } from "lucide-react";
import { ApiError } from "../../api/client";
import { Button } from "./Button";

export function EmptyState({ icon, title, children, action }: {
  icon?: ReactNode; title: string; children?: ReactNode; action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      {icon && <div className="mb-1 rounded-xl border border-line bg-ink-800 p-3 text-subtle">{icon}</div>}
      <p className="text-sm font-medium text-fg">{title}</p>
      {children && <div className="max-w-md text-sm text-subtle">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 py-10 text-sm text-subtle">
      <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden /> {label}…
    </div>
  );
}

/** Actionable error: says what failed and why, and offers a retry only when retrying can help. */
export function ErrorState({ error, what, onRetry, compact }: {
  error: unknown; what: string; onRetry?: () => void; compact?: boolean;
}) {
  const e = error instanceof ApiError ? error : null;
  const offline = e?.kind === "network";
  const body = (
    <>
      <p className="text-sm font-medium text-fg">{offline ? "AgentForge API not reachable" : `Could not load ${what}`}</p>
      <p className="mt-0.5 text-sm text-subtle">{e?.message ?? (error instanceof Error ? error.message : String(error))}</p>
      {e?.issues && e.issues.length > 0 && (
        <ul className="mt-1 list-inside list-disc text-xs text-subtle">
          {e.issues.map((i, n) => <li key={n}><code>{i.path.join(".")}</code>: {i.message}</li>)}
        </ul>
      )}
      {onRetry && (!e || e.retryable) && (
        <Button size="sm" className="mt-2" icon={<RefreshCw className="size-3.5" />} onClick={onRetry}>Try again</Button>
      )}
    </>
  );
  if (compact) return <div role="alert" className="rounded-lg border border-rose-400/30 bg-rose-500/5 px-3 py-2">{body}</div>;
  return (
    <div role="alert" className="flex items-start gap-3 px-5 py-8">
      <div className={`rounded-lg p-2 ${offline ? "bg-amber-400/10 text-amber-700" : "bg-rose-400/10 text-rose-700"}`}>
        {offline ? <PlugZap className="size-5" /> : <AlertTriangle className="size-5" />}
      </div>
      <div className="min-w-0">{body}</div>
    </div>
  );
}
