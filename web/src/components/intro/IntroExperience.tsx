// Cinematic intro that plays once per visit before the homepage:
// idle 3D computer -> (click / Enter / Space) screen glow + camera move -> brand reveal -> hand-off into the homepage.
// Falls back to a CSS computer when WebGL is unavailable, the device is weak, or reduced motion is requested.
import { createContext, lazy, Suspense, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { usePrefersReducedMotion } from "../../lib/useReveal";
import { BrandReveal } from "./BrandReveal";
import { ComputerFallback } from "./ComputerFallback";
import { canUse3D, clearIntroSeen, hasSeenIntro, introForcedByUrl, liteForcedByUrl, markIntroSeen, TIMING } from "./introState";
import { flyMarkToNav, IntroTransition, type IntroPhase } from "./IntroTransition";
import "./intro.css";

const loadComputer = () => import("./ThreeDComputer");
const ThreeDComputer = lazy(loadComputer);

const IntroContext = createContext<{ replay: () => void } | null>(null);
/** `replay` restarts the intro; null outside an IntroExperience (e.g. tests rendering the page directly). */
export const useIntro = () => useContext(IntroContext);

export function IntroExperience({ children }: { children: ReactNode }) {
  const reduced = usePrefersReducedMotion();
  const [phase, setPhase] = useState<IntroPhase>(() => (hasSeenIntro() && !introForcedByUrl() ? "done" : "idle"));
  const [webgl, setWebgl] = useState(() => phase !== "done" && !liteForcedByUrl() && canUse3D());
  const [sceneReady, setSceneReady] = useState(false);
  const [stageGone, setStageGone] = useState(false);
  const [revealed, setRevealed] = useState(false); // wordmark entrance finished
  const [cleared, setCleared] = useState(false); // black stage faded out during the hand-off
  const use3D = webgl && !reduced;

  const rootRef = useRef<HTMLDivElement>(null);
  const markRef = useRef<HTMLDivElement>(null);
  const targetRef = useRef<HTMLElement>(null);
  const triggered = useRef(false);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  const advance = useCallback((from: IntroPhase, to: IntroPhase) => {
    if (phaseRef.current === from) setPhase(to);
  }, []);

  const enter = useCallback(() => {
    if (triggered.current || phaseRef.current !== "idle") return; // exactly once
    triggered.current = true;
    setPhase("activating");
  }, []);

  const replay = useCallback(() => {
    clearIntroSeen();
    triggered.current = false;
    setSceneReady(false);
    setStageGone(false);
    setRevealed(false);
    setCleared(false);
    window.scrollTo(0, 0);
    setPhase("idle");
  }, []);

  // Preload the 3D chunk as soon as we know it will be needed.
  useEffect(() => {
    if (phase === "idle" && use3D) void loadComputer();
  }, [phase, use3D]);

  // Lock page scroll while the stage covers the screen.
  useEffect(() => {
    if (phase === "done") return;
    const html = document.documentElement;
    const prev = html.style.overflow;
    html.style.overflow = "hidden";
    return () => { html.style.overflow = prev; };
  }, [phase]);

  // Focus the stage so Enter / Space work straight away.
  useEffect(() => {
    if (phase === "idle") targetRef.current?.focus({ preventScroll: true });
  }, [phase, use3D, sceneReady]);

  // Phase timers. The 3D path reports the end of activation itself; the timer is a safety net (e.g. hidden tab).
  useEffect(() => {
    let t: number | undefined;
    if (phase === "activating") {
      const ms = use3D ? TIMING.activate3d + 900 : reduced ? TIMING.reduced.activate : TIMING.activateFallback;
      t = window.setTimeout(() => advance("activating", "brand"), ms);
    } else if (phase === "brand") {
      t = revealed
        ? window.setTimeout(() => advance("brand", "handoff"), reduced ? TIMING.reduced.hold : TIMING.hold)
        : window.setTimeout(() => advance("brand", "handoff"), TIMING.brandMax);
    } else if (phase === "handoff") {
      const finish = () => { markIntroSeen(); advance("handoff", "done"); };
      t = cleared
        ? window.setTimeout(finish, reduced ? TIMING.reduced.handoffTail : TIMING.handoffTail)
        : window.setTimeout(finish, TIMING.handoffMax);
    }
    return () => window.clearTimeout(t);
  }, [phase, use3D, reduced, revealed, cleared, advance]);

  useEffect(() => {
    if (phase === "brand") window.scrollTo(0, 0);
    if (phase === "handoff" && !reduced) flyMarkToNav(markRef.current, rootRef.current);
  }, [phase, reduced]);

  // Keep the computer on screen briefly after activation so it can cross-fade into the brand reveal.
  useEffect(() => {
    if (phase !== "brand") return;
    const t = window.setTimeout(() => setStageGone(true), TIMING.stageFade);
    return () => window.clearTimeout(t);
  }, [phase]);

  const ctx = useMemo(() => ({ replay }), [replay]);
  const coarse = typeof window !== "undefined" && !!window.matchMedia?.("(pointer: coarse)").matches;
  const hint = coarse ? "Tap to enter" : "Click to enter";
  const activated = phase !== "idle";
  const showStage = (phase === "idle" || phase === "activating" || phase === "brand") && !stageGone;

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); enter(); }
  };

  return (
    <IntroContext.Provider value={ctx}>
      <div ref={rootRef} data-intro-phase={phase} data-intro-reduced={reduced ? "true" : undefined}>
        <IntroTransition phase={phase}>{children}</IntroTransition>
        {phase !== "done" && (
          <div className="intro-overlay" data-testid="intro-overlay">
            <div className="intro-bg" onTransitionEnd={(e) => {
              if (e.target === e.currentTarget && e.propertyName === "opacity" && phaseRef.current === "handoff") setCleared(true);
            }} />
            {showStage && (
              <div className={`intro-stage ${phase === "brand" ? "intro-stage-out" : ""}`}>
                {use3D ? (
                  <div ref={(el) => { targetRef.current = el; }} role="button" tabIndex={0} aria-label="Enter site"
                    aria-disabled={activated || undefined} onKeyDown={onKey} className="intro-3d" data-testid="intro-3d">
                    <Suspense fallback={null}>
                      <ThreeDComputer activated={activated} onEnter={enter} onReady={() => setSceneReady(true)}
                        onActivated={() => advance("activating", "brand")} onError={() => setWebgl(false)} />
                    </Suspense>
                    <span className={`intro-hint intro-hint-3d ${sceneReady && !activated ? "intro-hint-on" : ""}`}>{hint}</span>
                  </div>
                ) : (
                  <ComputerFallback ref={(el) => { targetRef.current = el; }} activated={activated} onEnter={enter} hint={hint} />
                )}
              </div>
            )}
            {(phase === "brand" || phase === "handoff") && <BrandReveal ref={markRef} leaving={phase === "handoff"} onRevealed={() => setRevealed(true)} />}
          </div>
        )}
      </div>
    </IntroContext.Provider>
  );
}
