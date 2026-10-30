import { useState } from "react";
import { GitBranch, History, RotateCcw } from "lucide-react";
import { isTerminal, type Checkpoint, type RunDetail, type RunEvent } from "../../api/types";
import { fmtRelative, fmtTime } from "../../lib/format";
import { useRollback } from "../../state/mutations";
import { useCheckpoints } from "../../state/queries";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { EmptyState, ErrorState, Loading } from "../ui/States";
import { useToast } from "../ui/Toast";
import { lastEventFor } from "../run/derive";

export function CheckpointsPanel({ run, events }: { run: RunDetail; events: RunEvent[] }) {
  const cps = useCheckpoints(run.id);
  const rollback = useRollback(run.id);
  const toast = useToast();
  const [target, setTarget] = useState<Checkpoint | null>(null);
  const [reason, setReason] = useState("");
  const [retry, setRetry] = useState(false);
  const rolling = run.status === "rolling_back";
  const lastRollback = lastEventFor(events, (e) => e.type === "rollback_started" || e.type === "rollback_finished");

  const confirm = () => {
    if (!target) return;
    rollback.mutate({ checkpoint_id: target.id, ...(reason.trim() ? { reason: reason.trim() } : {}), retry_after_rollback: retry }, {
      onSuccess: (r) => {
        setTarget(null);
        setReason("");
        // 202 = accepted, not done. The run state (and rollback_finished event) confirms completion.
        toast("info", r.message || `Rollback to ${target.git_tag} requested. Waiting for the server to confirm.`);
      },
    });
  };

  if (cps.isLoading) return <Loading label="Loading checkpoints" />;
  if (cps.isError) return <ErrorState error={cps.error} what="checkpoints" onRetry={() => void cps.refetch()} />;
  const list = [...(cps.data?.checkpoints ?? [])].reverse();
  const latest = list[0];

  return (
    <div className="p-4">
      {(rolling || lastRollback) && (
        <p role="status" className="mb-3 rounded-lg border border-amber-400/30 bg-amber-400/5 px-3 py-2 text-xs text-amber-900">
          {rolling ? "Rollback in progress (server status: rolling back)…" : lastRollback?.type === "rollback_finished"
            ? `Rollback confirmed by the server at ${fmtTime(lastRollback.timestamp)}.` : `Rollback started at ${fmtTime(lastRollback!.timestamp)}; waiting for confirmation.`}
        </p>
      )}
      {list.length === 0 ? (
        <EmptyState icon={<GitBranch className="size-5" />} title="No checkpoints yet">A checkpoint (git tag + checkpoint.json) is created after the Reviewer approves a stage.</EmptyState>
      ) : (
        <ol className="relative space-y-3 border-l border-line pl-5" aria-label="Checkpoints, newest first">
          {list.map((cp) => (
            <li key={cp.id} className="relative">
              <span aria-hidden className={`absolute -left-[1.6rem] top-3 size-2.5 rounded-full border-2 border-ink-850 ${cp === latest ? "bg-emerald-400" : "bg-line-strong"}`} />
              <div className="panel flex flex-wrap items-center gap-3 rounded-xl px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm text-fg">{cp.git_tag}</span>
                    <Badge tone="ok" dot>approved</Badge>
                    {cp === latest && <Badge tone="brand">latest</Badge>}
                  </div>
                  <p className="mt-0.5 text-sm text-muted">{cp.summary}</p>
                  <p className="num mt-1 text-xs text-subtle">
                    stage <span className="font-mono">{cp.stage_id}</span> · {cp.tests_passed} checks passed · {fmtTime(cp.created_at)} ({fmtRelative(cp.created_at)}) · id <span className="font-mono">{cp.id}</span>
                  </p>
                </div>
                <Button size="sm" icon={<RotateCcw className="size-3.5" />} disabled={rolling || (!isTerminal(run.status) && cp === latest)}
                  onClick={() => setTarget(cp)} title={rolling ? "A rollback is already in progress" : undefined}>
                  Roll back here
                </Button>
              </div>
            </li>
          ))}
        </ol>
      )}

      <ConfirmDialog open={!!target} title={`Roll back to ${target?.git_tag ?? ""}?`} confirmLabel="Request rollback" danger busy={rollback.isPending}
        onConfirm={confirm} onClose={() => { setTarget(null); rollback.reset(); }}>
        <p className="flex items-start gap-2"><History className="mt-0.5 size-4 shrink-0 text-amber-700" />
          The run workspace is restored to this approved checkpoint. Files written after it are discarded from the workspace (they remain in git history).</p>
        <label className="block text-xs text-subtle">Reason (optional, max 2000 chars)
          <textarea value={reason} maxLength={2000} onChange={(e) => setReason(e.target.value)} rows={2}
            className="mt-1 w-full rounded-lg border border-line bg-ink-900 px-3 py-2 text-sm text-fg" />
        </label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={retry} onChange={(e) => setRetry(e.target.checked)} className="size-4 accent-nv-600" />
          Retry the failed stage after rolling back</label>
        {rollback.isError && <ErrorState compact error={rollback.error} what="the rollback" />}
      </ConfirmDialog>
    </div>
  );
}
