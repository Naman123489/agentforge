import { Link } from "react-router";
import { ChevronRight, Inbox } from "lucide-react";
import type { RunDetail } from "../../api/types";
import { fmtRelative, fmtTokens, fmtUsd } from "../../lib/format";
import { RUN_STATUS } from "../../lib/status";
import { Badge } from "../ui/Badge";
import { EmptyState } from "../ui/States";

export function RunList({ runs, empty }: { runs: RunDetail[]; empty?: string }) {
  if (runs.length === 0)
    return <EmptyState icon={<Inbox className="size-5" />} title="No runs yet">{empty ?? <>Start one from <Link to="/new" className="font-medium text-nv-700 hover:underline">New project</Link>.</>}</EmptyState>;
  return (
    <ul aria-label="Runs">
      {runs.map((r) => {
        const st = RUN_STATUS[r.status] ?? { label: r.status, tone: "neutral" as const, live: false };
        return (
          <li key={r.id} className="border-b border-line/60 last:border-0">
            <Link to={`/runs/${encodeURIComponent(r.id)}`} className="flex items-center gap-3 px-4 py-3 hover:bg-ink-800/60">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium">{r.project_name || "Untitled project"}</span>
                  <Badge tone={st.tone} dot pulse={st.live}>{st.label}</Badge>
                </div>
                <p className="truncate text-xs text-subtle">{r.prompt}</p>
              </div>
              <div className="num hidden text-right text-xs text-subtle sm:block">
                <div>{fmtRelative(r.created_at)}</div>
                <div>{fmtTokens(r.usage.total_tokens)} tok · {fmtUsd(r.usage.confirmed_cost_usd ?? r.usage.estimated_cost_usd)}{r.usage.confirmed_cost_usd == null ? " est." : ""}</div>
              </div>
              <ChevronRight className="size-4 text-subtle" aria-hidden />
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
