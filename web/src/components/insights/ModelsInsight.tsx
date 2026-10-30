import { Link } from "react-router";
import { Boxes } from "lucide-react";
import type { RunDetail } from "../../api/types";
import { fmtInt, fmtTokens, fmtUsd } from "../../lib/format";
import { useModels } from "../../state/queries";
import { Badge } from "../ui/Badge";
import { Panel } from "../ui/Panel";
import { EmptyState, ErrorState, Loading } from "../ui/States";
import { modelUsage } from "./aggregate";

const SLOT: Record<string, { letter: string; tone: "brand" | "neutral" }> = {
  premium: { letter: "A", tone: "brand" }, coding: { letter: "B", tone: "neutral" }, economy: { letter: "C", tone: "neutral" },
};

export function ModelsInsight({ runs }: { runs: RunDetail[] }) {
  const models = useModels();
  const usage = modelUsage(runs);
  const latest = runs[0];
  return (
    <div className="space-y-4">
      <Panel title="Configured models · GET /models" id="models" bodyClassName="p-0">
        {models.isLoading ? <Loading label="Loading models" />
          : models.isError ? <ErrorState error={models.error} what="model status" onRetry={() => void models.refetch()} />
          : !models.data?.models.length ? <EmptyState icon={<Boxes className="size-5" />} title="No models configured" />
          : (
            <ul>
              {models.data.models.map((m) => (
                <li key={m.slot} className="grid gap-3 border-b border-line/60 px-5 py-4 last:border-0 sm:grid-cols-[9rem_1fr_auto] sm:items-center">
                  <div className="flex items-center gap-2">
                    <span className="grid size-7 place-items-center rounded-lg bg-fg text-xs font-semibold text-white">{SLOT[m.slot]?.letter ?? "?"}</span>
                    <Badge tone={SLOT[m.slot]?.tone ?? "neutral"}>{m.slot}</Badge>
                  </div>
                  <div className="min-w-0">
                    <div className="truncate font-mono text-sm text-fg">{m.model_id}</div>
                    <div className="text-xs text-subtle">{m.role}</div>
                    <div className="num mt-1 flex flex-wrap gap-x-4 text-[11px] text-subtle">
                      <span>context {m.context_window ? m.context_window.toLocaleString() : "unknown"} tok</span>
                      <span>structured output {m.supports_structured_output == null ? "unknown" : m.supports_structured_output ? "yes" : "no"}</span>
                      <span>price {m.price_status ?? "unknown"}</span>
                    </div>
                  </div>
                  <div className="flex gap-2">
                    {!m.enabled && <Badge tone="neutral">disabled</Badge>}
                    <Badge tone={m.health === "healthy" ? "ok" : m.health === "degraded" ? "warn" : m.health === "unavailable" ? "bad" : "neutral"} dot>{m.health}</Badge>
                  </div>
                </li>
              ))}
            </ul>
          )}
        <p className="border-t border-line/70 px-5 py-3 text-xs text-subtle">
          Provider: Nebius Token Factory (server configuration). API keys stay on the server and are never sent to this page.
          Fallback order: A→B→C, B→A→C, C→B.
        </p>
      </Panel>

      <Panel title={`Usage by model · last ${runs.length} runs`} id="by-model" bodyClassName="p-0"
        actions={latest && <Link to={`/runs/${encodeURIComponent(latest.id)}?tab=usage`} className="text-xs font-medium text-nv-700 hover:underline">Per-call routing reasons →</Link>}>
        {usage.length === 0 ? <p className="px-5 py-6 text-sm text-subtle">No model usage reported yet.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] text-sm">
              <thead><tr className="text-left text-xs text-subtle">
                <th className="px-5 py-2 font-medium">Model</th><th className="px-3 py-2 text-right font-medium">Calls</th>
                <th className="px-3 py-2 text-right font-medium">Input</th><th className="px-3 py-2 text-right font-medium">Output</th>
                <th className="px-5 py-2 text-right font-medium">Est. cost</th>
              </tr></thead>
              <tbody className="num">
                {usage.map((u) => (
                  <tr key={u.model_id} className="border-t border-line/60">
                    <td className="max-w-64 truncate px-5 py-2.5 font-mono text-xs" title={u.model_id}>{u.model_id}</td>
                    <td className="px-3 py-2.5 text-right">{u.calls == null ? "—" : fmtInt(u.calls)}</td>
                    <td className="px-3 py-2.5 text-right">{fmtTokens(u.input_tokens)}</td>
                    <td className="px-3 py-2.5 text-right">{fmtTokens(u.output_tokens)}</td>
                    <td className="px-5 py-2.5 text-right">{fmtUsd(u.estimated_cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="border-t border-line/70 px-5 py-3 text-xs text-subtle">Costs are the router's estimates from list prices. Confirmed totals appear under Usage only when the server reports them.</p>
      </Panel>
    </div>
  );
}
