import { useMemo } from "react";
import { CircuitBoard } from "lucide-react";
import type { RunEvent, Stage, UsageSummary } from "../../api/types";
import { agentMeta } from "../../lib/agents";
import { explainRouting, TIER, TIER_TONE } from "../../lib/events";
import { fmtTime, fmtTokens, fmtUsd, shortModel } from "../../lib/format";
import { Badge } from "../ui/Badge";
import { Metric } from "../ui/Panel";
import { EmptyState, ErrorState, Loading } from "../ui/States";
import type { AgentActivity } from "../run/derive";

function Bar({ value, max, tone = "bg-nv-400" }: { value: number; max: number; tone?: string }) {
  const pct = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
  return (
    <div className="h-1.5 w-full rounded-full bg-ink-700" role="presentation">
      <div className={`h-full rounded-full ${tone}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function UsageSummaryView({ usage }: { usage: UsageSummary }) {
  const inPct = usage.total_tokens ? Math.round((usage.input_tokens / usage.total_tokens) * 100) : 0;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Metric label="Total tokens" value={fmtTokens(usage.total_tokens)} sub={`${fmtTokens(usage.input_tokens)} in · ${fmtTokens(usage.output_tokens)} out`} />
        <Metric label="Estimated cost" value={fmtUsd(usage.estimated_cost_usd)} sub="router estimate from configured prices" tone="live" />
        <Metric label="Confirmed cost" value={usage.confirmed_cost_usd == null ? "not confirmed" : fmtUsd(usage.confirmed_cost_usd)}
          sub={usage.confirmed_cost_usd == null ? "provider has not confirmed billing" : "confirmed by provider"} tone={usage.confirmed_cost_usd == null ? "warn" : "ok"} />
        <Metric label="Remaining budget" value={usage.remaining_budget_usd == null ? "no cost limit" : fmtUsd(usage.remaining_budget_usd)}
          sub={`premium spent ${fmtUsd(usage.premium_cost_usd)}`} tone={usage.remaining_budget_usd != null && usage.remaining_budget_usd < 0.05 ? "bad" : "fg"} />
      </div>
      <div>
        <div className="mb-1 flex justify-between text-[11px] text-subtle"><span>input {inPct}%</span><span>output {100 - inPct}%</span></div>
        <div className="flex h-2 overflow-hidden rounded-full bg-ink-700" role="img" aria-label={`Input ${inPct} percent, output ${100 - inPct} percent`}>
          <div className="bg-amber-400/80" style={{ width: `${inPct}%` }} /><div className="bg-nv-400/80" style={{ width: `${100 - inPct}%` }} />
        </div>
      </div>
      {usage.free_allowance_used != null && <p className="text-xs text-subtle">Free allowance used: <span className="num text-muted">{fmtTokens(usage.free_allowance_used)}</span> tokens</p>}
    </div>
  );
}

export function UsagePanel({ usage, usageLoading, usageError, events, activity, stagesById }: {
  usage?: UsageSummary | null; usageLoading: boolean; usageError: unknown;
  events: RunEvent[]; activity: Map<string, AgentActivity>; stagesById: Map<string, Stage>;
}) {
  const decisions = useMemo(() => events.filter((e) => e.type === "route_decision").reverse(), [events]);
  const failures = useMemo(() => events.filter((e) => e.type === "model_call_failed"), [events]);
  const budgetEvents = useMemo(() => events.filter((e) => e.type === "budget_warning" || e.type === "budget_exhausted"), [events]);
  const byModelMax = Math.max(0, ...(usage?.by_model ?? []).map((m) => m.input_tokens + m.output_tokens));
  const agents = [...activity.entries()].filter(([, a]) => a.calls > 0);
  const agentMax = Math.max(0, ...agents.map(([, a]) => a.inputTokens + a.outputTokens));

  return (
    <div className="space-y-5 p-4">
      <section aria-label="Usage summary">
        {usageLoading ? <Loading label="Loading usage" />
          : usageError ? <ErrorState compact error={usageError} what="usage" />
          : usage ? <UsageSummaryView usage={usage} />
          : <EmptyState icon={<CircuitBoard className="size-5" />} title="No usage reported yet">Token and cost figures appear once the server reports them (GET /runs/{"{id}"}/usage).</EmptyState>}
        {budgetEvents.length > 0 && (
          <ul className="mt-3 space-y-1.5">
            {budgetEvents.map((e) => (
              <li key={e.id} className={`rounded-lg border px-3 py-2 text-xs ${e.type === "budget_exhausted" ? "border-rose-400/30 bg-rose-500/5 text-rose-800" : "border-amber-400/30 bg-amber-400/5 text-amber-900"}`}>
                <strong>{e.type === "budget_exhausted" ? "Hard limit reached — run stopped by budget" : "Approaching the budget: router switched to low-budget mode"}</strong>
                <span className="ml-2 num text-subtle">{fmtTime(e.timestamp)}</span>
                {typeof (e.data as Record<string, unknown>).reason === "string" && <span className="ml-2">({String((e.data as Record<string, unknown>).reason).replace(/_/g, " ")})</span>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="grid gap-5 lg:grid-cols-2">
        <section aria-label="Usage per model">
          <h3 className="panel-title mb-2">Per model</h3>
          {usage?.by_model.length ? (
            <ul className="space-y-2.5">
              {usage.by_model.map((m) => (
                <li key={m.model_id} className="text-xs">
                  <div className="mb-1 flex justify-between gap-2"><span className="truncate font-mono text-muted" title={m.model_id}>{shortModel(m.model_id)}</span>
                    <span className="num text-subtle">{fmtTokens(m.input_tokens + m.output_tokens)} tok · {fmtUsd(m.estimated_cost_usd)} · {m.calls ?? "?"} calls</span></div>
                  <Bar value={m.input_tokens + m.output_tokens} max={byModelMax} tone="bg-amber-400/80" />
                </li>
              ))}
            </ul>
          ) : <p className="text-xs text-subtle">No per-model usage reported.</p>}
        </section>
        <section aria-label="Usage per agent">
          <h3 className="panel-title mb-2">Per agent <span className="normal-case tracking-normal text-subtle">(summed from model-call events)</span></h3>
          {agents.length ? (
            <ul className="space-y-2.5">
              {agents.map(([k, a]) => (
                <li key={k} className="text-xs">
                  <div className="mb-1 flex justify-between gap-2"><span className="text-muted">{agentMeta(k).label}</span>
                    <span className="num text-subtle">{fmtTokens(a.inputTokens + a.outputTokens)} tok · {fmtUsd(a.costUsd)}{a.unpricedCalls ? " + unpriced" : ""}{a.failures ? ` · ${a.failures} failed` : ""}</span></div>
                  <Bar value={a.inputTokens + a.outputTokens} max={agentMax} />
                </li>
              ))}
            </ul>
          ) : <p className="text-xs text-subtle">No model calls observed in this session's event stream.</p>}
        </section>
      </div>

      <section aria-label="Routing decisions">
        <h3 className="panel-title mb-2">Routing decisions <span className="normal-case tracking-normal text-subtle">({decisions.length}{failures.length ? `, ${failures.length} failed calls` : ""})</span></h3>
        {decisions.length === 0 ? <p className="text-xs text-subtle">No routing decisions received yet.</p> : (
          <div className="overflow-x-auto rounded-xl border border-line">
            <table className="w-full min-w-[42rem] text-left text-xs">
              <thead className="bg-ink-800 text-subtle"><tr>
                <th className="px-3 py-2 font-medium">Time</th><th className="px-3 py-2 font-medium">Agent · task</th>
                <th className="px-3 py-2 font-medium">Allocation</th><th className="px-3 py-2 font-medium">Model</th>
                <th className="px-3 py-2 font-medium">Why</th><th className="px-3 py-2 text-right font-medium">Est. cost</th>
              </tr></thead>
              <tbody>
                {decisions.map((e) => {
                  const d = e.data as Record<string, unknown>;
                  const task = (d.task ?? {}) as Record<string, unknown>;
                  const tier = typeof d.slot === "string" ? TIER[d.slot] : undefined;
                  const agent = (task.agent as string | undefined) ?? (e.stage_id ? stagesById.get(e.stage_id)?.agent : undefined);
                  return (
                    <tr key={e.id} className="border-t border-line/60 align-top">
                      <td className="num px-3 py-2 font-mono text-subtle">{fmtTime(e.timestamp)}</td>
                      <td className="px-3 py-2 text-muted">{agent ? agentMeta(agent).label : "—"}{typeof task.task_type === "string" && <span className="block text-subtle">{task.task_type.replace(/_/g, " ")}{typeof task.failed_attempts === "number" && task.failed_attempts > 0 ? ` · ${task.failed_attempts} failed before` : ""}</span>}</td>
                      <td className="px-3 py-2">{d.action === "call" && tier ? <Badge tone={TIER_TONE[tier]}>{tier}</Badge> : <Badge tone={d.action === "stop" ? "bad" : "warn"}>{String(d.action ?? "—")}</Badge>}</td>
                      <td className="px-3 py-2 font-mono text-muted" title={String(d.model_id ?? "")}>{shortModel(d.model_id as string | undefined)}</td>
                      <td className="px-3 py-2 text-fg">{explainRouting(d)}</td>
                      <td className="num px-3 py-2 text-right text-muted">{fmtUsd(typeof d.estimated_cost_usd === "number" ? d.estimated_cost_usd : null)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
