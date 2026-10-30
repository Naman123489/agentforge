import type { ReactNode } from "react";
import { TONE_CLASSES, type Tone } from "../../lib/status";

export function Badge({ tone = "neutral", children, dot, pulse, className = "", title }: {
  tone?: Tone; children: ReactNode; dot?: boolean; pulse?: boolean; className?: string; title?: string;
}) {
  const t = TONE_CLASSES[tone];
  return (
    <span title={title} className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-5 whitespace-nowrap ${t.text} ${t.bg} ${t.border} ${className}`}>
      {dot && <span aria-hidden className={`size-1.5 rounded-full ${t.dot} ${pulse ? "motion-safe:animate-pulse-dot" : ""}`} />}
      {children}
    </span>
  );
}

export function StatusDot({ tone, pulse, label }: { tone: Tone; pulse?: boolean; label: string }) {
  return (
    <span className="inline-flex items-center" role="img" aria-label={label}>
      <span className={`size-2 rounded-full ${TONE_CLASSES[tone].dot} ${pulse ? "motion-safe:animate-pulse-dot" : ""}`} />
    </span>
  );
}
