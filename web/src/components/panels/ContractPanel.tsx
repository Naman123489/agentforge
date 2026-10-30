import { FileJson2 } from "lucide-react";
import { fmtTime } from "../../lib/format";
import { useContract } from "../../state/queries";
import { Badge } from "../ui/Badge";
import { EmptyState, ErrorState, Loading } from "../ui/States";
import { CodeViewer } from "./CodeViewer";

/** The generated application's per-run contract (GET /runs/{id}/contracts) — not the platform API. */
export function ContractPanel({ runId }: { runId: string }) {
  const c = useContract(runId);
  if (c.isLoading) return <Loading label="Loading contract" />;
  if (c.isError) return <ErrorState error={c.error} what="the API contract" onRetry={() => void c.refetch()} />;
  if (!c.data) return <EmptyState icon={<FileJson2 className="size-5" />} title="No contract yet">The Planner writes the app's OpenAPI contract before any code.</EmptyState>;
  const a = c.data;
  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2 text-xs">
        <span className="font-mono text-muted">{a.path}</span>
        <Badge tone="neutral">OpenAPI {a.openapi_version}</Badge>
        <Badge tone={a.valid ? "ok" : "bad"} dot>{a.valid ? "valid" : "invalid"}</Badge>
        {a.updated_at && <span className="num ml-auto text-subtle">updated {fmtTime(a.updated_at)}</span>}
      </div>
      {!a.valid && a.validation_errors && a.validation_errors.length > 0 && (
        <ul className="border-b border-line bg-rose-500/5 px-5 py-2 text-xs text-rose-800">
          {a.validation_errors.map((e, i) => <li key={i} className="list-disc">{e}</li>)}
        </ul>
      )}
      {a.content ? <CodeViewer path={a.path.endsWith(".json") ? "c.json" : "c.yaml"} content={a.content} />
        : <p className="p-4 text-sm text-subtle">The server returned no contract content.</p>}
    </div>
  );
}
