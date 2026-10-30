import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Loader2 } from "lucide-react";

type Variant = "primary" | "brand" | "secondary" | "ghost" | "danger";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-fg text-white shadow-[0_10px_24px_-12px_rgb(17_19_24/0.55)] hover:bg-[#23262e] disabled:bg-subtle",
  brand: "brand-gradient text-white shadow-[0_10px_28px_-12px_rgb(118_185_0/0.75)] hover:brightness-105 disabled:brightness-90",
  secondary: "border border-line-strong bg-white/80 text-fg hover:border-nv-500/50 hover:bg-white",
  ghost: "text-muted hover:bg-ink-800 hover:text-fg",
  danger: "border border-rose-500/30 bg-rose-500/5 text-rose-700 hover:bg-rose-500/10",
};

export function Button({ variant = "secondary", size = "md", loading, icon, children, className = "", ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md" | "lg"; loading?: boolean; icon?: ReactNode }) {
  const sz = size === "sm" ? "h-7 px-2.5 text-xs gap-1.5" : size === "lg" ? "h-12 px-6 text-[15px] gap-2" : "h-9 px-3.5 text-sm gap-2";
  return (
    <button
      type="button"
      {...rest}
      disabled={rest.disabled || loading}
      aria-busy={loading || undefined}
      className={`inline-flex shrink-0 items-center justify-center rounded-full font-medium transition disabled:cursor-not-allowed disabled:opacity-60 ${sz} ${VARIANTS[variant]} ${className}`}
    >
      {loading ? <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}
