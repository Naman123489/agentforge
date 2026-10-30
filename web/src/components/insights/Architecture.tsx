import type { ReactNode } from "react";
import { ArrowDown, ArrowRight, Split } from "lucide-react";

// The architecture as it exists in this repository (agentforge/). Modules owned by teammates that are not
// wired into this API yet are drawn dashed and labelled, rather than presented as running.
function Node({ title, where, children, dashed, accent = "border-line" }: {
  title: string; where: string; children: ReactNode; dashed?: boolean; accent?: string;
}) {
  return (
    <div className={`min-w-0 flex-1 rounded-2xl border bg-white/85 p-3.5 shadow-sm ${dashed ? "border-dashed border-line-strong bg-white/50" : accent}`}>
      <div className="text-sm font-semibold">{title}</div>
      <div className="mt-0.5 truncate font-mono text-[11px] text-subtle" title={where}>{where}</div>
      <p className="mt-2 text-xs leading-relaxed text-muted">{children}</p>
    </div>
  );
}

function Arrow({ wide = false }: { wide?: boolean }) {
  return (
    <div aria-hidden className="flex shrink-0 items-center justify-center text-subtle">
      <ArrowDown className={`size-4 ${wide ? "xl:hidden" : "md:hidden"}`} />
      <ArrowRight className={`hidden size-4 ${wide ? "xl:block" : "md:block"}`} />
    </div>
  );
}

export function Architecture() {
  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-2 md:flex-row md:items-stretch">
        <Node title="User" where="browser">Describes an app; watches it being built.</Node>
        <Arrow />
        <Node title="Dashboard" where="web/ (this app)">Calls only the documented Control Plane API; streams events over SSE.</Node>
        <Arrow />
        <Node title="Control Plane API" where="server/app.py">Runs, stages, events, files, review, usage, checkpoints, preview. Bearer-token auth.</Node>
        <Arrow />
        <Node title="Orchestrator" where="server/pipeline.py" accent="border-nv-500/40">State machine per run: retries, wall-time and cancellation; budgets enforced through the router.</Node>
      </div>

      <div className="flex flex-col gap-2 xl:flex-row xl:items-stretch">
        <Node title="Decision Model · Push/Pull distribution" where="teammate modules" dashed>
          Task assignment between agents. Owned by teammates and not connected to this API yet; the orchestrator schedules stages today.
        </Node>
        <Arrow wide />
        <Node title="Planner / Architect" where="server/planner.py">Writes and validates the OpenAPI 3.1 contract both builders follow.</Node>
        <Arrow wide />
        <div className="flex min-w-0 flex-[1.6] flex-col gap-2 rounded-2xl border border-dashed border-amber-400/50 bg-amber-50/40 p-2">
          <span className="px-1 text-[10px] font-semibold tracking-wider text-amber-800 uppercase">in parallel</span>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Node title="Backend Agent" where="engine/agents/backend.py">FastAPI + SQLite; pytest and contract checks.</Node>
            <Node title="Frontend Agent" where="engine/agents/frontend.py">React + TypeScript; tsc, vite build, served-with-backend.</Node>
          </div>
        </div>
        <Arrow wide />
        <Node title="Reviewer / Integrator" where="pipeline · _review" accent="border-emerald-500/40">Re-runs every check itself. Approve → checkpoint; reject → builder retries with the failures.</Node>
        <Arrow wide />
        <Node title="Checkpoint & preview" where="git tag · preview_app.py">Approved state is tagged; exhausted retries roll back to the last checkpoint.</Node>
      </div>

      <div className="flex items-start gap-3 rounded-2xl bg-[#111318] p-4 text-white">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-white/10"><Split className="size-4 text-nv-400" aria-hidden /></span>
        <div className="min-w-0">
          <div className="text-sm font-semibold">Model Router <span className="ml-1 font-mono text-[11px] font-normal text-white/50">engine/router/</span></div>
          <p className="mt-1 text-xs leading-relaxed text-white/70">
            Shared by the Planner, Backend and Frontend agents. Every model call goes through it: it picks one of three slots
            (premium, coding, economy) by task and failure history, falls back on errors, and stops the run at its budget.
            Models are served by Nebius Token Factory.
          </p>
        </div>
      </div>
    </div>
  );
}
