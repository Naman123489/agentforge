import { Link } from "react-router";
import { KeyRound } from "lucide-react";
import { useConnection } from "../../api/config";
import { isTerminal } from "../../api/types";
import { useHealth, useRuns } from "../../state/queries";
import { StatusDot } from "../ui/Badge";

/** Sidebar footer: API health (GET /health), active runs (GET /runs) and whether a platform token is set. */
export function ConnectionStatus() {
  const health = useHealth();
  const runs = useRuns(20);
  const { token } = useConnection();
  const active = runs.data?.items.filter((r) => !isTerminal(r.status)) ?? [];
  const [tone, label] = health.isError ? ["bad", "API offline"] as const
    : health.data ? (health.data.status === "ok" ? ["ok", "API online"] as const : ["warn", "API degraded"] as const)
    : ["neutral", "Checking API…"] as const;

  return (
    <div className="space-y-2 rounded-2xl border border-line bg-white/70 p-3 text-xs">
      <div className="flex items-center gap-2 text-muted" title={health.data ? `API ${health.data.version}` : "GET /health"}>
        <StatusDot tone={tone} label={label} /> {label}
        {health.data && <span className="num ml-auto text-subtle">v{health.data.version}</span>}
      </div>
      {active.length > 0 && (
        <Link to={`/runs/${encodeURIComponent(active[0].id)}`} className="flex items-center gap-2 text-amber-800 hover:underline">
          <StatusDot tone="live" pulse label="running" /> {active.length} build{active.length > 1 ? "s" : ""} running
        </Link>
      )}
      <Link to="/settings" className={`flex items-center gap-2 hover:underline ${token ? "text-emerald-700" : "text-subtle"}`}>
        <KeyRound className="size-3.5" aria-hidden /> {token ? "Platform token set" : "No platform token"}
      </Link>
    </div>
  );
}
