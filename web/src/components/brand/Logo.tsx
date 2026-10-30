import { useId } from "react";
import { Link } from "react-router";

/** AgentForge mark: three agent nodes joined into an "A" — a team converging on one product. */
export function LogoMark({ className = "size-8" }: { className?: string }) {
  const id = useId();
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <defs>
        <linearGradient id={`${id}-g`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#9be22d" />
          <stop offset="1" stopColor="#4f8000" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill="#111318" />
      <path d="M16 7.5 9 24M16 7.5 23 24M11.6 18h8.8" stroke={`url(#${id}-g)`} strokeWidth="2.6" strokeLinecap="round" fill="none" />
      <circle cx="16" cy="7.5" r="2.6" fill="#fff" />
      <circle cx="9" cy="24" r="2.6" fill="#fff" />
      <circle cx="23" cy="24" r="2.6" fill="#76b900" />
    </svg>
  );
}

export function Logo({ to = "/", className = "" }: { to?: string; className?: string }) {
  return (
    <Link to={to} className={`flex items-center gap-2.5 ${className}`} aria-label="AgentForge home">
      <LogoMark />
      <span className="text-[16px] font-semibold tracking-tight text-fg">AgentForge</span>
    </Link>
  );
}
