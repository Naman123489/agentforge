import { Gauge, RotateCcw, Split, TrendingUp } from "lucide-react";
import type { ModelStatus } from "../../api/types";
import { useModels } from "../../state/queries";
import { Reveal } from "./Reveal";

type Slot = ModelStatus["slot"];

// What each slot is for, and an example of the work routed there (engine/router/policy.py).
const ROUTES: { slot: Slot; letter: string; title: string; task: string; level: string; tone: string }[] = [
  { slot: "premium", letter: "A", title: "Premium reasoning", task: "Plan the architecture", level: "High complexity", tone: "text-violet-700 bg-violet-500/10 border-violet-400/40" },
  { slot: "coding", letter: "B", title: "Main coder", task: "Write a React page", level: "Medium complexity", tone: "text-nv-700 bg-nv-500/10 border-nv-500/40" },
  { slot: "economy", letter: "C", title: "Economy", task: "Triage a failing log", level: "Low complexity", tone: "text-sky-700 bg-sky-500/10 border-sky-400/40" },
];

const FACTS = [
  { icon: RotateCcw, title: "Fallback", text: "If a model fails or is rate-limited, the call moves to the next healthy slot (A→B→C, B→A→C, C→B)." },
  { icon: TrendingUp, title: "Escalation", text: "Repeated failures on a task escalate it to the premium model." },
  { icon: Gauge, title: "Cost-aware", text: "Token, cost and premium-spend limits are enforced on the server for every run." },
];

function Wire({ delay }: { delay: number }) {
  return (
    <span aria-hidden className="relative hidden h-px flex-1 bg-[repeating-linear-gradient(90deg,#c5ccd8_0_4px,transparent_4px_9px)] sm:block">
      <span className="motion-particle absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-nv-500 shadow-[0_0_10px_2px_rgb(118_185_0/0.45)] motion-safe:animate-travel"
        style={{ animationDelay: `${delay}s` }} />
    </span>
  );
}

export function RoutingSection() {
  const models = useModels();
  // Real model ids when the API answers (GET /models); otherwise the slots are shown without inventing names.
  const bySlot = new Map((models.data?.models ?? []).map((m) => [m.slot, m]));

  return (
    <section id="technology" className="scroll-mt-28 py-24 sm:py-32">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <div className="grid items-end gap-6 lg:grid-cols-2">
          <div>
            <span className="eyebrow">Model intelligence</span>
            <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-5xl">The right model for every call</h2>
          </div>
          <p className="max-w-md text-[17px] leading-relaxed text-subtle lg:justify-self-end">
            One router sits between every agent and the models. It weighs each task's difficulty against your budget.
          </p>
        </div>

        <Reveal className="mt-14">
          <div className="glass relative overflow-hidden rounded-[2rem] p-5 sm:p-8">
            <div aria-hidden className="grid-fade absolute inset-0 opacity-70" />
            <div className="relative grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] sm:items-center sm:gap-0">
              <ul className="space-y-3 sm:space-y-6">
                {ROUTES.map((r, i) => (
                  <li key={r.slot} className="flex items-center gap-3">
                    <div className="min-w-0 rounded-2xl border border-line bg-white px-4 py-3 shadow-sm sm:max-w-[15rem]">
                      <div className="truncate text-sm font-medium">{r.task}</div>
                      <div className="text-xs text-subtle">{r.level}</div>
                    </div>
                    <Wire delay={i * 0.5} />
                  </li>
                ))}
              </ul>
              <div className="mx-auto flex flex-col items-center px-2 py-2 sm:px-6">
                <div className="relative grid size-24 place-items-center rounded-[1.75rem] bg-fg text-white shadow-[0_24px_50px_-20px_rgb(17_19_24/0.7)] sm:size-28">
                  <span aria-hidden className="absolute inset-0 rounded-[1.75rem] ring-2 ring-nv-500/50 motion-safe:animate-pulse-dot" />
                  <Split className="size-8 text-nv-400" aria-hidden />
                </div>
                <span className="mt-3 text-sm font-semibold">Model Router</span>
                <span className="text-xs text-subtle">decide · call · fall back</span>
              </div>
              <ul className="space-y-3 sm:space-y-6">
                {ROUTES.map((r, i) => {
                  const m = bySlot.get(r.slot);
                  return (
                    <li key={r.slot} className="flex items-center gap-3">
                      <Wire delay={0.9 + i * 0.5} />
                      <div className={`min-w-0 flex-1 rounded-2xl border px-4 py-3 sm:max-w-[16rem] ${r.tone}`}>
                        <div className="flex items-center gap-2 text-sm font-semibold">
                          <span className="grid size-5 place-items-center rounded-md bg-white/80 text-[11px]">{r.letter}</span>{r.title}
                        </div>
                        <div className="mt-0.5 truncate font-mono text-xs text-fg/70" title={m?.model_id}>
                          {m ? m.model_id : models.isLoading ? "…" : "configured on the server"}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
        </Reveal>

        <div className="mt-6 grid gap-4 md:grid-cols-3">
          {FACTS.map((f, i) => (
            <Reveal key={f.title} delay={i * 120}>
              <div className="glass h-full rounded-3xl p-5">
                <f.icon className="size-5 text-nv-700" aria-hidden />
                <h3 className="mt-3 font-semibold">{f.title}</h3>
                <p className="mt-1 text-sm leading-relaxed text-subtle">{f.text}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
