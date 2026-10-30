import type { RunStatus, Stage, StageStatus } from "../api/types";

export type Tone = "neutral" | "brand" | "live" | "ok" | "warn" | "bad";

export const TONE_CLASSES: Record<Tone, { text: string; bg: string; border: string; dot: string }> = {
  neutral: { text: "text-muted", bg: "bg-ink-800", border: "border-line-strong", dot: "bg-subtle" },
  brand: { text: "text-nv-700", bg: "bg-nv-500/10", border: "border-nv-500/30", dot: "bg-nv-500" },
  // Running / waiting is amber; green is reserved for stages that genuinely passed their checks.
  live: { text: "text-amber-700", bg: "bg-amber-400/12", border: "border-amber-500/30", dot: "bg-amber-500" },
  ok: { text: "text-emerald-700", bg: "bg-emerald-500/10", border: "border-emerald-500/30", dot: "bg-emerald-500" },
  warn: { text: "text-amber-800", bg: "bg-amber-400/15", border: "border-amber-500/35", dot: "bg-amber-500" },
  bad: { text: "text-rose-700", bg: "bg-rose-500/10", border: "border-rose-500/30", dot: "bg-rose-500" },
};

export const RUN_STATUS: Record<RunStatus, { label: string; tone: Tone; live: boolean }> = {
  created: { label: "Queued", tone: "neutral", live: true },
  planning: { label: "Planning", tone: "live", live: true },
  contract: { label: "Contract", tone: "live", live: true },
  building: { label: "Building", tone: "live", live: true },
  verifying: { label: "Verifying", tone: "live", live: true },
  checkpointing: { label: "Checkpointing", tone: "live", live: true },
  rolling_back: { label: "Rolling back", tone: "warn", live: true },
  done: { label: "Done", tone: "ok", live: false },
  failed: { label: "Failed", tone: "bad", live: false },
  cancelled: { label: "Cancelled", tone: "neutral", live: false },
  stopped: { label: "Stopped", tone: "warn", live: false },
};

export const STAGE_STATUS: Record<StageStatus, { label: string; tone: Tone }> = {
  pending: { label: "Pending", tone: "neutral" },
  running: { label: "Running", tone: "live" },
  passed: { label: "Passed", tone: "ok" },
  failed: { label: "Failed", tone: "bad" },
  rolled_back: { label: "Rolled back", tone: "warn" },
  skipped: { label: "Skipped", tone: "neutral" },
  stopped: { label: "Stopped", tone: "warn" },
};

/** Check counts where "not executed" (skipped) is never counted as passed. */
export function countChecks(checks: { passed: boolean; skipped?: boolean }[]) {
  const notExecuted = checks.filter((c) => c.skipped).length;
  const passed = checks.filter((c) => c.passed && !c.skipped).length;
  return { passed, failed: checks.length - passed - notExecuted, notExecuted, total: checks.length,
    label: `${passed}/${checks.length} checks passed${notExecuted ? ` · ${notExecuted} not executed` : ""}` };
}

/** Display state for a stage. "Retrying" is derived only from the backend's own attempt counter. */
export function stageDisplay(stage: Stage): { label: string; tone: Tone; active: boolean } {
  const base = STAGE_STATUS[stage.status] ?? { label: stage.status, tone: "neutral" as Tone };
  if (stage.status === "running" && stage.attempt > 1)
    return { label: `Retrying · attempt ${stage.attempt}${stage.max_attempts ? `/${stage.max_attempts}` : ""}`, tone: "warn", active: true };
  if (stage.status === "pending" && stage.review?.verdict === "blocked") return { label: "Blocked", tone: "warn", active: false };
  return { ...base, active: stage.status === "running" };
}
