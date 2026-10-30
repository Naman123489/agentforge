import { Link } from "react-router";
import { ArrowDown, ArrowRight } from "lucide-react";
import { AgentsSection } from "../components/landing/AgentsSection";
import { HeroScene } from "../components/landing/HeroScene";
import { FinalCta, LandingFooter } from "../components/landing/LandingFooter";
import { LandingNav } from "../components/landing/LandingNav";
import { ObserveSection } from "../components/landing/ObserveSection";
import { ProcessSection } from "../components/landing/ProcessSection";
import { RoutingSection } from "../components/landing/RoutingSection";

export default function LandingPage() {
  return (
    <div className="overflow-x-clip">
      <LandingNav />
      <main>
        <section className="relative px-4 pt-28 pb-12 sm:px-6 sm:pt-36 lg:pb-20">
          <div className="mx-auto grid max-w-7xl items-center gap-10 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] lg:gap-6">
            <div className="text-center lg:text-left">
              <span className="glass inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium text-muted">
                <span className="size-1.5 rounded-full bg-nv-500" /> Multi-agent software engineering
              </span>
              <h1 className="ink-text mt-6 text-[2.6rem] leading-[1.04] font-semibold tracking-[-0.035em] sm:text-6xl lg:text-[4.1rem]">
                Your idea. A team of AI engineers. <span className="brand-text">One working product.</span>
              </h1>
              <p className="mx-auto mt-6 max-w-xl text-[17px] leading-relaxed text-subtle sm:text-lg lg:mx-0">
                AgentForge turns natural-language ideas into real applications through coordinated AI agents that plan, build, test, and improve your software.
              </p>
              <div className="mt-9 flex flex-wrap justify-center gap-3 lg:justify-start">
                <Link to="/new" className="inline-flex h-12 items-center gap-2 rounded-full bg-fg px-6 text-[15px] font-medium text-white shadow-[0_14px_30px_-14px_rgb(17_19_24/0.7)] transition hover:-translate-y-0.5 hover:bg-[#23262e]">
                  Start Building <ArrowRight className="size-4" aria-hidden />
                </Link>
                <a href="#how" className="glass inline-flex h-12 items-center gap-2 rounded-full px-6 text-[15px] font-medium text-fg transition hover:-translate-y-0.5">
                  Explore How It Works <ArrowDown className="size-4" aria-hidden />
                </a>
              </div>
            </div>
            <HeroScene />
          </div>
        </section>
        <ObserveSection />
        <ProcessSection />
        <AgentsSection />
        <RoutingSection />
        <FinalCta />
      </main>
      <LandingFooter />
    </div>
  );
}
