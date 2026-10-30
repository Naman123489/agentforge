import { CheckCircle2, Compass, Hammer, MessageSquareText, ShieldCheck, type LucideIcon } from "lucide-react";
import { useReveal } from "../../lib/useReveal";

const STEPS: { icon: LucideIcon; title: string; text: string; visual: React.ReactNode }[] = [
  { icon: MessageSquareText, title: "Describe your idea", text: "Plain language. No spec required.",
    visual: <div className="space-y-1.5"><span className="block h-1.5 w-[88%] rounded-full bg-ink-700" /><span className="block h-1.5 w-[64%] rounded-full bg-ink-700" /><span className="block h-1.5 w-[40%] rounded-full bg-nv-400/70" /></div> },
  { icon: Compass, title: "Agents plan the architecture", text: "A validated API contract both sides build to.",
    visual: <div className="font-mono text-[10px] leading-4 text-subtle"><div><span className="text-violet-700">paths:</span></div><div className="pl-2">/api/items: <span className="text-nv-700">get</span>, <span className="text-nv-700">post</span></div><div className="pl-2">/api/login: <span className="text-nv-700">post</span></div></div> },
  { icon: Hammer, title: "Frontend + backend build it", text: "In parallel, against the same contract.",
    visual: <div className="grid grid-cols-2 gap-1.5"><span className="h-7 rounded-md border border-sky-400/40 bg-sky-500/10" /><span className="h-7 rounded-md border border-amber-400/40 bg-amber-400/10" /></div> },
  { icon: ShieldCheck, title: "Reviewers test and verify", text: "Every check is executed, not self-reported.",
    visual: <ul className="space-y-1 text-[10px] text-muted">{["tsc --noEmit", "vite build", "pytest"].map((c) => <li key={c} className="flex items-center gap-1.5"><CheckCircle2 className="size-3 text-nv-600" />{c}</li>)}</ul> },
  { icon: CheckCircle2, title: "Get a working application", text: "Checkpointed, previewable, downloadable.",
    visual: <div className="flex h-9 items-center justify-center gap-1.5 rounded-lg bg-nv-50 text-[11px] font-semibold text-nv-800"><CheckCircle2 className="size-3.5" />Preview ready</div> },
];

export function ProcessSection() {
  const { props, visible } = useReveal<HTMLDivElement>(0.2);
  return (
    <section id="how" className="mx-auto max-w-6xl scroll-mt-28 px-4 py-24 sm:px-6 sm:py-32">
      <div className="mx-auto max-w-2xl text-center">
        <span className="eyebrow">How it works</span>
        <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-5xl">From a sentence to a running app</h2>
      </div>
      <div {...props} className="relative mt-16">
        {/* Connector: fills left to right (top to bottom on mobile) as the section comes into view. */}
        <div aria-hidden className="absolute top-7 right-[10%] left-[10%] hidden h-0.5 overflow-hidden rounded-full bg-line lg:block">
          <div className="brand-gradient h-full origin-left transition-transform duration-[1800ms] ease-out" style={{ transform: `scaleX(${visible ? 1 : 0})` }} />
        </div>
        <div aria-hidden className="absolute top-0 bottom-0 left-7 w-0.5 overflow-hidden rounded-full bg-line lg:hidden">
          <div className="brand-gradient w-full origin-top transition-transform duration-[1800ms] ease-out h-full" style={{ transform: `scaleY(${visible ? 1 : 0})` }} />
        </div>
        <ol className="relative grid gap-6 lg:grid-cols-5 lg:gap-4">
          {STEPS.map((s, i) => {
            const Icon = s.icon;
            return (
              <li key={s.title} className="reveal flex gap-4 lg:flex-col lg:items-center lg:text-center" data-visible={visible ? "true" : "false"}
                style={{ transitionDelay: `${150 + i * 180}ms` }}>
                <span className={`relative z-10 grid size-14 shrink-0 place-items-center rounded-2xl border bg-white shadow-sm ${i === 4 ? "border-nv-500/50 text-nv-700" : "border-line text-fg"}`}>
                  <Icon className="size-5" aria-hidden />
                  <span className="num absolute -top-2 -right-2 grid size-5 place-items-center rounded-full bg-fg text-[10px] font-semibold text-white">{i + 1}</span>
                </span>
                <div className="min-w-0 flex-1 lg:w-full">
                  <h3 className="font-semibold tracking-tight">{s.title}</h3>
                  <p className="mt-1 text-sm text-subtle">{s.text}</p>
                  <div className="glass mt-3 rounded-2xl p-3 text-left lg:mt-4">{s.visual}</div>
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
