import { useEffect, useState } from "react";
import { Link } from "react-router";
import { Menu, X } from "lucide-react";
import { Logo } from "../brand/Logo";

export const SECTIONS = [
  { href: "#product", label: "Product" },
  { href: "#how", label: "How it works" },
  { href: "#features", label: "Features" },
  { href: "#technology", label: "Technology" },
];

export function LandingNav() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 12);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);

  return (
    <header className="fixed inset-x-0 top-3 z-50 px-3 sm:top-4 sm:px-6">
      <div className={`glass mx-auto flex h-14 max-w-6xl items-center gap-4 rounded-full pr-2 pl-4 transition-shadow duration-300 ${scrolled ? "glass-float" : ""}`}>
        <Logo />
        <nav aria-label="Sections" className="mx-auto hidden items-center gap-1 md:flex">
          {SECTIONS.map((s) => (
            <a key={s.href} href={s.href} className="rounded-full px-3.5 py-1.5 text-sm text-muted transition hover:bg-white hover:text-fg">{s.label}</a>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-1.5 md:ml-0">
          <Link to="/settings" className="hidden rounded-full px-3.5 py-2 text-sm font-medium text-muted transition hover:text-fg sm:block"
            title="Connect with your AgentForge platform token">Sign in</Link>
          <Link to="/new" className="rounded-full bg-fg px-4 py-2 text-sm font-medium text-white shadow-[0_10px_24px_-12px_rgb(17_19_24/0.6)] transition hover:bg-[#23262e]">
            Get started
          </Link>
          <button className="rounded-full p-2 text-muted hover:bg-white md:hidden" aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
        </div>
      </div>
      {open && (
        <nav aria-label="Sections" className="glass glass-float mx-auto mt-2 flex max-w-6xl flex-col rounded-3xl p-2 md:hidden">
          {SECTIONS.map((s) => (
            <a key={s.href} href={s.href} onClick={() => setOpen(false)} className="rounded-2xl px-4 py-3 text-[15px] text-fg hover:bg-white">{s.label}</a>
          ))}
          <Link to="/settings" className="rounded-2xl px-4 py-3 text-[15px] text-muted hover:bg-white">Sign in</Link>
        </nav>
      )}
    </header>
  );
}
