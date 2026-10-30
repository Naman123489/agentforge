import { Link } from "react-router";
import type { RunDetail } from "../../api/types";
import { agentMeta } from "../../lib/agents";
import { durationBetween, fmtDuration, fmtInt, fmtRelative, fmtTokens, fmtUsd } from "../../lib/format";
import { countChecks, stageDisplay } from "../../lib/status";
import { Badge } from "../ui/Badge";
import { Panel } from "../ui/Panel";
import { platformTotals, stageHistory } from "./aggregate";

function Stat({ label, value, sub, tone = "" }: { label: string; value: React.ReactNode; sub?: string; tone?: string }) {
  return (
    <div className="panel p-4">
      <div className="text-xs font-medium text-subtle">{label}</div>
      <div className={`num mt-1 text-2xl font-semibold tracking-tight ${tone}`}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-subtle">{sub}</div>}
    </div>
  );
}

const unavailable = <span className="text-base font-medium text-subtle">unavailable</span>;

export function UsageInsight({ runs }: { runs: RunDetail[] }) {
  const t = platformTotals(runs);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Total runs" value={fmtInt(t.runs)} sub={t.active ? `${t.active} in progress` : `last ${runs.length} on this server`} />
        <Stat label="Successful builds" value={fmtInt(t.done)} tone="text-emerald-700" sub="status done" />
        <Stat label="Failed builds" value={fmtInt(t.failed)} tone={t.failed ? "text-rose-700" : ""} sub={t.stopped ? `+ ${t.stopped} stopped or cancelled` : undefined} />
        <Stat label="Average run time" value={t.avgDurationMs == null ? unavailable : fmtDuration(t.avgDurationMs)} sub="created → last update, finished runs" />
        <Stat label="Model calls" value={t.modelCalls == null ? unavailable : fmtInt(t.modelCalls)} sub={t.modelCalls == null ? "not reported per model" : undefined} />
        <Stat label="Tokens" value={fmtTokens(t.tokens)} sub="input + output" />
        <Stat label="Estimated cost" value={fmtUsd(t.estimatedCost)} sub="router estimate" />
        <Stat label="Confirmed cost" value={t.confirmedCost == null ? unavailable : fmtUsd(t.confirmedCost)} sub={t.confirmedCost == null ? "not confirmed by the provider" : "confirmed"} />
      </div>
    </div>
  );
}

export function ExecutionInsight({ runs }: { runs: RunDetail[] }) {
  const t = platformTotals(runs);
  const rows = stageHistory(runs);
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Reviewer approvals" value={fmtInt(t.approvals)} tone="text-emerald-700" />
        <Stat label="Reviewer rejections" value={fmtInt(t.rejections)} tone={t.rejections ? "text-amber-800" : ""} sub="sent back to the builder" />
        <Stat label="Runs with a checkpoint" value={fmtInt(t.withCheckpoint)} />
        <Stat label="Rolled-back stages" value={fmtInt(t.rolledBack)} />
      </div>
      <Panel title="Stage history" id="stage-history" bodyClassName="p-0">
        {rows.length === 0 ? <p className="px-5 py-6 text-sm text-subtle">No stages have run yet.</p> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[44rem] text-sm">
              <thead><tr className="text-left text-xs text-subtle">
                <th className="px-5 py-2 font-medium">Project</th><th className="px-3 py-2 font-medium">Agent</th>
                <th className="px-3 py-2 font-medium">Status</th><th className="px-3 py-2 font-medium">Checks</th>
                <th className="px-3 py-2 font-medium">Attempt</th><th className="px-3 py-2 font-medium">Duration</th>
                <th className="px-5 py-2 text-right font-medium">Started</th>
              </tr></thead>
              <tbody>
                {rows.map(({ run, stage }) => {
                  const d = stageDisplay(stage);
                  const checks = stage.review?.checks ?? stage.result?.checks ?? [];
                  const c = checks.length ? countChecks(checks) : null;
                  return (
                    <tr key={`${run.id}-${stage.id}`} className="border-t border-line/60">
                      <td className="max-w-48 truncate px-5 py-2.5">
                        <Link to={`/runs/${encodeURIComponent(run.id)}?stage=${encodeURIComponent(stage.id)}`} className="font-medium hover:underline">
                          {run.project_name || "Untitled"}
                        </Link>
                      </td>
                      <td className="px-3 py-2.5 text-muted">{agentMeta(stage.agent).short}</td>
                      <td className="px-3 py-2.5"><Badge tone={d.tone} dot pulse={d.active}>{d.label}</Badge>
                        {stage.review && <span className="ml-1.5 text-xs text-subtle">{stage.review.verdict}</span>}</td>
                      <td className="num px-3 py-2.5 text-xs text-muted">{c ? `${c.passed}/${c.total}${c.notExecuted ? ` · ${c.notExecuted} n/e` : ""}` : "—"}</td>
                      <td className="num px-3 py-2.5 text-muted">{stage.attempt}{stage.max_attempts ? `/${stage.max_attempts}` : ""}</td>
                      <td className="num px-3 py-2.5 text-muted">{fmtDuration(durationBetween(stage.started_at, stage.finished_at))}</td>
                      <td className="num px-5 py-2.5 text-right text-xs text-subtle">{fmtRelative(stage.started_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="border-t border-line/70 px-5 py-3 text-xs text-subtle">"n/e" = not executed. Checks that did not run are never counted as passed.</p>
      </Panel>
    </div>
  );
}
