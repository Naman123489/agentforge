import type { ReactNode } from "react";
import { Reveal } from "./Reveal";

// Small original glyph-illustrations, one per agent (stroke = currentColor so each card tints its own).
const ART: Record<string, ReactNode> = {
  decision: (
    <svg viewBox="0 0 120 64" className="h-16 w-full" aria-hidden fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <rect x="48" y="20" width="24" height="24" rx="4" transform="rotate(45 60 32)" />
      <path d="M22 32h22M76 32h14M90 32l10-14M90 32l10 14" />
      <circle cx="16" cy="32" r="5" /><circle cx="104" cy="16" r="4" /><circle cx="104" cy="48" r="4" fill="currentColor" />
    </svg>
  ),
  planner: (
    <svg viewBox="0 0 120 64" className="h-16 w-full" aria-hidden fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <rect x="48" y="6" width="24" height="14" rx="4" />
      <path d="M60 20v8M30 28h60M30 28v8M60 28v8M90 28v8" />
      <rect x="18" y="38" width="24" height="14" rx="4" /><rect x="48" y="38" width="24" height="14" rx="4" fill="currentColor" fillOpacity=".15" /><rect x="78" y="38" width="24" height="14" rx="4" />
    </svg>
  ),
  frontend: (
    <svg viewBox="0 0 120 64" className="h-16 w-full" aria-hidden fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <rect x="14" y="6" width="92" height="52" rx="7" />
      <path d="M14 18h92" /><circle cx="22" cy="12" r="1.5" fill="currentColor" /><circle cx="28" cy="12" r="1.5" fill="currentColor" />
      <rect x="22" y="26" width="34" height="24" rx="4" fill="currentColor" fillOpacity=".15" /><path d="M64 28h34M64 36h26M64 44h30" />
    </svg>
  ),
  backend: (
    <svg viewBox="0 0 120 64" className="h-16 w-full" aria-hidden fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <rect x="20" y="8" width="80" height="14" rx="4" /><rect x="20" y="25" width="80" height="14" rx="4" /><rect x="20" y="42" width="80" height="14" rx="4" />
      <circle cx="30" cy="15" r="2" fill="currentColor" /><circle cx="30" cy="32" r="2" fill="currentColor" /><circle cx="30" cy="49" r="2" fill="currentColor" />
      <path d="M44 15h20M44 32h34M44 49h14" />
    </svg>
  ),
  reviewer: (
    <svg viewBox="0 0 120 64" className="h-16 w-full" aria-hidden fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M60 6 84 14v16c0 14-10 23-24 28-14-5-24-14-24-28V14z" fill="currentColor" fillOpacity=".12" />
      <path d="m50 31 7 7 14-14" />
      <path d="M96 20h12M96 32h12M96 44h12M12 20h12M12 32h12M12 44h12" strokeOpacity=".5" />
    </svg>
  ),
  router: (
    <svg viewBox="0 0 120 64" className="h-16 w-full" aria-hidden fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
      <circle cx="22" cy="32" r="7" fill="currentColor" fillOpacity=".15" />
      <path d="M29 32h18c10 0 14-20 26-20h16M47 32h42M47 32c10 0 14 20 26 20h16" />
      <rect x="90" y="5" width="20" height="14" rx="4" /><rect x="90" y="25" width="20" height="14" rx="4" fill="currentColor" fillOpacity=".2" /><rect x="90" y="45" width="20" height="14" rx="4" />
    </svg>
  ),
};

const AGENTS = [
  { key: "decision", name: "Decision Model", text: "Decides which agent takes each task, and when work is ready to move on.", tint: "text-fuchsia-700", wash: "from-fuchsia-500/10" },
  { key: "planner", name: "Planner / Architect", text: "Turns your idea into stages and a validated API contract.", tint: "text-violet-700", wash: "from-violet-500/10" },
  { key: "frontend", name: "Frontend Engine", text: "Builds a typed React UI that can only call the contracted API.", tint: "text-sky-700", wash: "from-sky-500/10" },
  { key: "backend", name: "Backend Engine", text: "Implements the API with a database and its own test suite.", tint: "text-amber-700", wash: "from-amber-400/15" },
  { key: "reviewer", name: "Reviewer / Integrator", text: "Re-runs every check itself and approves only on evidence.", tint: "text-emerald-700", wash: "from-emerald-500/10" },
  { key: "router", name: "Model Router", text: "Picks the right model for every call, within your budget.", tint: "text-indigo-700", wash: "from-indigo-500/10" },
];

export function AgentsSection() {
  return (
    <section id="features" className="relative scroll-mt-28 py-24 sm:py-32">
      <div className="mx-auto max-w-6xl px-4 sm:px-6">
        <div className="max-w-2xl">
          <span className="eyebrow">Meet the agents</span>
          <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-5xl">A specialist for every part of the job</h2>
        </div>
        <div className="mt-14 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {AGENTS.map((a, i) => (
            <Reveal key={a.key} delay={(i % 3) * 110}>
              <article className="glass group relative h-full overflow-hidden rounded-3xl p-6 transition duration-300 hover:-translate-y-1 hover:shadow-[var(--shadow-float)]">
                <div aria-hidden className={`absolute inset-0 bg-gradient-to-br ${a.wash} to-transparent opacity-0 transition-opacity duration-300 group-hover:opacity-100`} />
                <div className={`relative ${a.tint} transition-transform duration-500 group-hover:scale-[1.04]`}>{ART[a.key]}</div>
                <h3 className="relative mt-5 text-lg font-semibold tracking-tight">{a.name}</h3>
                <p className="relative mt-1.5 text-[15px] leading-relaxed text-subtle">{a.text}</p>
              </article>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
