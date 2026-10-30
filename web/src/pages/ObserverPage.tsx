import { useEffect, useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import {
  Activity, CircuitBoard, FileCode2, FileJson2, FlaskConical, GitBranch, MonitorPlay, Rocket, SearchX,
} from "lucide-react";
import { ApiError } from "../api/client";
import { isTerminal, type Stage } from "../api/types";
import { AgentPanel } from "../components/run/AgentPanel";
import { activityByAgent } from "../components/run/derive";
import { EventStream } from "../components/run/EventStream";
import { Inspector } from "../components/run/Inspector";
import { RunHeader } from "../components/run/RunHeader";
import { RunProgress } from "../components/run/RunProgress";
import { StagePipeline } from "../components/run/StagePipeline";
import { CheckpointsPanel } from "../components/panels/CheckpointsPanel";
import { ContractPanel } from "../components/panels/ContractPanel";
import { DeployPanel } from "../components/panels/DeployPanel";
import { FilesPanel } from "../components/panels/FilesPanel";
import { PreviewPanel } from "../components/panels/PreviewPanel";
import { ReviewPanel } from "../components/panels/ReviewPanel";
import { UsagePanel } from "../components/panels/UsagePanel";
import { Panel } from "../components/ui/Panel";
import { EmptyState, ErrorState, Loading } from "../components/ui/States";
import { Tabs, type TabDef } from "../components/ui/Tabs";
import { setCurrentRun } from "../state/currentRun";
import { useCheckpoints, useReview, useRun, useStages, useUsage } from "../state/queries";
import { useRunEvents } from "../state/runEvents";

type TabKey = "activity" | "files" | "preview" | "review" | "usage" | "checkpoints" | "contract" | "deploy";
const TABS: TabDef<TabKey>[] = [
  { key: "activity", label: "Activity", icon: <Activity className="size-4" /> },
  { key: "files", label: "Files", icon: <FileCode2 className="size-4" /> },
  { key: "preview", label: "Preview", icon: <MonitorPlay className="size-4" /> },
  { key: "review", label: "Review", icon: <FlaskConical className="size-4" /> },
  { key: "usage", label: "Models & Usage", icon: <CircuitBoard className="size-4" /> },
  { key: "checkpoints", label: "Checkpoints", icon: <GitBranch className="size-4" /> },
  { key: "contract", label: "Contract", icon: <FileJson2 className="size-4" /> },
  { key: "deploy", label: "Deploy", icon: <Rocket className="size-4" /> },
];

export default function ObserverPage() {
  const { runId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.key === params.get("tab"))?.key ?? "activity") as TabKey;
  const selectedStage = params.get("stage");
  const qc = useQueryClient();

  const run = useRun(runId);
  const live = run.data ? !isTerminal(run.data.status) : false;
  const stagesQ = useStages(runId, run.data);
  const stages: Stage[] = useMemo(() => stagesQ.data?.stages ?? run.data?.stages ?? [], [stagesQ.data, run.data]);
  const events = useRunEvents(runId, qc, live, run.isSuccess);
  const usage = useUsage(runId, run.data);
  const review = useReview(runId);
  const checkpoints = useCheckpoints(runId);

  const missing = run.error instanceof ApiError && run.error.kind === "not_found";
  useEffect(() => {
    if (run.isSuccess) setCurrentRun(runId);
    else if (missing) setCurrentRun(null); // don't keep nav links pointing at a run the server doesn't know
  }, [run.isSuccess, missing, runId]);

  const stagesById = useMemo(() => new Map(stages.map((s) => [s.id, s])), [stages]);
  const activity = useMemo(() => activityByAgent(events.events, stagesById), [events.events, stagesById]);
  const focusStage = (selectedStage && stagesById.get(selectedStage))
    || stages.find((s) => s.status === "running") || stages.at(-1);

  const setTab = (k: TabKey) => setParams((q) => { q.set("tab", k); return q; }, { replace: true });
  const selectStage = (id: string) => setParams((q) => { q.set("stage", id); return q; }, { replace: true });

  if (run.isLoading) return <Loading label="Loading run" />;
  if (run.isError) {
    if (run.error instanceof ApiError && run.error.kind === "not_found")
      return <EmptyState icon={<SearchX className="size-5" />} title="Run not found" action={<Link className="text-sm text-nv-700 hover:underline" to="/runs">Back to runs</Link>}>
        No run with ID <code className="font-mono">{runId}</code> exists on this server.</EmptyState>;
    return <ErrorState error={run.error} what={`run ${runId}`} onRetry={() => void run.refetch()} />;
  }
  const r = run.data!;

  return (
    <div className="mx-auto max-w-[1700px] space-y-4 p-4 sm:p-6 lg:p-8">
      <RunHeader run={r} review={review.data} events={events.events} />
      <RunProgress run={r} stages={stages} checkpoints={checkpoints.data?.checkpoints ?? []} review={review.data} />

      <Panel title="Execution pipeline" id="pipeline"
        actions={stagesQ.isError ? <span className="text-xs text-rose-700">stages unavailable</span> : <span className="text-[11px] text-subtle">click a stage to inspect</span>}>
        {stages.length === 0 && !stagesQ.isLoading
          ? <p className="text-sm text-subtle">The orchestrator has not reported any stages yet.</p>
          : <StagePipeline stages={stages} events={events.events} checkpoints={checkpoints.data?.checkpoints ?? []}
              selectedStage={focusStage?.id ?? null} onSelect={selectStage} />}
      </Panel>

      <div className="grid gap-4 2xl:grid-cols-[minmax(0,1fr)_22rem] xl:grid-cols-[minmax(0,1fr)_20rem]">
        <section className="panel min-w-0 overflow-hidden" aria-label="Run workspace">
          <Tabs tabs={TABS} active={tab} onChange={setTab} label="Run views" />
          <div id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`} className="min-w-0">
            {tab === "activity" && (
              <div className="grid min-h-[34rem] lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
                <div className="border-b border-line p-3 lg:border-r lg:border-b-0">
                  <h2 className="panel-title mb-2.5 px-1">Agents</h2>
                  <AgentPanel run={r} stages={stages} activity={activity} stagesById={stagesById} />
                </div>
                <div className="flex max-h-[44rem] min-h-0 flex-col">
                  <EventStream events={events.events} stagesById={stagesById} status={events.status} />
                </div>
              </div>
            )}
            {tab === "files" && <FilesPanel runId={runId} stages={stages} />}
            {tab === "preview" && <PreviewPanel runId={runId} />}
            {tab === "review" && <ReviewPanel run={r} stages={stages} />}
            {tab === "usage" && <UsagePanel usage={usage.data} usageLoading={usage.isLoading} usageError={usage.error}
              events={events.events} activity={activity} stagesById={stagesById} />}
            {tab === "checkpoints" && <CheckpointsPanel run={r} events={events.events} />}
            {tab === "contract" && <ContractPanel runId={runId} />}
            {tab === "deploy" && <DeployPanel run={r} />}
          </div>
        </section>
        <aside className="panel h-fit min-w-0 xl:sticky xl:top-4" aria-label="Inspector">
          <Inspector stage={focusStage} events={events.events} usage={usage.data} review={review.data} stagesById={stagesById} />
        </aside>
      </div>
    </div>
  );
}
