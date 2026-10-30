import { memo } from "react";
import { ArrowRight, GitCommitVertical, Rocket } from "lucide-react";
import type { AgentRole, Checkpoint, RunEvent, Stage } from "../../api/types";
import { agentMeta } from "../../lib/agents";
import { describeEvent } from "../../lib/events";
import { durationBetween, fmtDuration, fmtTime, shortModel } from "../../lib/format";
import { stageDisplay, TONE_CLASSES, type Tone } from "../../lib/status";
import { Badge } from "../ui/Badge";
import { lastEventFor, latestStage } from "./derive";

interface NodeProps {
  agent: AgentRole;
  stage?: Stage;
  events: RunEvent[];
  selected: boolean;
  onSelect: (id: string) => void;
}

const StageNode = memo(function StageNode({ agent, stage, events, selected, onSelect }: NodeProps) {
  const meta = agentMeta(agent);
  const Icon = meta.icon;
  const disp = stage ? stageDisplay(stage) : { label: "Not started", tone: "neutral" as Tone, active: false };
  const t = TONE_CLASSES[disp.tone];
  const model = stage && lastEventFor(events, (e) => e.stage_id === stage.id && (e.type === "model_call_ok" || e.type === "route_decision"))?.data.model_id;
  const latest = stage && lastEventFor(events, (e) => e.stage_id === stage.id && e.type !== "model_call_started");
  const latestText = latest ? describeEvent(latest).detail ?? describeEvent(latest).title : undefined;
  const dur = stage ? durationBetween(stage.started_at, stage.finished_at) : null;

  return (
    <button
      type="button"
      disabled={!stage}
      onClick={() => stage && onSelect(stage.id)}
      aria-pressed={selected}
      aria-label={`${meta.label}: ${disp.label}`}
      className={`panel group w-full min-w-0 rounded-xl p-3 text-left transition ${disp.active ? "live-ring" : ""} ${
        selected ? "border-nv-400/60 bg-ink-800" : "hover:border-line-strong"} disabled:cursor-default disabled:opacity-60`}
    >
      <div className="flex items-start gap-2.5">
        <span className={`grid size-8 shrink-0 place-items-center rounded-lg ${meta.accent}`}><Icon className="size-4" aria-hidden /></span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium" title={meta.label}>{meta.short}</div>
          <div className="mt-1"><Badge tone={disp.tone} dot pulse={disp.active}>{disp.label}</Badge></div>
        </div>
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-x-2 gap-y-1 text-[11px]">
        <dt className="text-subtle">Started</dt><dd className="num text-right text-muted">{fmtTime(stage?.started_at)}</dd>
        <dt className="text-subtle">Duration</dt><dd className="num text-right text-muted">{fmtDuration(dur)}</dd>
        <dt className="text-subtle">Attempt</dt>
        <dd className="num text-right text-muted">{stage ? `${stage.attempt}${stage.max_attempts ? ` / ${stage.max_attempts}` : ""}` : "—"}</dd>
        <dt className="text-subtle">Model</dt>
        <dd className="truncate text-right font-mono text-muted" title={typeof model === "string" ? model : undefined}>
          {typeof model === "string" ? shortModel(model) : "—"}
        </dd>
      </dl>
      {latestText && <p className={`mt-2 line-clamp-2 text-xs ${disp.tone === "bad" ? t.text : "text-subtle"}`}>{latestText}</p>}
    </button>
  );
});

function Connector({ active }: { active: boolean }) {
  return (
    <div aria-hidden className="hidden items-center justify-center xl:flex">
      <ArrowRight className={`size-4 ${active ? "text-amber-700" : "text-line-strong"}`} />
    </div>
  );
}

function StaticNode({ icon, title, tone, label, sub }: { icon: React.ReactNode; title: string; tone: Tone; label: string; sub: string }) {
  return (
    <div className="panel rounded-xl p-3">
      <div className="flex items-start gap-2.5">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-ink-700 text-muted">{icon}</span>
        <div className="min-w-0">
          <div className="text-sm font-medium">{title}</div>
          <div className="mt-1"><Badge tone={tone} dot>{label}</Badge></div>
        </div>
      </div>
      <p className="mt-3 text-[11px] text-subtle">{sub}</p>
    </div>
  );
}

export function StagePipeline({ stages, events, checkpoints, selectedStage, onSelect }: {
  stages: Stage[]; events: RunEvent[]; checkpoints: Checkpoint[]; selectedStage: string | null; onSelect: (id: string) => void;
}) {
  const planner = latestStage(stages, "planner");
  const backend = latestStage(stages, "backend");
  const frontend = latestStage(stages, "frontend");
  const reviewer = latestStage(stages, "reviewer");
  const deploy = latestStage(stages, "deploy");
  const parallel = backend?.status === "running" && frontend?.status === "running";
  const cp = checkpoints.at(-1);
  const node = (agent: AgentRole, s?: Stage) => (
    <StageNode agent={agent} stage={s} events={events} selected={!!s && s.id === selectedStage} onSelect={onSelect} />
  );

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-[1fr_auto_2fr_auto_1fr_auto_1fr_auto_1fr] xl:items-stretch">
      {node("planner", planner)}
      <Connector active={planner?.status === "passed"} />
      <div className="relative grid grid-cols-1 gap-2 sm:col-span-2 sm:grid-cols-2 xl:col-span-1">
        {parallel && (
          <span className="absolute -top-2.5 left-1/2 z-10 -translate-x-1/2 rounded-full border border-amber-400/30 bg-ink-900 px-2 text-[10px] font-medium uppercase tracking-wider text-amber-700">
            parallel
          </span>
        )}
        {node("backend", backend)}
        {node("frontend", frontend)}
      </div>
      <Connector active={backend?.status === "passed" && frontend?.status === "passed"} />
      {node("reviewer", reviewer)}
      <Connector active={reviewer?.status === "passed"} />
      <StaticNode icon={<GitCommitVertical className="size-4" />} title="Checkpoint"
        tone={cp ? "ok" : "neutral"} label={cp ? cp.git_tag : "None yet"}
        sub={cp ? `${cp.tests_passed} checks passed · ${fmtTime(cp.created_at)}` : "Created after an approved review"} />
      <Connector active={!!cp} />
      {deploy ? node("deploy", deploy)
        : <StaticNode icon={<Rocket className="size-4" />} title="Deploy (optional)" tone="neutral" label="Not requested"
            sub="Only runs when enabled and configured on the server" />}
    </div>
  );
}
