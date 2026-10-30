// Platform-wide numbers derived from GET /runs (each RunDetail carries its stages and usage).
// Nothing is estimated here beyond what the server reports; missing data stays null so the UI can say "unavailable".
import type { AgentRole, RunDetail, Stage } from "../../api/types";
import { isTerminal } from "../../api/types";

export interface AgentStats { completed: number; failed: number; running: number; attempts: number; lastModels: string[]; lastAt: string | null }

export function agentStats(runs: RunDetail[]): Map<AgentRole, AgentStats> {
  const out = new Map<AgentRole, AgentStats>();
  for (const run of runs) for (const s of run.stages) {
    const a = out.get(s.agent) ?? { completed: 0, failed: 0, running: 0, attempts: 0, lastModels: [], lastAt: null };
    a.attempts += 1;
    if (s.status === "passed") a.completed += 1;
    else if (s.status === "failed") a.failed += 1;
    else if (s.status === "running") a.running += 1;
    const at = s.finished_at ?? s.started_at ?? null;
    if (at && (!a.lastAt || at > a.lastAt)) {
      a.lastAt = at;
      if (s.result?.models_used?.length) a.lastModels = s.result.models_used;
    }
    out.set(s.agent, a);
  }
  return out;
}

export interface ModelUsage { model_id: string; input_tokens: number; output_tokens: number; estimated_cost_usd: number; calls: number | null }

export function modelUsage(runs: RunDetail[]): ModelUsage[] {
  const m = new Map<string, ModelUsage>();
  for (const run of runs) for (const b of run.usage.by_model ?? []) {
    const u = m.get(b.model_id) ?? { model_id: b.model_id, input_tokens: 0, output_tokens: 0, estimated_cost_usd: 0, calls: 0 };
    u.input_tokens += b.input_tokens;
    u.output_tokens += b.output_tokens;
    u.estimated_cost_usd += b.estimated_cost_usd;
    u.calls = u.calls == null || b.calls == null ? null : u.calls + b.calls;
    m.set(b.model_id, u);
  }
  return [...m.values()].sort((a, b) => b.input_tokens + b.output_tokens - (a.input_tokens + a.output_tokens));
}

export interface PlatformTotals {
  runs: number; done: number; failed: number; stopped: number; active: number;
  tokens: number; estimatedCost: number; confirmedCost: number | null; modelCalls: number | null;
  avgDurationMs: number | null; withCheckpoint: number;
  approvals: number; rejections: number; rolledBack: number;
}

export function platformTotals(runs: RunDetail[]): PlatformTotals {
  let tokens = 0, est = 0, confirmed: number | null = 0, calls: number | null = 0, durSum = 0, durN = 0;
  let approvals = 0, rejections = 0, rolledBack = 0;
  for (const r of runs) {
    tokens += r.usage.total_tokens;
    est += r.usage.estimated_cost_usd;
    confirmed = confirmed == null || r.usage.confirmed_cost_usd == null ? null : confirmed + r.usage.confirmed_cost_usd;
    for (const b of r.usage.by_model ?? []) calls = calls == null || b.calls == null ? null : calls + b.calls;
    if (isTerminal(r.status) && r.updated_at) {
      const d = Date.parse(r.updated_at) - Date.parse(r.created_at);
      if (Number.isFinite(d) && d >= 0) { durSum += d; durN += 1; }
    }
    for (const s of r.stages) {
      if (s.review?.verdict === "approve") approvals += 1;
      else if (s.review?.verdict === "reject") rejections += 1;
      if (s.status === "rolled_back") rolledBack += 1;
    }
  }
  return {
    runs: runs.length,
    done: runs.filter((r) => r.status === "done").length,
    failed: runs.filter((r) => r.status === "failed").length,
    stopped: runs.filter((r) => r.status === "stopped" || r.status === "cancelled").length,
    active: runs.filter((r) => !isTerminal(r.status)).length,
    tokens, estimatedCost: est, confirmedCost: runs.length ? confirmed : null,
    modelCalls: runs.some((r) => (r.usage.by_model ?? []).length) ? calls : null,
    avgDurationMs: durN ? durSum / durN : null,
    withCheckpoint: runs.filter((r) => !!r.latest_checkpoint_id).length,
    approvals, rejections, rolledBack,
  };
}

export interface StageRow { run: RunDetail; stage: Stage }

/** Most recent stages across runs, newest first. */
export function stageHistory(runs: RunDetail[], limit = 40): StageRow[] {
  return runs.flatMap((run) => run.stages.map((stage) => ({ run, stage })))
    .sort((a, b) => (b.stage.started_at ?? b.run.created_at).localeCompare(a.stage.started_at ?? a.run.created_at))
    .slice(0, limit);
}
