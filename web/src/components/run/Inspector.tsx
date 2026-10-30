import type { ReviewReport, RunEvent, Stage, UsageSummary } from "../../api/types";
import { agentMeta } from "../../lib/agents";
import { describeEvent, explainRouting, TIER, TIER_TONE } from "../../lib/events";
import { fmtTime, fmtTokens, fmtUsd, shortModel } from "../../lib/format";
import { countChecks, stageDisplay } from "../../lib/status";
import { Badge } from "../ui/Badge";
import { lastEventFor } from "./derive";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="border-b border-line/70 px-4 py-3 last:border-0">
      <h3 className="panel-title mb-2">{title}</h3>
      {children}
    </section>
  );
}

export function Inspector({ stage, events, usage, review, stagesById }: {
  stage?: Stage; events: RunEvent[]; usage?: UsageSummary | null; review?: ReviewReport | null; stagesById: Map<string, Stage>;
}) {
  const route = stage ? lastEventFor(events, (e) => e.stage_id === stage.id && e.type === "route_decision")
    : lastEventFor(events, (e) => e.type === "route_decision");
  const rd = route?.data as Record<string, unknown> | undefined;
  const tier = rd && typeof rd.slot === "string" ? TIER[rd.slot] : undefined;
  const problems = events.filter((e) => ["model_call_failed", "error", "budget_exhausted", "budget_warning", "contract_change_requested"].includes(e.type)
    && (!stage || e.stage_id === stage.id)).slice(-3).reverse();
  const meta = stage ? agentMeta(stage.agent) : null;
  const failingSelf = stage?.result?.checks.filter((c) => !c.passed) ?? [];

  return (
    <div className="text-sm">
      <Section title={stage ? "Selected stage" : "Run focus"}>
        {stage && meta ? (
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-2 font-medium"><meta.icon className="size-4 text-muted" aria-hidden />{meta.label}</span>
              <Badge tone={stageDisplay(stage).tone} dot>{stageDisplay(stage).label}</Badge>
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              <dt className="text-subtle">Stage</dt><dd className="truncate font-mono text-muted">{stage.name}</dd>
              <dt className="text-subtle">Attempt</dt><dd className="num text-muted">{stage.attempt}{stage.max_attempts ? ` of ${stage.max_attempts}` : ""}</dd>
              <dt className="text-subtle">Window</dt><dd className="num text-muted">{fmtTime(stage.started_at)} → {stage.finished_at ? fmtTime(stage.finished_at) : "…"}</dd>
              {stage.files_allowed && stage.files_allowed.length > 0 && (<>
                <dt className="text-subtle">May write</dt><dd className="font-mono text-muted">{stage.files_allowed.join(", ")}</dd>
              </>)}
            </dl>
          </div>
        ) : <p className="text-xs text-subtle">Select a stage in the pipeline to inspect it.</p>}
      </Section>

      <Section title="Model routing">
        {rd ? (
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2">
              {tier && <Badge tone={TIER_TONE[tier]}>{tier}</Badge>}
              <span className="font-mono text-xs text-muted" title={String(rd.model_id ?? "")}>{shortModel(rd.model_id as string)}</span>
            </div>
            <p className="text-xs text-fg">{explainRouting(rd)}</p>
            <p className="num text-[11px] text-subtle">
              est. call cost {fmtUsd(typeof rd.estimated_cost_usd === "number" ? rd.estimated_cost_usd : null)} · {fmtTime(route!.timestamp)}
            </p>
          </div>
        ) : <p className="text-xs text-subtle">No routing decision reported{stage ? " for this stage" : ""} yet.</p>}
      </Section>

      <Section title="Tokens & cost (run)">
        {usage ? (
          <dl className="grid grid-cols-2 gap-y-1 text-xs">
            <dt className="text-subtle">Tokens</dt><dd className="num text-right text-muted">{fmtTokens(usage.total_tokens)}</dd>
            <dt className="text-subtle">Estimated</dt><dd className="num text-right text-muted">{fmtUsd(usage.estimated_cost_usd)}</dd>
            <dt className="text-subtle">Confirmed</dt>
            <dd className="num text-right text-muted">{usage.confirmed_cost_usd == null ? "not confirmed by provider" : fmtUsd(usage.confirmed_cost_usd)}</dd>
            <dt className="text-subtle">Remaining budget</dt><dd className="num text-right text-muted">{fmtUsd(usage.remaining_budget_usd ?? null)}</dd>
          </dl>
        ) : <p className="text-xs text-subtle">No usage reported yet.</p>}
      </Section>

      <Section title="Verification">
        {review ? (
          <p className="text-xs">
            <Badge tone={review.verdict === "approve" ? "ok" : review.verdict === "reject" ? "bad" : "warn"} dot>
              {review.verdict === "approve" ? "Approved on executed checks" : review.verdict === "reject" ? "Rejected" : "Blocked"}
            </Badge>
            <span className="ml-2 num text-subtle">{countChecks(review.checks).label}</span>
          </p>
        ) : <p className="text-xs text-subtle">No Reviewer report yet. The app is not verified.</p>}
        {failingSelf.length > 0 && (
          <ul className="mt-2 space-y-1 text-xs">
            {failingSelf.map((c) => <li key={c.name} className="text-rose-700">✕ {c.name} <span className="text-subtle">(builder self-check)</span></li>)}
          </ul>
        )}
      </Section>

      <Section title="Recent problems">
        {problems.length ? (
          <ul className="space-y-2">
            {problems.map((e) => {
              const v = describeEvent(e, stagesById);
              return <li key={e.id} className="text-xs"><span className="text-rose-800">{v.title}</span>{v.detail && <span className="block text-subtle">{v.detail}</span>}</li>;
            })}
          </ul>
        ) : <p className="text-xs text-subtle">None reported.</p>}
      </Section>
    </div>
  );
}
