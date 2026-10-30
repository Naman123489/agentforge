import { memo } from "react";
import type { RunDetail, Stage } from "../../api/types";
import { AGENT_ORDER, agentMeta, type AgentKey } from "../../lib/agents";
import { describeEvent, TIER, TIER_TONE } from "../../lib/events";
import { fmtRelative, fmtTokens, fmtUsd, shortModel } from "../../lib/format";
import { RUN_STATUS, stageDisplay, type Tone } from "../../lib/status";
import { Badge } from "../ui/Badge";
import type { AgentActivity } from "./derive";
import { latestStage } from "./derive";

const AgentCard = memo(function AgentCard({ agentKey, stage, activity, run, stagesById }: {
  agentKey: AgentKey; stage?: Stage; activity?: AgentActivity; run: RunDetail; stagesById: Map<string, Stage>;
}) {
  const meta = agentMeta(agentKey);
  const Icon = meta.icon;
  let status: { label: string; tone: Tone; active: boolean };
  if (agentKey === "orchestrator") {
    const s = RUN_STATUS[run.status];
    status = { label: s.label, tone: s.tone, active: s.live };
  } else if (stage) {
    status = stageDisplay(stage);
  } else {
    status = { label: "Idle", tone: "neutral", active: false };
  }
  const last = activity?.lastEvent;
  const lastView = last ? describeEvent(last, stagesById) : undefined;
  const tier = activity?.tier ? TIER[activity.tier] : undefined;
  const tokens = activity ? activity.inputTokens + activity.outputTokens : 0;

  return (
    <article className={`rounded-xl border bg-ink-800/60 p-3 ${status.active ? meta.ring : "border-line"}`} aria-label={meta.label}>
      <div className="flex items-start gap-2.5">
        <span className={`grid size-8 shrink-0 place-items-center rounded-lg ${meta.accent}`}><Icon className="size-4" aria-hidden /></span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-medium" title={meta.label}>{meta.label}</h3>
          <p className="truncate text-xs text-subtle" title={meta.role}>{meta.role}</p>
        </div>
        <Badge tone={status.tone} dot pulse={status.active} className="shrink-0">{status.label}</Badge>
      </div>
      <p className="mt-2.5 line-clamp-2 min-h-8 text-xs text-muted">
        {lastView ? <>{lastView.title}{lastView.detail ? ` — ${lastView.detail}` : ""}</> : <span className="text-subtle">No activity reported yet.</span>}
      </p>
      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-subtle">
        {activity?.model ? (
          <span className="flex items-center gap-1.5">
            {tier && <Badge tone={TIER_TONE[tier]}>{tier}</Badge>}
            <span className="font-mono text-muted" title={activity.model}>{shortModel(activity.model)}</span>
          </span>
        ) : <span>model —</span>}
        {activity && activity.calls > 0 && (
          <span className="num">{activity.calls} call{activity.calls > 1 ? "s" : ""} · {fmtTokens(tokens)} tok · {fmtUsd(activity.costUsd)}
            {activity.unpricedCalls > 0 && " + unpriced"} est.</span>
        )}
        {activity && activity.failures > 0 && <span className="text-rose-700">{activity.failures} failed call{activity.failures > 1 ? "s" : ""}</span>}
        {stage && stage.attempt > 1 && <span className="text-amber-700">attempt {stage.attempt}</span>}
        {last && <span className="ml-auto num">{fmtRelative(last.timestamp)}</span>}
      </div>
    </article>
  );
});

export function AgentPanel({ run, stages, activity, stagesById }: {
  run: RunDetail; stages: Stage[]; activity: Map<string, AgentActivity>; stagesById: Map<string, Stage>;
}) {
  const hasDeploy = stages.some((s) => s.agent === "deploy");
  return (
    <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-1 min-[1700px]:grid-cols-2">
      {AGENT_ORDER.filter((k) => k !== "deploy" || hasDeploy).map((k) => (
        <AgentCard key={k} agentKey={k} run={run} stagesById={stagesById}
          stage={k === "orchestrator" ? undefined : latestStage(stages, k)} activity={activity.get(k)} />
      ))}
    </div>
  );
}
