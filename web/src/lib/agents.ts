import { Compass, LayoutPanelTop, Rocket, Server, ShieldCheck, Workflow, type LucideIcon } from "lucide-react";
import type { AgentRole } from "../api/types";

export type AgentKey = AgentRole | "orchestrator";

export interface AgentMeta {
  key: AgentKey;
  label: string;
  short: string;
  role: string;
  icon: LucideIcon;
  /** accent for icon tile and subtle borders */
  accent: string;
  ring: string;
}

export const AGENTS: Record<AgentKey, AgentMeta> = {
  orchestrator: { key: "orchestrator", label: "Orchestrator", short: "Orchestrator", role: "State machine, retries, budgets",
    icon: Workflow, accent: "text-nv-700 bg-nv-500/12", ring: "border-nv-500/40" },
  planner: { key: "planner", label: "Planner / Architect", short: "Planner", role: "Plan, stages and API contract",
    icon: Compass, accent: "text-violet-700 bg-violet-500/10", ring: "border-violet-400/40" },
  backend: { key: "backend", label: "Backend Agent", short: "Backend", role: "FastAPI + SQLite against the contract",
    icon: Server, accent: "text-sky-700 bg-sky-500/10", ring: "border-sky-400/40" },
  frontend: { key: "frontend", label: "Frontend Agent", short: "Frontend", role: "React UI against the contract",
    icon: LayoutPanelTop, accent: "text-indigo-700 bg-indigo-500/10", ring: "border-indigo-400/40" },
  reviewer: { key: "reviewer", label: "Reviewer / Integrator", short: "Reviewer", role: "Executes checks; approves on evidence",
    icon: ShieldCheck, accent: "text-emerald-700 bg-emerald-500/10", ring: "border-emerald-400/40" },
  deploy: { key: "deploy", label: "Deploy Agent", short: "Deploy", role: "Optional deploy + health check",
    icon: Rocket, accent: "text-amber-700 bg-amber-400/12", ring: "border-amber-400/30" },
};

export const AGENT_ORDER: AgentKey[] = ["orchestrator", "planner", "backend", "frontend", "reviewer", "deploy"];

export const agentMeta = (k: string | null | undefined): AgentMeta =>
  AGENTS[(k ?? "orchestrator") as AgentKey] ?? AGENTS.orchestrator;
