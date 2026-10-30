import { useSearchParams } from "react-router";
import { Activity, BookOpen, Bot, Boxes, ChartColumn, Network } from "lucide-react";
import { AgentsInsight } from "../components/insights/AgentsInsight";
import { Architecture } from "../components/insights/Architecture";
import { Docs } from "../components/insights/Docs";
import { ExecutionInsight, UsageInsight } from "../components/insights/ExecutionInsight";
import { ModelsInsight } from "../components/insights/ModelsInsight";
import { Badge } from "../components/ui/Badge";
import { ErrorState, Loading } from "../components/ui/States";
import { Tabs, type TabDef } from "../components/ui/Tabs";
import { useHealth, useRuns } from "../state/queries";

type TabKey = "architecture" | "agents" | "models" | "execution" | "usage" | "docs";
const TABS: TabDef<TabKey>[] = [
  { key: "architecture", label: "Architecture", icon: <Network className="size-4" /> },
  { key: "agents", label: "Agents", icon: <Bot className="size-4" /> },
  { key: "models", label: "Model router", icon: <Boxes className="size-4" /> },
  { key: "execution", label: "Execution", icon: <Activity className="size-4" /> },
  { key: "usage", label: "Usage", icon: <ChartColumn className="size-4" /> },
  { key: "docs", label: "Documentation", icon: <BookOpen className="size-4" /> },
];
const NEEDS_RUNS: TabKey[] = ["agents", "models", "execution", "usage"];

/** Page 4: the detail that stays off the create page. Every number comes from GET /runs, /models or /health. */
export default function InsightsPage() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.key === params.get("tab"))?.key ?? "architecture") as TabKey;
  const runs = useRuns(100);
  const health = useHealth();
  const items = runs.data?.items ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-4 sm:p-6 lg:p-10">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="eyebrow">Control center</span>
          <h1 className="mt-2 text-3xl font-semibold tracking-[-0.03em] sm:text-4xl">Platform Insights</h1>
          <p className="mt-2 max-w-xl text-subtle">How AgentForge is built, what each agent and model is doing, and what it has cost.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {health.data && <Badge tone={health.data.status === "ok" ? "ok" : "warn"} dot>API {health.data.status} · v{health.data.version}</Badge>}
          {health.data?.dependencies && Object.entries(health.data.dependencies).map(([k, v]) => (
            <Badge key={k} tone={v === "ok" ? "ok" : v === "degraded" ? "warn" : "bad"} dot>{k}</Badge>
          ))}
          {health.isError && <Badge tone="bad" dot>API offline</Badge>}
        </div>
      </header>

      <div className="panel overflow-hidden">
        <Tabs tabs={TABS} active={tab} label="Insights sections"
          onChange={(k) => setParams((q) => { q.set("tab", k); return q; }, { replace: true })} />
      </div>

      <div id={`panel-${tab}`} role="tabpanel" aria-labelledby={`tab-${tab}`}>
        {NEEDS_RUNS.includes(tab) && runs.isLoading ? <Loading label="Loading runs" />
          : NEEDS_RUNS.includes(tab) && runs.isError ? <div className="panel"><ErrorState error={runs.error} what="runs" onRetry={() => void runs.refetch()} /></div>
          : (
            <>
              {tab === "architecture" && <Architecture />}
              {tab === "agents" && <AgentsInsight runs={items} />}
              {tab === "models" && <ModelsInsight runs={items} />}
              {tab === "execution" && <ExecutionInsight runs={items} />}
              {tab === "usage" && <UsageInsight runs={items} />}
              {tab === "docs" && <Docs />}
            </>
          )}
      </div>
    </div>
  );
}
