// Hand-off from the intro to the homepage: the homepage is mounted (inert, behind the black stage) during the
// brand reveal, then revealed progressively while the lettermark flies to the nav logo's position.
import type { ReactNode } from "react";

export type IntroPhase = "idle" | "activating" | "brand" | "handoff" | "done";

export function IntroTransition({ phase, children }: { phase: IntroPhase; children: ReactNode }) {
  if (phase === "idle" || phase === "activating") return null; // never show the homepage before the click
  const live = phase === "handoff" || phase === "done";
  return (
    <div className="intro-home" data-intro-reveal={phase === "handoff" ? "true" : undefined}
      inert={!live} aria-hidden={live ? undefined : true}>
      {children}
    </div>
  );
}

/** FLIP the brand mark onto the nav logo (`[aria-label="AgentForge home"] svg`) inside `scope`. */
export function flyMarkToNav(mark: HTMLElement | null, scope: ParentNode | null): void {
  if (!mark || !scope) return;
  const target = scope.querySelector<SVGElement>('[aria-label="AgentForge home"] svg');
  if (!target) return;
  const from = mark.getBoundingClientRect();
  const to = target.getBoundingClientRect();
  if (!from.width || !to.width) return;
  const dx = to.left + to.width / 2 - (from.left + from.width / 2);
  const dy = to.top + to.height / 2 - (from.top + from.height / 2);
  const s = to.width / from.width;
  mark.style.transform = `translate(${dx}px, ${dy}px) scale(${s})`;
}
