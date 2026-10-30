import { useRef, useState } from "react";
import { useNavigate } from "react-router";
import { Ban, Copy, GitPullRequestArrow, OctagonX, ShieldAlert, ShieldCheck } from "lucide-react";
import { isTerminal, type ReviewReport, type RunDetail, type RunEvent } from "../../api/types";
import { durationBetween, fmtDuration, fmtRelative } from "../../lib/format";
import { RUN_STATUS } from "../../lib/status";
import { setCurrentRun } from "../../state/currentRun";
import { newIdempotencyKey, useCancelRun, useFollowup } from "../../state/mutations";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { ErrorState } from "../ui/States";
import { useToast } from "../ui/Toast";
import { lastEventFor } from "./derive";

export function RunHeader({ run, review, events }: { run: RunDetail; review?: ReviewReport | null; events: RunEvent[] }) {
  const st = RUN_STATUS[run.status] ?? { label: run.status, tone: "neutral" as const, live: false };
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [follow, setFollow] = useState("");
  const followKey = useRef(newIdempotencyKey());
  const cancel = useCancelRun(run.id);
  const followup = useFollowup(run.id);
  const toast = useToast();
  const navigate = useNavigate();
  const terminal = isTerminal(run.status);
  const budgetStop = lastEventFor(events, (e) => e.type === "budget_exhausted");
  const verified = review?.verdict === "approve";

  const doCancel = () => cancel.mutate(undefined, {
    onSuccess: (r) => { setConfirmCancel(false); toast("info", r.message || "Cancellation requested. Waiting for the orchestrator to confirm."); },
  });
  const doFollowup = (e: React.FormEvent) => {
    e.preventDefault();
    if (follow.trim().length < 3 || followup.isPending) return;
    followup.mutate({ body: { prompt: follow.trim() }, key: followKey.current }, {
      onSuccess: (r) => {
        followKey.current = newIdempotencyKey();
        setFollow("");
        setCurrentRun(r.id);
        toast("ok", `Follow-up accepted as run ${r.id}.`);
        navigate(`/runs/${encodeURIComponent(r.id)}`);
      },
    });
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-xl font-semibold tracking-tight">{run.project_name || "Untitled project"}</h1>
            <Badge tone={st.tone} dot pulse={st.live}>{st.label}</Badge>
            {run.status === "done" && (verified
              ? <Badge tone="ok"><ShieldCheck className="size-3" aria-hidden /> Verified</Badge>
              : <Badge tone="warn"><ShieldAlert className="size-3" aria-hidden /> Not verified</Badge>)}
          </div>
          <p className="mt-1 line-clamp-2 max-w-3xl text-sm text-muted">{run.prompt}</p>
          <p className="num mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-subtle">
            <button type="button" className="inline-flex items-center gap-1 font-mono hover:text-fg" title="Copy run ID"
              onClick={() => { void navigator.clipboard?.writeText(run.id); toast("info", "Run ID copied."); }}>
              {run.id} <Copy className="size-3" aria-hidden />
            </button>
            <span>created {fmtRelative(run.created_at)}</span>
            <span>elapsed {fmtDuration(durationBetween(run.created_at, terminal ? run.updated_at : null))}</span>
            {run.current_stage_id && <span>stage <span className="font-mono">{run.current_stage_id}</span></span>}
          </p>
        </div>
        {!terminal && (
          <Button variant="danger" icon={<Ban className="size-4" />} onClick={() => setConfirmCancel(true)}>Cancel run</Button>
        )}
      </div>

      {(run.status === "stopped" || run.status === "failed") && (run.error || budgetStop) && (
        <div role="alert" className="flex items-start gap-3 rounded-xl border border-rose-400/30 bg-rose-500/5 px-4 py-3">
          <OctagonX className="mt-0.5 size-5 shrink-0 text-rose-700" aria-hidden />
          <div className="text-sm">
            <p className="font-medium text-rose-900">
              {budgetStop ? "Stopped by budget" : run.status === "stopped" ? "Run stopped" : "Run failed"}
              {run.error?.error && <span className="ml-2 font-mono text-xs text-rose-700">{run.error.error}</span>}
            </p>
            <p className="text-rose-800/80">{run.error?.message ?? String((budgetStop?.data as Record<string, unknown>)?.reason ?? "").replace(/_/g, " ")}</p>
          </div>
        </div>
      )}
      {run.status === "done" && !verified && (
        <div role="status" className="rounded-xl border border-amber-400/30 bg-amber-400/5 px-4 py-2.5 text-sm text-amber-900">
          The run finished but there is no approving Reviewer report, so the generated app is <strong>not verified</strong>.
        </div>
      )}

      {terminal && (
        <form onSubmit={doFollowup} className="flex flex-wrap items-center gap-2" aria-label="Follow-up request">
          <label htmlFor="followup" className="sr-only">Follow-up request</label>
          <input id="followup" value={follow} onChange={(e) => setFollow(e.target.value)} maxLength={20_000}
            placeholder="Follow-up, e.g. “Add Tic-Tac-Toe” (starts a new run from the latest checkpoint)"
            className="h-9 min-w-64 flex-1 rounded-lg border border-line bg-ink-900 px-3 text-sm text-fg placeholder:text-subtle focus:border-nv-400/60 focus:outline-none" />
          <Button type="submit" loading={followup.isPending} disabled={follow.trim().length < 3} icon={<GitPullRequestArrow className="size-4" />}>
            Start follow-up
          </Button>
          {followup.isError && <div className="w-full"><ErrorState compact error={followup.error} what="the follow-up" /></div>}
        </form>
      )}

      <ConfirmDialog open={confirmCancel} title="Cancel this run?" confirmLabel="Request cancellation" danger busy={cancel.isPending}
        onConfirm={doCancel} onClose={() => { setConfirmCancel(false); cancel.reset(); }}>
        <p>Cancellation is cooperative: the orchestrator stops at the next safe point. Model calls already sent may still finish and be billed.</p>
        <p>Approved checkpoints are kept. The run status updates once the server confirms.</p>
        {cancel.isError && <ErrorState compact error={cancel.error} what="the cancellation" />}
      </ConfirmDialog>
    </div>
  );
}
