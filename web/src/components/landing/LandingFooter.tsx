import { Link } from "react-router";
import { ArrowRight } from "lucide-react";
import { LogoMark } from "../brand/Logo";
import { useIntro } from "../intro/IntroExperience";
import { Reveal } from "./Reveal";
import { SECTIONS } from "./LandingNav";

export function FinalCta() {
  return (
    <section className="px-4 py-20 sm:px-6 sm:py-28">
      <Reveal>
        <div className="relative mx-auto max-w-6xl overflow-hidden rounded-[2.5rem] bg-[#111318] px-6 py-16 text-center sm:px-12 sm:py-24">
          <div aria-hidden className="absolute -top-40 left-1/2 size-[36rem] -translate-x-1/2 rounded-full bg-nv-500/25 blur-[100px]" />
          <div aria-hidden className="absolute inset-0 bg-[linear-gradient(rgb(255_255_255/0.05)_1px,transparent_1px),linear-gradient(90deg,rgb(255_255_255/0.05)_1px,transparent_1px)] bg-[size:44px_44px] [mask-image:radial-gradient(ellipse_at_center,#000_20%,transparent_70%)]" />
          <h2 className="relative mx-auto max-w-3xl text-3xl font-semibold tracking-tight text-white sm:text-5xl">
            Stop imagining your next application. <span className="text-nv-400">Start building it.</span>
          </h2>
          <Link to="/new" className="relative mt-10 inline-flex h-12 items-center gap-2 rounded-full bg-white px-7 text-[15px] font-semibold text-fg shadow-[0_18px_40px_-14px_rgb(118_185_0/0.8)] transition hover:-translate-y-0.5">
            Build with AgentForge <ArrowRight className="size-4" aria-hidden />
          </Link>
        </div>
      </Reveal>
    </section>
  );
}

export function LandingFooter() {
  const intro = useIntro();
  return (
    <footer className="border-t border-line/80 px-4 py-12 sm:px-6">
      <div className="mx-auto flex max-w-6xl flex-col gap-10 sm:flex-row sm:justify-between">
        <div className="max-w-xs">
          <div className="flex items-center gap-2.5"><LogoMark /><span className="font-semibold">AgentForge</span></div>
          <p className="mt-3 text-sm text-subtle">From an idea to a working application. Powered by AI agents.</p>
          <p className="mt-3 text-xs text-subtle">Built for the NVIDIA × Nebius Global AI Hackathon.</p>
        </div>
        <div className="grid grid-cols-2 gap-10 text-sm">
          <div>
            <h3 className="font-semibold">Product</h3>
            <ul className="mt-3 space-y-2 text-subtle">
              {SECTIONS.map((s) => <li key={s.href}><a href={s.href} className="hover:text-fg">{s.label}</a></li>)}
            </ul>
          </div>
          <div>
            <h3 className="font-semibold">Resources</h3>
            <ul className="mt-3 space-y-2 text-subtle">
              <li><Link to="/insights?tab=docs" className="hover:text-fg">Documentation</Link></li>
              <li><Link to="/insights" className="hover:text-fg">Platform insights</Link></li>
              <li><Link to="/insights?tab=architecture" className="hover:text-fg">About</Link></li>
              <li><Link to="/new" className="hover:text-fg">Start a project</Link></li>
              {intro && <li><button type="button" onClick={intro.replay} className="hover:text-fg">Replay intro</button></li>}
            </ul>
          </div>
        </div>
      </div>
    </footer>
  );
}
