import { CheckCircle2, Circle, GitCommitVertical, Loader2 } from "lucide-react";
import { useReveal } from "../../lib/useReveal";

// A stylised glimpse of the Live Observer: stages light up in order as the section scrolls into view.
const STAGES = ["Planner", "Backend", "Frontend", "Reviewer", "Checkpoint"];
const LOG = [
  ["route_decision", "planner → premium model · high complexity"],
  ["stage_finished", "Planner · contract validated"],
  ["stage_started", "Backend ∥ Frontend · building in parallel"],
  ["stage_progress", "Frontend · typecheck (tsc) passed"],
  ["review_finished", "Reviewer · 14/14 checks passed · approve"],
  ["checkpoint_created", "cp-3 · preview ready"],
];

export function ObserveSection() {
  const { props, visible } = useReveal<HTMLDivElement>(0.25);
  return (
    <section id="product" className="scroll-mt-28 px-4 py-20 sm:px-6 sm:py-28">
      <div className="mx-auto grid max-w-6xl grid-cols-1 items-center gap-12 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div>
          <span className="eyebrow">Product</span>
          <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-5xl">Watch your team work</h2>
          <p className="mt-5 max-w-md text-[17px] leading-relaxed text-subtle">
            Every stage, model call, check and checkpoint streams to your screen as it happens. A stage turns green only when its checks really pass.
          </p>
        </div>
        <div {...props} className="glass glass-float rounded-[2rem] p-4 sm:p-6">
          <ol className="grid grid-cols-5 gap-1.5 sm:gap-3">
            {STAGES.map((s, i) => {
              const done = visible;
              return (
                <li key={s} className="reveal" data-visible={visible ? "true" : "false"} style={{ transitionDelay: `${i * 260}ms` }}>
                  <div className={`flex flex-col items-center gap-1.5 rounded-2xl border p-2 text-center transition-colors duration-700 sm:p-3 ${
                    done ? "border-nv-500/40 bg-nv-50" : "border-line bg-white"}`} style={{ transitionDelay: `${300 + i * 260}ms` }}>
                    {i === 4 ? <GitCommitVertical className={`size-4 ${done ? "text-nv-700" : "text-subtle"}`} aria-hidden />
                      : done ? <CheckCircle2 className="size-4 text-nv-700" aria-hidden /> : <Circle className="size-4 text-subtle" aria-hidden />}
                    <span className="max-w-full truncate text-[9px] font-medium sm:text-xs">{s}</span>
                  </div>
                </li>
              );
            })}
          </ol>
          <div className="mt-4 rounded-2xl bg-[#111318] p-4 font-mono text-[11px] leading-6 sm:text-xs">
            {LOG.map(([type, text], i) => (
              <div key={type} className="reveal flex gap-3" data-visible={visible ? "true" : "false"} style={{ transitionDelay: `${500 + i * 280}ms` }}>
                <span className="shrink-0 text-nv-400">{type}</span>
                <span className="truncate text-white/75">{text}</span>
              </div>
            ))}
            <div className="mt-1 flex items-center gap-2 text-white/40"><Loader2 className="size-3 motion-safe:animate-spin" aria-hidden />streaming</div>
          </div>
        </div>
      </div>
    </section>
  );
}
