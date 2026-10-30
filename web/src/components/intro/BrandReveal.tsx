// Brand reveal: an original "forged A" lettermark (three knife-cut shards: left leg, sliced right leg, low crossbar)
// that assembles from the centre, followed by the AGENTFORGE wordmark sliding out from behind it under a mask.
import { forwardRef } from "react";

/** Monochrome A-inspired lettermark. Pieces are separated by a constant 5-unit cut so it reads as forged shards. */
export function ForgeMark({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 120 120" className={className} aria-hidden fill="currentColor">
      <polygon className="intro-shard intro-shard-l" points="8,112 48,8 72,8 36,112" />
      <polygon className="intro-shard intro-shard-r" points="74.6,14.8 112,112 84,112 61.4,53.2" />
      <polygon className="intro-shard intro-shard-c" points="52.1,80 66.7,80 72.8,96 46.5,96" />
    </svg>
  );
}

interface Props {
  leaving: boolean;
  /** Fires once the wordmark's entrance (full or reduced-motion) has finished. */
  onRevealed: () => void;
}

export const BrandReveal = forwardRef<HTMLDivElement, Props>(function BrandReveal({ leaving, onRevealed }, markRef) {
  return (
    <div className={`intro-brand ${leaving ? "intro-brand-leaving" : ""}`} aria-hidden>
      <div ref={markRef} className="intro-mark">
        <ForgeMark className="size-full" />
      </div>
      <div className="intro-word-clip">
        <div className="intro-word-mask">
          <span className="intro-word" onAnimationEnd={(e) => {
            if (e.animationName === "intro-word-in" || e.animationName === "intro-fade-in") onRevealed();
          }}>AGENTFORGE</span>
        </div>
      </div>
    </div>
  );
});
