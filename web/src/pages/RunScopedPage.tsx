import { Navigate } from "react-router";
import { isTerminal } from "../api/types";
import { RunList } from "../components/run/RunList";
import { Panel } from "../components/ui/Panel";
import { ErrorState, Loading } from "../components/ui/States";
import { useCurrentRun } from "../state/currentRun";
import { useRuns } from "../state/queries";

/** Files / Reviews / Usage / Checkpoints / Live Observer belong to a run: go to the current one, or ask. */
export default function RunScopedPage({ tab, title }: { tab: string; title: string }) {
  const current = useCurrentRun();
  const runs = useRuns(20);
  if (current) return <Navigate replace to={`/runs/${encodeURIComponent(current)}?tab=${tab}`} />;
  if (runs.isLoading) return <Loading label="Finding runs" />;
  const active = runs.data?.items.find((r) => !isTerminal(r.status));
  if (tab === "activity" && active) return <Navigate replace to={`/runs/${encodeURIComponent(active.id)}`} />;
  return (
    <div className="mx-auto max-w-4xl space-y-4 p-4 sm:p-6">
      <h1 className="text-xl font-semibold">{title}</h1>
      <p className="text-sm text-subtle">{title} belongs to a run. Choose one:</p>
      <Panel bodyClassName="p-0">
        {runs.isError ? <ErrorState error={runs.error} what="runs" onRetry={() => void runs.refetch()} /> : <RunList runs={runs.data?.items ?? []} />}
      </Panel>
    </div>
  );
}
