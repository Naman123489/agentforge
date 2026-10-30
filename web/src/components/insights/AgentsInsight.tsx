import type { AgentRole, RunDetail } from "../../api/types";
import { AGENTS } from "../../lib/agents";
import { fmtRelative, shortModel } from "../../lib/format";
import { Badge } from "../ui/Badge";
import { agentStats } from "./aggregate";

// Responsibilities and inputs/outputs as implemented in server/ and engine/agents/.
const SPEC: { role: AgentRole; input: string; output: string; model: string }[] = [
  { role: "planner", input: "Your prompt", output: "contract/openapi.yaml (validated)", model: "Router · premium for planning" },
  { role: "backend", input: "Contract + stage assignment", output: "backend/** + self-checks", model: "Router · coding slot, escalates" },
  { role: "frontend", input: "Contract + generated typed client", output: "frontend/** + self-checks", model: "Router · coding slot, escalates" },
  { role: "reviewer", input: "Both builders' output", output: "ReviewReport (approve / reject)", model: "No model: executes checks" },
  { role: "deploy", input: "Approved checkpoint", output: "Deployment status", model: "Only when enabled on the server" },
];

export function AgentsInsight({ runs }: { runs: RunDetail[] }) {
  const stats = agentStats(runs);
  return (
    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {SPEC.map((s) => {
        const meta = AGENTS[s.role];
        const st = stats.get(s.role);
        const Icon = meta.icon;
        const status = st?.running ? <Badge tone="live" dot pulse>Running</Badge>
          : st ? <Badge tone="neutral" dot>Idle</Badge> : <Badge tone="neutral">No stages yet</Badge>;
        return (
          <article key={s.role} className="panel flex flex-col p-5">
            <div className="flex items-start gap-3">
              <span className={`grid size-10 shrink-0 place-items-center rounded-xl ${meta.accent}`}><Icon className="size-5" aria-hidden /></span>
              <div className="min-w-0 flex-1">
                <h3 className="font-semibold tracking-tight">{meta.label}</h3>
                <p className="text-sm text-subtle">{meta.role}</p>
              </div>
              {status}
            </div>
            <dl className="mt-4 grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1.5 text-sm">
              <dt className="text-subtle">Input</dt><dd className="text-muted">{s.input}</dd>
              <dt className="text-subtle">Output</dt><dd className="text-muted">{s.output}</dd>
              <dt className="text-subtle">Model</dt><dd className="text-muted">{s.model}</dd>
              <dt className="text-subtle">Last used</dt>
              <dd className="truncate font-mono text-xs text-muted" title={st?.lastModels.join(", ")}>
                {st?.lastModels.length ? st.lastModels.map(shortModel).join(", ") : "—"}
              </dd>
            </dl>
            <div className="mt-auto grid grid-cols-3 gap-2 border-t border-line/70 pt-4 text-center">
              <div><div className="num text-lg font-semibold text-emerald-700">{st?.completed ?? 0}</div><div className="text-[11px] text-subtle">passed</div></div>
              <div><div className={`num text-lg font-semibold ${st?.failed ? "text-rose-700" : ""}`}>{st?.failed ?? 0}</div><div className="text-[11px] text-subtle">failed</div></div>
              <div><div className="num text-lg font-semibold">{st?.attempts ?? 0}</div><div className="text-[11px] text-subtle">stage runs</div></div>
            </div>
            {st?.lastAt && <p className="mt-2 text-center text-[11px] text-subtle">last activity {fmtRelative(st.lastAt)}</p>}
          </article>
        );
      })}
      <article className="panel flex flex-col justify-center border-dashed p-5">
        <h3 className="font-semibold tracking-tight">Decision Model · Push/Pull</h3>
        <p className="mt-1 text-sm text-subtle">Built by teammates as separate modules. This API does not report their status yet, so nothing is shown for them here.</p>
      </article>
    </div>
  );
}
