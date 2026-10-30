// The run's journey at a glance, derived only from server state: stage statuses (GET /runs/{id}/stages),
// checkpoints, the review verdict and the run status. Green means the stage really passed its checks.
import { Check, Circle, Compass, Flag, GitCommitVertical, Hammer, Loader2, ShieldCheck, X, type LucideIcon } from "lucide-react";
import type { Checkpoint, ReviewReport, RunDetail, Stage } from "../../api/types";
import { fmtTokens, fmtUsd } from "../../lib/format";
import { stageDisplay, type Tone } from "../../lib/status";
import { latestStage } from "./derive";

interface Step { key: string; label: string; sub: string; icon: LucideIcon; tone: Tone; active: boolean }

const fromStage = (s: Stage | undefined): { tone: Tone; active: boolean; sub?: string } => {
  if (!s) return { tone: "neutral", active: false };
  const d = stageDisplay(s);
  return { tone: d.tone, active: d.active, sub: d.label };
};

/** Combine the two parallel builders: failed if either failed, passed only if both passed. */
function combine(a: Stage | undefined, b: Stage | undefined): { tone: Tone; active: boolean; sub: string } {
  const sa = fromStage(a), sb = fromStage(b);
  const present = [a, b].filter(Boolean) as Stage[];
  if (present.length === 0) return { tone: "neutral", active: false, sub: "Waiting" };
  if (sa.tone === "bad" || sb.tone === "bad") return { tone: "bad", active: false, sub: "A builder failed" };
  if (present.every((s) => s.status === "passed" || s.status === "skipped") && present.some((s) => s.status === "passed"))
    return { tone: "ok", active: false, sub: "Frontend + backend passed" };
  if (sa.active || sb.active) return { tone: "live", active: true, sub: "Frontend ∥ backend" };
  return { tone: sa.tone === "warn" || sb.tone === "warn" ? "warn" : "neutral", active: false, sub: "Waiting" };
}

export function buildSteps(run: RunDetail, stages: Stage[], checkpoints: Checkpoint[], review?: ReviewReport | null): Step[] {
  const planner = fromStage(latestStage(stages, "planner"));
  const build = combine(latestStage(stages, "backend"), latestStage(stages, "frontend"));
  const reviewer = latestStage(stages, "reviewer");
  const rev = fromStage(reviewer);
  const cp = checkpoints.filter((c) => c.stage_id !== latestStage(stages, "planner")?.id).at(-1) ?? null;
  const approved = review?.verdict === "approve" || reviewer?.status === "passed";
  const cpStep = cp && approved ? { tone: "ok" as Tone, active: false, sub: cp.git_tag }
    : run.status === "checkpointing" ? { tone: "live" as Tone, active: true, sub: "Creating" }
    : { tone: "neutral" as Tone, active: false, sub: "After approval" };
  const done: { tone: Tone; active: boolean; sub: string } =
    run.status === "done" ? (approved ? { tone: "ok", active: false, sub: "Verified" } : { tone: "warn", active: false, sub: "Not verified" })
    : run.status === "failed" ? { tone: "bad", active: false, sub: "Failed" }
    : run.status === "stopped" ? { tone: "warn", active: false, sub: "Stopped" }
    : run.status === "cancelled" ? { tone: "neutral", active: false, sub: "Cancelled" }
    : { tone: "neutral", active: false, sub: "In progress" };
  return [
    { key: "plan", label: "Planning", icon: Compass, ...planner, sub: planner.sub ?? "Waiting" },
    { key: "build", label: "Building", icon: Hammer, ...build },
    { key: "verify", label: "Integration & tests", icon: ShieldCheck, ...rev, sub: rev.sub ?? "Reviewer" },
    { key: "cp", label: "Checkpoint", icon: GitCommitVertical, ...cpStep },
    { key: "done", label: "Completion", icon: Flag, ...done },
  ];
}

const DOT: Record<Tone, string> = {
  ok: "bg-emerald-500 text-white border-emerald-500",
  live: "bg-amber-50 text-amber-700 border-amber-400",
  warn: "bg-amber-50 text-amber-800 border-amber-400",
  bad: "bg-rose-500 text-white border-rose-500",
  brand: "bg-nv-50 text-nv-700 border-nv-500/50",
  neutral: "bg-white text-subtle border-line-strong",
};

export function RunProgress({ run, stages, checkpoints, review }: {
  run: RunDetail; stages: Stage[]; checkpoints: Checkpoint[]; review?: ReviewReport | null;
}) {
  const steps = buildSteps(run, stages, checkpoints, review);
  const u = run.usage;
  return (
    <section aria-label="Run progress" className="panel px-4 py-4 sm:px-6">
      <ol className="grid grid-cols-5 gap-1">
        {steps.map((s, i) => {
          const Icon = s.tone === "ok" ? Check : s.tone === "bad" ? X : s.active ? Loader2 : s.tone === "neutral" ? Circle : s.icon;
          const lineDone = s.tone === "ok";
          return (
            <li key={s.key} className="relative flex flex-col items-center text-center" aria-label={`${s.label}: ${s.sub}`}>
              {i < steps.length - 1 && (
                <span aria-hidden className={`absolute top-4 left-[calc(50%+1.25rem)] h-0.5 w-[calc(100%-2.5rem)] rounded-full ${lineDone ? "bg-emerald-500/60" : "bg-line"}`} />
              )}
              <span className={`relative z-10 grid size-8 place-items-center rounded-full border-2 ${DOT[s.tone]} ${s.active ? "ring-4 ring-amber-400/20" : ""}`}>
                <Icon className={`size-4 ${s.active ? "motion-safe:animate-spin" : ""}`} aria-hidden />
              </span>
              <span className="mt-2 text-[11px] font-medium text-fg sm:text-sm">{s.label}</span>
              <span className="hidden max-w-full truncate px-1 text-xs text-subtle sm:block">{s.sub}</span>
            </li>
          );
        })}
      </ol>
      <p className="num mt-4 flex flex-wrap justify-center gap-x-4 gap-y-1 border-t border-line/70 pt-3 text-xs text-subtle sm:justify-end"
        title="From GET /runs/{id}">
        <span>{fmtTokens(u.total_tokens)} tokens</span>
        <span>{fmtUsd(u.confirmed_cost_usd ?? u.estimated_cost_usd)} {u.confirmed_cost_usd == null ? "estimated" : "confirmed"}</span>
        {u.remaining_budget_usd != null && <span>{fmtUsd(u.remaining_budget_usd)} budget left</span>}
      </p>
    </section>
  );
}
