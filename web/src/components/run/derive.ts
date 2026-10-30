// Read-only views over server data for the observer. No state machine here: we only group what the API reports.
import type { AgentRole, RunEvent, Stage } from "../../api/types";
import { eventAgent } from "../../lib/events";

export const latestStage = (stages: Stage[], agent: AgentRole): Stage | undefined => {
  const list = stages.filter((s) => s.agent === agent);
  return list.sort((a, b) => (a.started_at ?? "").localeCompare(b.started_at ?? "") || a.attempt - b.attempt).at(-1);
};

export interface AgentActivity {
  calls: number;
  failures: number;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  unpricedCalls: number;
  model?: string;
  tier?: string;
  lastEvent?: RunEvent;
}

/** Per-agent model usage aggregated from model_call_* events (the contract's UsageSummary has no per-agent split). */
export function activityByAgent(events: RunEvent[], stagesById: Map<string, Stage>): Map<string, AgentActivity> {
  const out = new Map<string, AgentActivity>();
  const routeAgent = new Map<string, string>(); // stage_id -> agent from route_decision.task.agent
  for (const ev of events) {
    const agent = eventAgent(ev, stagesById) ?? (ev.stage_id ? routeAgent.get(ev.stage_id) : undefined) ?? "orchestrator";
    if (ev.type === "route_decision" && ev.stage_id) routeAgent.set(ev.stage_id, agent);
    const a = out.get(agent) ?? { calls: 0, failures: 0, inputTokens: 0, outputTokens: 0, costUsd: 0, unpricedCalls: 0 };
    const d = ev.data as Record<string, unknown>;
    if (ev.type === "model_call_ok") {
      a.calls += 1;
      a.inputTokens += Number(d.input_tokens) || 0;
      a.outputTokens += Number(d.output_tokens) || 0;
      if (typeof d.cost_usd === "number") a.costUsd += d.cost_usd;
      else a.unpricedCalls += 1;
      a.model = typeof d.model_id === "string" ? d.model_id : a.model;
      a.tier = typeof d.slot === "string" ? d.slot : a.tier;
    } else if (ev.type === "model_call_failed") {
      a.failures += 1;
    } else if (ev.type === "route_decision" && typeof d.model_id === "string") {
      a.model = d.model_id;
      a.tier = typeof d.slot === "string" ? d.slot : a.tier;
    }
    a.lastEvent = ev;
    out.set(agent, a);
  }
  return out;
}

export function lastEventFor(events: RunEvent[], pred: (e: RunEvent) => boolean): RunEvent | undefined {
  for (let i = events.length - 1; i >= 0; i--) if (pred(events[i])) return events[i];
  return undefined;
}
