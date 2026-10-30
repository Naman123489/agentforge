import { useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { ArrowUp, ChevronDown, KanbanSquare, LayoutDashboard, ShoppingBag, Sparkles, type LucideIcon } from "lucide-react";
import { ApiError } from "../../api/client";
import type { CreateRunRequest, RunBudget, RunOptions } from "../../api/types";
import { newIdempotencyKey, useCreateRun } from "../../state/mutations";
import { setCurrentRun } from "../../state/currentRun";
import { RUN_STATUS } from "../../lib/status";
import { ErrorState } from "../ui/States";
import { useToast } from "../ui/Toast";

export const EXAMPLES: { label: string; text: string; icon: LucideIcon; project: string; prompt: string }[] = [
  { label: "Build a SaaS dashboard", text: "Sign-in, metrics overview and team settings.", icon: LayoutDashboard, project: "saas-dashboard",
    prompt: "Build a SaaS analytics dashboard: user sign-up and login, a dashboard page with key metrics and a chart of events per day, an events list with filters, and a team settings page to invite members by email. Include a REST API and tests." },
  { label: "Create an e-commerce platform", text: "Catalogue, cart and order history.", icon: ShoppingBag, project: "shop",
    prompt: "Build an e-commerce store: a product catalogue with categories and search, product detail pages, a shopping cart, checkout that creates an order (no real payments), and an order history page for logged-in users. Include an admin API to add products, and tests." },
  { label: "Build an AI productivity app", text: "Notes and tasks with smart summaries.", icon: Sparkles, project: "focus",
    prompt: "Build a productivity app: users sign up, write notes and to-do items, tag them, and search them. Each note has a 'summary' field the API fills with its first two sentences. Include a today view of open tasks, a REST API and tests." },
  { label: "Create a project management tool", text: "Projects, boards and task status.", icon: KanbanSquare, project: "projects",
    prompt: "Build a project management tool: users create projects, add tasks with title, assignee, due date and status (todo, doing, done), view them as a board grouped by status, and move tasks between columns. Include login, a REST API and tests." },
];

// Effort = the run's token pool (backend and frontend build in parallel from one pool; a 14-endpoint app used 194k
// of 200k in run_cac7f352b025) and wall time. Quality first (user decision 2026-10-30): $10 per run, $5 of it on
// the premium model; still hard limits enforced by the server.
export const EFFORTS = {
  low: { label: "Low", max_tokens: 1_000_000, max_wall_time_seconds: 1800 },
  medium: { label: "Medium", max_tokens: 2_000_000, max_wall_time_seconds: 3600 },
  high: { label: "High", max_tokens: 4_000_000, max_wall_time_seconds: 7200 },
} as const;
export type Effort = keyof typeof EFFORTS;
const DEFAULT_EFFORT: Effort = "high";
const budgetFor = (e: Effort): Required<RunBudget> => ({ max_tokens: EFFORTS[e].max_tokens, max_cost_usd: 10,
  max_premium_cost_usd: 5, max_wall_time_seconds: EFFORTS[e].max_wall_time_seconds, max_retries_per_stage: 3 });
const DEFAULT_OPTIONS: Required<RunOptions> = { enable_deploy_agent: false, enable_preview: true, run_e2e_tests: true, visibility: "private" };

export function validatePrompt(prompt: string, project: string): string | null {
  const p = prompt.trim();
  if (p.length < 5) return "Describe what to build (at least 5 characters).";
  if (p.length > 20_000) return "The request is too long (max 20,000 characters).";
  if (project.length > 100) return "Project name must be at most 100 characters.";
  return null;
}

/** The project-creation prompt: one large input, example cards that fill it, and POST /runs on submit. */
export function PromptComposer() {
  const [prompt, setPrompt] = useState("");
  const [project, setProject] = useState("");
  const [touched, setTouched] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [effort, setEffort] = useState<Effort>(DEFAULT_EFFORT);
  // One key per draft: retrying after a network error reuses it, so the server never creates the run twice.
  const draftKey = useRef(newIdempotencyKey());
  const textarea = useRef<HTMLTextAreaElement>(null);
  const create = useCreateRun();
  const navigate = useNavigate();
  const toast = useToast();
  const ids = { prompt: useId(), err: useId() };
  const error = validatePrompt(prompt, project);
  const unauthorized = create.error instanceof ApiError && create.error.kind === "unauthorized";

  // Grow the input with its content, up to a cap.
  useEffect(() => {
    const el = textarea.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 320)}px`;
  }, [prompt]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (error || create.isPending) return;
    const body: CreateRunRequest = { prompt: prompt.trim(), budget: budgetFor(effort), options: DEFAULT_OPTIONS,
      ...(project.trim() ? { project_name: project.trim() } : {}) };
    create.mutate({ body, key: draftKey.current }, {
      onSuccess: (run) => {
        draftKey.current = newIdempotencyKey();
        setCurrentRun(run.id);
        toast("ok", `Run ${run.id} accepted — status: ${RUN_STATUS[run.status]?.label ?? run.status}. Building happens asynchronously.`);
        navigate(`/runs/${encodeURIComponent(run.id)}`);
      },
    });
  };

  const applyExample = (ex: (typeof EXAMPLES)[number]) => {
    setPrompt(ex.prompt);
    setProject(ex.project);
    setTouched(false);
    textarea.current?.focus();
  };

  return (
    <div className="w-full">
      <form onSubmit={submit} aria-label="Start a new AgentForge project" noValidate
        className="glass glass-float rounded-[1.75rem] p-2 transition focus-within:ring-4 focus-within:ring-nv-500/15">
        <label htmlFor={ids.prompt} className="sr-only">Describe the application</label>
        <textarea
          id={ids.prompt}
          ref={textarea}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(e); }}
          rows={3}
          maxLength={20_000}
          aria-invalid={touched && !!error}
          aria-describedby={touched && error ? ids.err : undefined}
          placeholder="Describe the application you want to build..."
          className="block min-h-28 w-full resize-none bg-transparent px-4 pt-4 pb-2 text-[16px] leading-relaxed text-fg placeholder:text-subtle focus:outline-none sm:text-[17px]"
        />
        <div className="flex items-center gap-2 px-2 pb-1">
          <button type="button" onClick={() => setAdvanced((a) => !a)} aria-expanded={advanced}
            className="flex h-9 items-center gap-1 rounded-full px-3 text-xs font-medium text-subtle transition hover:bg-ink-800 hover:text-fg">
            Options <ChevronDown className={`size-3.5 transition ${advanced ? "rotate-180" : ""}`} aria-hidden />
          </button>
          <span className="ml-auto hidden text-[11px] text-subtle sm:inline">Ctrl + Enter</span>
          <button type="submit" aria-label={create.isPending ? "Submitting" : "Start build"} disabled={create.isPending}
            className="flex h-10 items-center gap-2 rounded-full bg-fg pr-4 pl-3 text-sm font-medium text-white shadow-[0_10px_24px_-12px_rgb(17_19_24/0.6)] transition hover:bg-[#23262e] disabled:opacity-70">
            <span className="grid size-6 place-items-center rounded-full bg-nv-500">
              <ArrowUp className={`size-3.5 ${create.isPending ? "motion-safe:animate-bounce" : ""}`} aria-hidden />
            </span>
            {create.isPending ? "Submitting…" : "Build"}
          </button>
        </div>
        {advanced && (
          <div role="radiogroup" aria-label="Effort" className="mx-2 mt-1 mb-2 flex gap-1 rounded-2xl border border-line bg-white/70 p-1">
            {(Object.keys(EFFORTS) as Effort[]).map((k) => (
              <button key={k} type="button" role="radio" aria-checked={effort === k} onClick={() => setEffort(k)}
                className={`h-9 flex-1 rounded-xl text-sm font-medium transition ${effort === k
                  ? "bg-fg text-white shadow-sm" : "text-muted hover:bg-ink-800 hover:text-fg"}`}>
                {EFFORTS[k].label}
              </button>
            ))}
          </div>
        )}
      </form>

      {touched && error && <p id={ids.err} role="alert" className="mt-3 px-2 text-sm text-amber-800">{error}</p>}
      {create.isError && (
        <div className="mt-3">
          <ErrorState compact error={create.error} what="the new project" />
          {unauthorized && <Link to="/settings" className="mt-2 inline-block px-1 text-sm font-medium text-nv-700 hover:underline">Open Settings to add your platform token →</Link>}
        </div>
      )}

      <ul className="mt-6 grid gap-3 sm:grid-cols-2" aria-label="Example ideas">
        {EXAMPLES.map((ex) => {
          const Icon = ex.icon;
          return (
            <li key={ex.label}>
              <button type="button" onClick={() => applyExample(ex)} aria-label={ex.label}
                className="glass group flex h-full w-full items-start gap-3 rounded-2xl p-4 text-left transition hover:-translate-y-0.5 hover:shadow-[var(--shadow-float)]">
                <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-ink-800 text-muted transition group-hover:bg-nv-500/12 group-hover:text-nv-700">
                  <Icon className="size-4" aria-hidden />
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-fg">{ex.label}</span>
                  <span className="mt-0.5 block text-[13px] text-subtle">{ex.text}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

