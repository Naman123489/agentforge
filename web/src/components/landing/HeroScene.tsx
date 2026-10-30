// The landing hero: a living picture of the AgentForge pipeline. An idea reaches the Planner, the Frontend and
// Backend agents build into one workspace in parallel, the Reviewer verifies it, and a working app comes out —
// with the Model Router serving every agent. This is an illustration, not live data (it says so to screen readers).
import { useEffect, useState, type CSSProperties } from "react";
import { CheckCircle2, Compass, LayoutPanelTop, Lightbulb, Server, ShieldCheck, Split, type LucideIcon } from "lucide-react";
import { usePrefersReducedMotion } from "../../lib/useReveal";

const W = 720;
const H = 560;
const pos = (x: number, y: number): CSSProperties => ({ left: `${(x / W) * 100}%`, top: `${(y / H) * 100}%` });

// Phase 0 planning, 1 building, 2 verifying, 3 shipped.
const PHASES = [
  { label: "Planning the architecture", sub: "API contract · OpenAPI 3.1", pct: 22 },
  { label: "Frontend + backend in parallel", sub: "React UI · FastAPI service", pct: 58 },
  { label: "Reviewer running checks", sub: "tsc · vite build · pytest · served together", pct: 86 },
  { label: "Approved · checkpoint created", sub: "Preview ready", pct: 100 },
];

const FLOWS = [
  { id: "idea", d: "M120 64 C 150 78, 160 92, 168 104", phase: 0 },
  { id: "plan", d: "M214 146 C 250 160, 262 172, 282 176", phase: 0 },
  { id: "fe", d: "M142 316 C 190 312, 206 300, 236 296", phase: 1 },
  { id: "be", d: "M578 316 C 530 312, 514 300, 484 296", phase: 1 },
  { id: "rev", d: "M360 390 C 360 420, 360 436, 360 462", phase: 2 },
  { id: "ship", d: "M420 494 C 470 494, 500 494, 532 494", phase: 3 },
];
const ROUTER_LINKS = ["M520 120 C 420 96, 300 100, 226 118", "M600 150 C 618 200, 620 250, 612 292", "M560 152 C 470 210, 300 240, 150 296"];

function AgentCard({ x, y, icon: Icon, name, status, active, tint, delay }: {
  x: number; y: number; icon: LucideIcon; name: string; status: string; active: boolean; tint: string; delay: number;
}) {
  return (
    <div className="absolute -translate-x-1/2 -translate-y-1/2" style={pos(x, y)}>
      <div className="motion-safe:animate-float" style={{ animationDelay: `${delay}s` }}>
        <div className={`glass glass-float flex items-center gap-2 rounded-2xl py-1.5 pr-3 pl-1.5 transition duration-500 sm:gap-2.5 sm:py-2 sm:pl-2 ${
          active ? "scale-105 ring-2 ring-nv-500/50" : ""}`}>
          <span className={`grid size-6 place-items-center rounded-xl sm:size-8 ${tint}`}><Icon className="size-3.5 sm:size-4" aria-hidden /></span>
          <span className="min-w-0">
            <span className="block text-[10px] font-semibold whitespace-nowrap text-fg sm:text-[13px]">{name}</span>
            <span className={`hidden text-[11px] whitespace-nowrap sm:block ${active ? "text-nv-700" : "text-subtle"}`}>
              {active && <span className="mr-1 inline-block size-1.5 rounded-full bg-nv-500 align-middle motion-safe:animate-pulse-dot" />}
              {status}
            </span>
          </span>
        </div>
      </div>
    </div>
  );
}

function Snippet({ x, y, children, delay }: { x: number; y: number; children: React.ReactNode; delay: number }) {
  return (
    <div className="absolute hidden -translate-x-1/2 -translate-y-1/2 md:block" style={pos(x, y)}>
      <div className="motion-safe:animate-float" style={{ animationDelay: `${delay}s` }}>
        <div className="rounded-xl bg-[#111318] px-3 py-2 font-mono text-[11px] leading-5 text-white/85 shadow-[0_18px_40px_-18px_rgb(17_19_24/0.6)]">
          {children}
        </div>
      </div>
    </div>
  );
}

function Workspace({ phase }: { phase: number }) {
  const p = PHASES[phase];
  const built = phase >= 1;
  return (
    <div className="absolute" style={{ ...pos(236, 176), width: `${(248 / W) * 100}%`, height: `${(214 / H) * 100}%` }}>
      <div className="glass glass-float flex h-full flex-col overflow-hidden rounded-2xl">
        <div className="flex items-center gap-1.5 border-b border-line/70 px-3 py-1.5 sm:py-2">
          <span className="size-1.5 rounded-full bg-rose-400 sm:size-2" /><span className="size-1.5 rounded-full bg-amber-400 sm:size-2" />
          <span className="size-1.5 rounded-full bg-nv-500 sm:size-2" />
          <span className="ml-1.5 truncate font-mono text-[8px] text-subtle sm:text-[10px]">workspace / your-app</span>
        </div>
        <div className="flex min-h-0 flex-1 gap-2 p-2 sm:gap-2.5 sm:p-3">
          <div className="hidden w-1/4 flex-col gap-1.5 sm:flex">
            {[70, 90, 55, 80].map((w, i) => (
              <span key={i} className={`h-1.5 rounded-full transition-all duration-700 ${built ? "bg-ink-700" : "bg-ink-800"}`} style={{ width: `${w}%` }} />
            ))}
          </div>
          <div className="grid flex-1 grid-cols-2 gap-1.5 sm:gap-2">
            {[0, 1, 2, 3].map((i) => (
              <span key={i} className={`rounded-lg border transition-all duration-700 ${built
                ? "border-line bg-white shadow-sm" : "border-dashed border-line-strong bg-transparent"}`}
                style={{ transitionDelay: `${i * 120}ms` }}>
                <span className={`m-1.5 block h-1 rounded-full sm:m-2 sm:h-1.5 ${phase >= 2 ? "bg-nv-500/60" : "bg-ink-700"} motion-safe:animate-shimmer`}
                  style={{ width: `${50 + i * 10}%`, animationDelay: `${i * 0.3}s` }} />
              </span>
            ))}
          </div>
        </div>
        <div className="border-t border-line/70 px-2.5 py-1.5 sm:px-3 sm:py-2" aria-live="off">
          <div className="flex items-center gap-1.5 text-[9px] font-medium text-fg sm:text-[12px]">
            {phase === 3 ? <CheckCircle2 className="size-3 text-nv-600 sm:size-3.5" aria-hidden />
              : <span className="inline-block size-1.5 rounded-full bg-amber-500 motion-safe:animate-pulse-dot" />}
            <span className="truncate">{p.label}</span>
          </div>
          <div className="mt-1 hidden truncate text-[10px] text-subtle sm:block">{p.sub}</div>
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-ink-800">
            <div className="brand-gradient h-full rounded-full transition-all duration-1000" style={{ width: `${p.pct}%` }} />
          </div>
        </div>
      </div>
    </div>
  );
}

export function HeroScene() {
  const reduced = usePrefersReducedMotion();
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (reduced) return;
    const t = setInterval(() => setTick((n) => n + 1), 2600);
    return () => clearInterval(t);
  }, [reduced]);
  const phase = reduced ? 3 : tick % PHASES.length;

  return (
    <div className="relative mx-auto aspect-[720/560] w-full max-w-[760px] select-none" role="img"
      aria-label="Illustration: your idea goes to the Planner, the Frontend and Backend agents build in parallel, the Reviewer verifies, and a working app is produced. The Model Router serves every agent.">
      <div aria-hidden className="grid-fade absolute inset-[-6%] rounded-[3rem]" />
      <div aria-hidden className="absolute top-1/2 left-1/2 size-[46%] -translate-x-1/2 -translate-y-1/2 rounded-full bg-nv-400/20 blur-3xl" />
      <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 size-full" aria-hidden>
        <defs>
          <linearGradient id="hero-flow" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#76b900" stopOpacity="0.25" />
            <stop offset="1" stopColor="#4f8000" stopOpacity="0.9" />
          </linearGradient>
        </defs>
        {ROUTER_LINKS.map((d, i) => (
          <path key={i} d={d} fill="none" stroke="#8b93a7" strokeOpacity="0.45" strokeWidth="1.2" strokeDasharray="3 6"
            className="motion-safe:animate-dash" />
        ))}
        {FLOWS.map((f) => (
          <g key={f.id}>
            <path d={f.d} fill="none" stroke="#d3dae4" strokeWidth="2.5" strokeLinecap="round" />
            <path d={f.d} fill="none" stroke="url(#hero-flow)" strokeWidth="2.5" strokeLinecap="round"
              strokeDasharray="6 8" className="motion-safe:animate-dash"
              style={{ opacity: phase >= f.phase ? 1 : 0.15, transition: "opacity .6s" }} />
            <circle r="4" fill="#76b900" className="motion-particle">
              <animateMotion dur="2.6s" repeatCount="indefinite" path={f.d} begin={`${f.phase * 0.4}s`} />
            </circle>
          </g>
        ))}
        {ROUTER_LINKS.map((d, i) => (
          <circle key={i} r="2.5" fill="#6366f1" className="motion-particle">
            <animateMotion dur="3.4s" repeatCount="indefinite" path={d} keyPoints="1;0" keyTimes="0;1" calcMode="linear" begin={`${i * 0.9}s`} />
          </circle>
        ))}
      </svg>

      <Workspace phase={phase} />

      <div className="absolute -translate-x-1/2 -translate-y-1/2" style={pos(92, 50)}>
        <div className="flex items-center gap-1.5 rounded-full bg-[#111318] px-2.5 py-1 text-[10px] font-medium text-white shadow-lg sm:px-3 sm:py-1.5 sm:text-[12px]">
          <Lightbulb className="size-3 text-nv-300 sm:size-3.5" aria-hidden /> Your idea
        </div>
      </div>
      <AgentCard x={180} y={124} icon={Compass} name="Planner" status="writing the contract" active={phase === 0} tint="bg-violet-500/10 text-violet-700" delay={0} />
      <AgentCard x={588} y={124} icon={Split} name="Model Router" status="picks a model per call" active={false} tint="bg-indigo-500/10 text-indigo-700" delay={1.2} />
      <AgentCard x={104} y={318} icon={LayoutPanelTop} name="Frontend" status="React + TypeScript" active={phase === 1} tint="bg-sky-500/10 text-sky-700" delay={0.6} />
      <AgentCard x={616} y={318} icon={Server} name="Backend" status="FastAPI + SQLite" active={phase === 1} tint="bg-amber-400/15 text-amber-700" delay={1.8} />
      <AgentCard x={360} y={494} icon={ShieldCheck} name="Reviewer" status="re-runs every check" active={phase === 2} tint="bg-emerald-500/10 text-emerald-700" delay={0.9} />
      <div className="absolute -translate-x-1/2 -translate-y-1/2" style={pos(612, 494)}>
        <div className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-semibold transition duration-500 sm:px-3 sm:py-1.5 sm:text-[12px] ${
          phase === 3 ? "border-nv-500/50 bg-nv-50 text-nv-800 shadow-[0_10px_30px_-10px_rgb(118_185_0/0.7)]" : "border-line bg-white/80 text-subtle"}`}>
          <CheckCircle2 className="size-3 sm:size-3.5" aria-hidden /> Working app
        </div>
      </div>

      <Snippet x={96} y={418} delay={0.4}>
        <span className="text-sky-300">&lt;RecipeList</span> <span className="text-white/60">items</span>=<span className="text-nv-300">{"{data}"}</span> <span className="text-sky-300">/&gt;</span>
      </Snippet>
      <Snippet x={624} y={412} delay={1.4}>
        <span className="text-amber-300">POST</span> /api/recipes <span className="text-nv-300">201</span>
      </Snippet>
    </div>
  );
}
