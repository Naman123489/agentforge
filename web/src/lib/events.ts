// Turns contract RunEvents into readable rows. Explanations are derived only from fields present in the
// event (e.g. the router's own `reasons`); nothing is invented when the data is missing.
import type { RunEvent, RunEventType, Stage } from "../api/types";
import type { Tone } from "./status";

export type EventCategory = "agents" | "models" | "tests" | "system";

export const CATEGORY: Record<RunEventType, EventCategory> = {
  run_created: "system", run_status_changed: "system", run_finished: "system", error: "system",
  stage_started: "agents", stage_progress: "agents", stage_finished: "agents", agent_message: "agents",
  contract_change_requested: "agents",
  route_decision: "models", model_call_started: "models", model_call_ok: "models", model_call_failed: "models",
  budget_warning: "models", budget_exhausted: "models",
  review_started: "tests", review_finished: "tests",
  checkpoint_created: "system", rollback_started: "system", rollback_finished: "system",
  preview_ready: "system", deployment_status: "system",
};

/** The router reports slots as A/B/C; the platform's ModelStatus calls them premium/coding/economy. */
export const TIER: Record<string, "premium" | "coding" | "economy"> = {
  A: "premium", B: "coding", C: "economy", premium: "premium", coding: "coding", economy: "economy",
};
export const TIER_TONE: Record<string, Tone> = { premium: "brand", coding: "neutral", economy: "neutral" };

type Data = Record<string, unknown>;
const str = (v: unknown): string | undefined => (typeof v === "string" && v ? v : undefined);
const num = (v: unknown): number | undefined => (typeof v === "number" && Number.isFinite(v) ? v : undefined);

export function eventAgent(ev: RunEvent, stagesById?: Map<string, Stage>): string | undefined {
  const d = ev.data as Data;
  const task = d.task as Data | undefined;
  return str(d.agent) ?? str(task?.agent) ?? (ev.stage_id ? stagesById?.get(ev.stage_id)?.agent : undefined)
    ?? (ev.stage_id && /^(planner|backend|frontend|reviewer|deploy)/.exec(ev.stage_id)?.[1]) ?? undefined;
}

const FAIL_REASON: Record<string, string> = {
  rate_limited: "was rate-limited", circuit_open: "kept failing (circuit open)", server_error: "returned a server error",
  timeout: "timed out", connection: "was unreachable", "invalid output after repair": "returned invalid output",
  context_length: "could not fit the context", model_not_found: "is not available at the provider", slow: "is slow",
  degraded: "is slow",
};

/** Human explanation of a router decision, built from its own `reasons` strings. */
export function explainRouting(d: Data): string {
  const reasons = Array.isArray(d.reasons) ? (d.reasons as string[]) : [];
  const tierOf = (s?: string) => (s ? TIER[s] ?? s : "selected");
  for (const r of [...reasons].reverse()) {
    let m = /escalate B->A after (\d+) failed/.exec(r);
    if (m) return `Escalated to the premium model after ${m[1]} failed attempt${m[1] === "1" ? "" : "s"}.`;
    m = /promote C->B after (\d+) failed/.exec(r);
    if (m) return `Promoted from the economy to the coding model after ${m[1]} failed attempt${m[1] === "1" ? "" : "s"}.`;
    m = /using fallback (\w) \((?:excluded for this call: )?([\w ]+)\)/.exec(r);
    if (m) return `Fell back to the ${tierOf(m[1])} model: the preferred model ${FAIL_REASON[m[2]] ?? m[2]}.`;
    m = /(\w) back in ([\d.]+)s: wait instead of upgrading/.exec(r);
    if (m) return `Waiting ${m[2]}s for the ${tierOf(m[1])} model instead of paying for an upgrade.`;
    if (/low budget: low-risk task downgraded/.test(r)) return "Downgraded to the economy model to protect the remaining budget.";
    m = /premium unavailable \((.+)\) -> target B/.exec(r);
    if (m) return `Premium model not used (${m[1]}); using the coding model.`;
    m = /expected cost to success: .* -> (\w)/.exec(r);
    if (m) return m[1] === "A" ? "Premium model chosen: cheaper overall once likely retries are counted."
      : "Coding model chosen: premium would not pay for itself here.";
    m = /^stop: (\w+)/.exec(r);
    if (m) return `Stopped: ${m[1].replace(/_/g, " ")}.`;
  }
  const def = reasons.find((r) => r.startsWith("default for "));
  const m = def && /default for (\w+): slot (\w)/.exec(def);
  if (m) return `Default ${tierOf(m[2])} model for ${m[1].replace(/_/g, " ")}.`;
  return str(d.reason) ?? "Routing decision (no reason supplied).";
}

export interface EventView {
  title: string;
  detail?: string;
  tone: Tone;
  category: EventCategory;
  agent?: string;
  model?: string;
}

export function describeEvent(ev: RunEvent, stagesById?: Map<string, Stage>): EventView {
  const d = ev.data as Data;
  const agent = eventAgent(ev, stagesById);
  const category = CATEGORY[ev.type] ?? "system";
  const model = str(d.model_id);
  const msg = str(d.message) ?? str(d.summary) ?? str(d.detail);
  const base = { category, agent, model };
  switch (ev.type) {
    case "run_created": return { ...base, title: "Run created", detail: msg, tone: "brand" };
    case "run_status_changed": return { ...base, title: `Run ${str(d.status)?.replace(/_/g, " ") ?? "status changed"}`, detail: msg, tone: "brand" };
    case "run_finished": {
      const st = str(d.status);
      return { ...base, title: `Run finished${st ? `: ${st}` : ""}`, detail: msg ?? str(d.reason),
        tone: st === "done" ? "ok" : st === "failed" ? "bad" : "warn" };
    }
    case "stage_started": return { ...base, title: `${str(d.name) ?? ev.stage_id ?? "Stage"} started`, detail: msg, tone: "live" };
    case "stage_progress": return { ...base, title: str(d.name) ?? "Progress", detail: msg, tone: "live" };
    case "stage_finished": {
      const st = str(d.status);
      const checks = num(d.checks_passed) !== undefined ? `${num(d.checks_passed)} passed, ${num(d.checks_failed) ?? 0} failed` : undefined;
      return { ...base, title: `${str(d.name) ?? ev.stage_id ?? "Stage"} ${st ?? "finished"}`,
        detail: [msg, checks].filter(Boolean).join(" · ") || undefined,
        tone: st === "passed" ? "ok" : st === "failed" ? "bad" : "warn" };
    }
    case "agent_message": return { ...base, title: msg ?? "Agent message", detail: str(d.details), tone: "neutral" };
    case "contract_change_requested": return { ...base, title: "Contract change requested", detail: str(d.problem) ?? msg, tone: "warn" };
    case "route_decision": {
      const action = str(d.action);
      const tier = TIER[str(d.slot) ?? ""];
      const task = (d.task as Data | undefined)?.task_type;
      const title = action === "stop" ? "Router stopped the call" : action === "wait" ? "Router waiting for capacity"
        : `Routed ${typeof task === "string" ? task.replace(/_/g, " ") : "call"} → ${tier ?? "model"}`;
      return { ...base, title, detail: explainRouting(d), tone: action === "stop" ? "bad" : action === "wait" ? "warn" : TIER_TONE[tier ?? ""] ?? "brand" };
    }
    case "model_call_started": return { ...base, title: `Calling ${TIER[str(d.slot) ?? ""] ?? ""} model`.replace("  ", " "), tone: "live" };
    case "model_call_ok": {
      const tokens = (num(d.input_tokens) ?? 0) + (num(d.output_tokens) ?? 0);
      const lat = num(d.latency_s);
      return { ...base, title: "Model call succeeded", tone: "ok",
        detail: `${tokens.toLocaleString()} tokens${lat !== undefined ? ` · ${lat.toFixed(1)} s` : ""}` };
    }
    case "model_call_failed": return { ...base, title: `Model call failed: ${str(d.error_kind)?.replace(/_/g, " ") ?? "error"}`, detail: str(d.error), tone: "bad" };
    case "budget_warning": return { ...base, title: "Budget warning: low-budget mode", detail: msg, tone: "warn" };
    case "budget_exhausted": return { ...base, title: "Stopped by budget", detail: str(d.reason)?.replace(/_/g, " ") ?? msg, tone: "bad" };
    case "review_started": return { ...base, title: "Review started", detail: msg, tone: "live" };
    case "review_finished": {
      const v = str(d.verdict);
      return { ...base, title: v === "approve" ? "Review approved (checks passed)" : v === "reject" ? "Review rejected" : `Review ${v ?? "finished"}`,
        detail: msg, tone: v === "approve" ? "ok" : v === "reject" ? "bad" : "warn" };
    }
    case "checkpoint_created": return { ...base, title: `Checkpoint ${str(d.git_tag) ?? ""}`.trim(), detail: msg, tone: "ok" };
    case "rollback_started": return { ...base, title: "Rollback started", detail: msg, tone: "warn" };
    case "rollback_finished": return { ...base, title: "Rollback finished", detail: msg, tone: "warn" };
    case "preview_ready": return { ...base, title: "Preview ready", detail: str(d.url), tone: "ok" };
    case "deployment_status": return { ...base, title: `Deployment ${str(d.status) ?? "update"}`, detail: msg ?? str(d.url), tone: "brand" };
    case "error": return { ...base, title: str(d.source) === "model_router" ? "Model router stopped" : "Error",
      detail: [str(d.reason)?.replace(/_/g, " "), str(d.detail) ?? msg].filter(Boolean).join(" — "), tone: "bad" };
    default: return { ...base, title: String(ev.type), detail: msg, tone: "neutral" };
  }
}
