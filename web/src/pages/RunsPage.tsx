import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "../api/endpoints";
import type { RunStatus } from "../api/types";
import { RunList } from "../components/run/RunList";
import { Button } from "../components/ui/Button";
import { Panel } from "../components/ui/Panel";
import { ErrorState, Loading } from "../components/ui/States";
import { RUN_STATUS } from "../lib/status";

export default function RunsPage() {
  const [status, setStatus] = useState<RunStatus | "">("");
  const q = useInfiniteQuery({
    queryKey: ["runs", "page", status],
    queryFn: ({ pageParam, signal }) => api.listRuns({ status: status || undefined, limit: 25, cursor: pageParam }, signal),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: 15_000,
  });
  const runs = q.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div className="mx-auto max-w-6xl space-y-4 p-4 sm:p-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
          <p className="text-sm text-subtle">Every build AgentForge has run on this server. Open one to watch it live.</p>
        </div>
        <label className="flex items-center gap-2 text-sm text-muted">Status
          <select value={status} onChange={(e) => setStatus(e.target.value as RunStatus | "")}
            className="h-9 rounded-lg border border-line bg-ink-900 px-2 text-sm text-fg">
            <option value="">All</option>
            {(Object.keys(RUN_STATUS) as RunStatus[]).map((s) => <option key={s} value={s}>{RUN_STATUS[s].label}</option>)}
          </select>
        </label>
      </div>
      <Panel bodyClassName="p-0">
        {q.isLoading ? <Loading label="Loading runs" />
          : q.isError ? <ErrorState error={q.error} what="runs" onRetry={() => void q.refetch()} />
          : <RunList runs={runs} empty={status ? "No runs with this status." : undefined} />}
        {q.hasNextPage && (
          <div className="border-t border-line p-3 text-center">
            <Button size="sm" loading={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>Load more</Button>
          </div>
        )}
      </Panel>
    </div>
  );
}
