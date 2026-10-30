import type { CSSProperties, ReactNode } from "react";
import { useReveal } from "../../lib/useReveal";

/** Fades and lifts its children in the first time they scroll into view. */
export function Reveal({ children, className = "", delay = 0 }: { children: ReactNode; className?: string; delay?: number }) {
  const { props } = useReveal<HTMLDivElement>();
  const style: CSSProperties | undefined = delay ? { transitionDelay: `${delay}ms` } : undefined;
  return <div {...props} className={`reveal ${className}`} style={style}>{children}</div>;
}
