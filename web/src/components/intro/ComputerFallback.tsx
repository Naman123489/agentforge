// Lightweight non-WebGL computer (CSS only) for weak devices, WebGL failures and reduced motion.
// The whole illustration is one large button, so it is an easy tap target and keyboard-operable.
import { forwardRef } from "react";

interface Props {
  activated: boolean;
  onEnter: () => void;
  hint: string;
}

export const ComputerFallback = forwardRef<HTMLButtonElement, Props>(function ComputerFallback({ activated, onEnter, hint }, ref) {
  return (
    <button ref={ref} type="button" onClick={onEnter} aria-label="Enter site" aria-disabled={activated || undefined}
      className={`intro-fallback ${activated ? "intro-fallback-on" : ""}`} data-testid="intro-fallback">
      <span className="intro-fb-rig">
        <span className="intro-fb-monitor">
          <span className="intro-fb-screen" />
        </span>
        <span className="intro-fb-neck" />
        <span className="intro-fb-base" />
        <span className="intro-fb-keyboard" />
      </span>
      <span className="intro-hint">{hint}</span>
    </button>
  );
});
