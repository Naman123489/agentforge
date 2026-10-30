import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";
import { Activity, BookOpen, FolderKanban, Gauge, Menu, Plus, Settings, X, type LucideIcon } from "lucide-react";
import { DEMO_MODE } from "../../api/config";
import { useCurrentRun } from "../../state/currentRun";
import { Logo } from "../brand/Logo";
import { ConnectionStatus } from "./TopBar";

interface NavItem { to: string; label: string; icon: LucideIcon; match: (path: string, search: string) => boolean }

function useNav(): NavItem[] {
  const current = useCurrentRun();
  return [
    { to: "/runs", label: "Projects", icon: FolderKanban, match: (p) => p === "/runs" },
    { to: current ? `/runs/${encodeURIComponent(current)}` : "/observer", label: "Live observer", icon: Activity,
      match: (p) => p.startsWith("/runs/") || p === "/observer" },
    { to: "/insights", label: "Platform insights", icon: Gauge, match: (p, s) => p === "/insights" && !s.includes("tab=docs") },
    { to: "/insights?tab=docs", label: "Documentation", icon: BookOpen, match: (p, s) => p === "/insights" && s.includes("tab=docs") },
    { to: "/settings", label: "Settings", icon: Settings, match: (p) => p === "/settings" },
  ];
}

function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { pathname, search } = useLocation();
  const nav = useNav();
  return (
    <div className="flex h-full flex-col gap-6 p-4">
      <Logo className="px-1.5 pt-1" />
      <NavLink to="/new" onClick={onNavigate}
        className={({ isActive }) => `flex h-10 items-center gap-2 rounded-full px-4 text-sm font-medium transition ${isActive
          ? "bg-fg text-white shadow-[0_10px_24px_-12px_rgb(17_19_24/0.55)]"
          : "border border-line-strong bg-white/80 text-fg hover:border-nv-500/50"}`}>
        <Plus className="size-4" aria-hidden /> New project
      </NavLink>
      <nav aria-label="Workspace" className="flex flex-col gap-0.5">
        {nav.map((n) => {
          const on = n.match(pathname, search);
          const Icon = n.icon;
          return (
            <NavLink key={n.label} to={n.to} onClick={onNavigate} aria-current={on ? "page" : undefined}
              className={`flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition ${on
                ? "bg-white text-fg shadow-[0_0_0_1px_rgb(17_19_24/0.06),0_6px_16px_-10px_rgb(16_24_40/0.3)]"
                : "text-muted hover:bg-white/60 hover:text-fg"}`}>
              <Icon className={`size-4 shrink-0 ${on ? "text-nv-700" : ""}`} aria-hidden />
              {n.label}
            </NavLink>
          );
        })}
      </nav>
      <div className="mt-auto">
        <ConnectionStatus />
      </div>
    </div>
  );
}

/** Workspace layout: a narrow glass sidebar on desktop, a top bar + drawer on mobile. */
export function AppShell() {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex h-full flex-col">
      {DEMO_MODE && (
        <div role="note" className="flex items-center justify-center gap-2 border-b border-amber-400/30 bg-amber-50 px-4 py-1.5 text-center text-xs text-amber-900">
          <strong className="font-semibold">DEMO MODE</strong>
          — simulated data from an in-browser mock of the AgentForge API (partly replaying a recorded real Backend Agent run). Not connected to a backend.
        </div>
      )}
      <header className="glass flex h-14 shrink-0 items-center gap-3 px-3 lg:hidden">
        <button onClick={() => setOpen(true)} aria-label="Open navigation" className="rounded-lg p-1.5 text-muted hover:bg-ink-800 hover:text-fg">
          <Menu className="size-5" />
        </button>
        <Logo />
      </header>
      <div className="flex min-h-0 flex-1">
        <aside className="hidden w-60 shrink-0 border-r border-line/80 bg-white/40 backdrop-blur-xl lg:block">
          <Sidebar />
        </aside>
        {open && (
          <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
            <button aria-label="Close navigation" className="absolute inset-0 bg-[#111318]/25 backdrop-blur-sm" onClick={() => setOpen(false)} />
            <aside className="glass relative h-full w-72 rounded-r-3xl">
              <button aria-label="Close navigation" onClick={() => setOpen(false)} className="absolute right-3 top-4 rounded-lg p-1 text-subtle hover:text-fg">
                <X className="size-5" />
              </button>
              <Sidebar onNavigate={() => setOpen(false)} />
            </aside>
          </div>
        )}
        <main id="main" className="min-w-0 flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
