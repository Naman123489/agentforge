import { memo, useMemo, useState } from "react";
import { ChevronRight, Radio } from "lucide-react";
import type { RunEvent, Stage } from "../../api/types";
import type { StreamStatus } from "../../api/sse";
import { agentMeta } from "../../lib/agents";
import { describeEvent, type EventCategory } from "../../lib/events";
import { fmtTime, shortModel } from "../../lib/format";
import { TONE_CLASSES } from "../../lib/status";
import { Badge } from "../ui/Badge";
import { EmptyState } from "../ui/States";

const FILTERS: { key: "all" | EventCategory; label: string }[] = [
  { key: "all", label: "All" }, { key: "agents", label: "Agents" }, { key: "models", label: "Models" },
  { key: "tests", label: "Tests" }, { key: "system", label: "System" },
];

// Never render prompts, model reasoning or credentials even if a producer puts them in event data.
const REDACT = new Set(["messages", "prompt", "system_prompt", "reasoning", "reasoning_content", "content", "api_key",
  "authorization", "apikey", "secret", "password"]);
export function redact(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(redact);
  if (v && typeof v === "object")
    return Object.fromEntries(Object.entries(v as Record<string, unknown>).map(([k, x]) => [k, REDACT.has(k.toLowerCase()) ? "[hidden]" : redact(x)]));
  return v;
}

const EventRow = memo(function EventRow({ ev, stagesById }: { ev: RunEvent; stagesById: Map<string, Stage> }) {
  const [open, setOpen] = useState(false);
  const v = describeEvent(ev, stagesById);
  const meta = v.agent ? agentMeta(v.agent) : null;
  const Icon = meta?.icon;
  return (
    <li className="border-b border-line/50 last:border-0">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex w-full items-start gap-3 px-3 py-2 text-left hover:bg-ink-800/60">
        <span className="num mt-0.5 w-16 shrink-0 font-mono text-[11px] text-subtle">{fmtTime(ev.timestamp)}</span>
        <span aria-hidden className={`mt-1.5 size-1.5 shrink-0 rounded-full ${TONE_CLASSES[v.tone].dot}`} />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className={`text-sm ${v.tone === "bad" ? "text-rose-800" : "text-fg"}`}>{v.title}</span>
            {meta && Icon && (
              <span className="inline-flex items-center gap-1 text-[11px] text-subtle"><Icon className="size-3" aria-hidden />{meta.label}</span>
            )}
            {v.model && <span className="font-mono text-[11px] text-subtle" title={v.model}>{shortModel(v.model)}</span>}
          </span>
          {v.detail && <span className="mt-0.5 block text-xs text-muted">{v.detail}</span>}
        </span>
        <span className="hidden font-mono text-[10px] text-subtle sm:block">{ev.type}</span>
        <ChevronRight className={`mt-0.5 size-4 shrink-0 text-subtle transition ${open ? "rotate-90" : ""}`} aria-hidden />
      </button>
      {open && (
        <pre className="code-scroll mx-3 mb-2 max-h-72 overflow-auto rounded-lg border border-line bg-ink-950 p-3 text-[11.5px] text-muted">
          {JSON.stringify(redact({ id: ev.id, stage_id: ev.stage_id, data: ev.data }), null, 2)}
        </pre>
      )}
    </li>
  );
});

const LIMIT = 400;

export function EventStream({ events, stagesById, status }: {
  events: RunEvent[]; stagesById: Map<string, Stage>; status: StreamStatus | "idle";
}) {
  const [filter, setFilter] = useState<"all" | EventCategory>("all");
  const [showAll, setShowAll] = useState(false);
  const filtered = useMemo(() => {
    const list = filter === "all" ? events : events.filter((e) => describeEvent(e, stagesById).category === filter);
    return [...list].reverse(); // newest first
  }, [events, filter, stagesById]);
  const visible = showAll ? filtered : filtered.slice(0, LIMIT);

  const statusBadge = {
    idle: <Badge tone="neutral">not connected</Badge>,
    connecting: <Badge tone="neutral" dot pulse>connecting</Badge>,
    open: <Badge tone="live" dot pulse>live</Badge>,
    reconnecting: <Badge tone="warn" dot pulse>reconnecting</Badge>,
    closed: <Badge tone="neutral" dot>stream closed</Badge>,
    unauthorized: <Badge tone="bad" dot>unauthorized</Badge>,
    not_found: <Badge tone="bad" dot>run not found</Badge>,
    error: <Badge tone="bad" dot>stream error</Badge>,
  }[status];

  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
        <div role="group" aria-label="Filter events" className="flex flex-wrap gap-1">
          {FILTERS.map((f) => (
            <button key={f.key} type="button" aria-pressed={filter === f.key} onClick={() => setFilter(f.key)}
              className={`rounded-md px-2 py-1 text-xs transition ${filter === f.key ? "bg-nv-500/20 text-nv-800" : "text-subtle hover:text-fg"}`}>
              {f.label}
            </button>
          ))}
        </div>
        <span className="ml-auto flex items-center gap-2 text-[11px] text-subtle"><span className="num">{events.length} events</span>{statusBadge}</span>
      </div>
      {visible.length === 0 ? (
        <EmptyState icon={<Radio className="size-5" />} title={events.length ? "No events in this filter" : "No events yet"}>
          {events.length ? "Try another filter." : status === "open" ? "Connected. Waiting for the orchestrator to report activity." : "Events appear here as the backend streams them (GET /runs/{id}/events)."}
        </EmptyState>
      ) : (
        <ol aria-label="Run events, newest first" className="min-h-0 flex-1 overflow-y-auto">
          {visible.map((ev) => <EventRow key={ev.id} ev={ev} stagesById={stagesById} />)}
          {!showAll && filtered.length > LIMIT && (
            <li className="p-3 text-center">
              <button className="text-xs text-nv-700 hover:underline" onClick={() => setShowAll(true)}>Show {filtered.length - LIMIT} older events</button>
            </li>
          )}
        </ol>
      )}
    </div>
  );
}
