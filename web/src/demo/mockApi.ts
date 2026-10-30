// DEMO MODE ONLY (npm run dev:demo). An in-browser stand-in for the AgentForge Control Plane API so the
// dashboard can be shown before the orchestrator/API server exists. Every screen shows a DEMO banner.
// Backend-agent events, files, notes, checks and usage are REPLAYED from a real recorded live run;
// planner/frontend/reviewer activity is synthetic and tagged {"demo": "synthetic"} in event data.
import { setTransport } from "../api/client";
import type {
  Checkpoint, CheckResult, ContractArtifact, FileTreeResponse, ModelStatus, PreviewInfo, ReviewReport, RunDetail, RunEvent,
  RunEventType, RunStatus, Stage, UsageSummary,
} from "../api/types";
import fixturesJson from "./fixtures.json";
import modelStatuses from "./model_statuses.json";

interface Fixtures {
  backendEvents: RunEvent[];
  backendResult: { checks: CheckResult[]; files_changed: string[]; models_used: string[]; notes: string[]; tokens: number; cost_usd: number; iterations: number };
  files: Record<string, string>;
  contract: string;
}
const fx = fixturesJson as unknown as Fixtures;
const MODELS = modelStatuses as ModelStatus[];
const modelFor = (slot: "premium" | "coding" | "economy") => MODELS.find((m) => m.slot === slot)!.model_id;
const PRICES: Record<string, [number, number]> = {}; // per 1M tokens, derived from recorded calls when possible
const RECORDED = "recorded from a live Backend Agent build on Nebius Token Factory (2026-10-29)";

interface DemoRun {
  detail: RunDetail;
  stages: Stage[];
  events: RunEvent[];
  checkpoints: Checkpoint[];
  review: ReviewReport | null;
  preview: PreviewInfo;
  files: Record<string, string>;
  timers: ReturnType<typeof setTimeout>[];
  listeners: Set<(ev: RunEvent) => void>;
  seq: number;
  maxCost: number;
}

const runs = new Map<string, DemoRun>();
const iso = (t: number) => new Date(t).toISOString().replace(/\.\d{3}Z$/, "Z");

function usageOf(r: DemoRun): UsageSummary {
  const by = new Map<string, { model_id: string; input_tokens: number; output_tokens: number; estimated_cost_usd: number; calls: number }>();
  let premium = 0;
  for (const e of r.events) {
    if (e.type !== "model_call_ok") continue;
    const d = e.data as Record<string, number | string>;
    const m = by.get(d.model_id as string) ?? { model_id: d.model_id as string, input_tokens: 0, output_tokens: 0, estimated_cost_usd: 0, calls: 0 };
    m.input_tokens += Number(d.input_tokens); m.output_tokens += Number(d.output_tokens);
    m.estimated_cost_usd = +(m.estimated_cost_usd + Number(d.cost_usd)).toFixed(6); m.calls += 1;
    by.set(m.model_id, m);
    if (d.slot === "A") premium += Number(d.cost_usd);
  }
  const list = [...by.values()];
  const inp = list.reduce((s, m) => s + m.input_tokens, 0);
  const out = list.reduce((s, m) => s + m.output_tokens, 0);
  const est = +list.reduce((s, m) => s + m.estimated_cost_usd, 0).toFixed(6);
  return { input_tokens: inp, output_tokens: out, total_tokens: inp + out, estimated_cost_usd: est, confirmed_cost_usd: null,
    premium_cost_usd: +premium.toFixed(6), free_allowance_used: null, remaining_budget_usd: Math.max(0, +(r.maxCost - est).toFixed(6)), by_model: list };
}

function emit(r: DemoRun, type: RunEventType, data: Record<string, unknown>, stageId: string | null, at = Date.now()) {
  r.seq += 1;
  const ev: RunEvent = { id: `evt_${r.detail.id}_${String(r.seq).padStart(5, "0")}`, run_id: r.detail.id, stage_id: stageId, type, timestamp: iso(at), data };
  r.events.push(ev);
  r.detail.updated_at = iso(at);
  r.detail.usage = usageOf(r);
  r.listeners.forEach((l) => l(ev));
}

function setStatus(r: DemoRun, status: RunStatus, at: number) {
  r.detail.status = status;
  emit(r, "run_status_changed", { status, demo: "synthetic" }, null, at);
}

function stage(r: DemoRun, agent: Stage["agent"], name: string, files: string[], depends: string[]): Stage {
  const s: Stage = { id: `${r.detail.id}-${agent}`, run_id: r.detail.id, name, agent, status: "pending", attempt: 1, max_attempts: 3,
    started_at: null, finished_at: null, depends_on: depends, files_allowed: files, result: null, review: null };
  r.stages.push(s);
  return s;
}

function call(r: DemoRun, s: Stage, at: number, o: { slot: "A" | "B" | "C"; task: string; reasons: string[]; inTok: number; outTok: number; latency: number; failed?: number }) {
  const tier = o.slot === "A" ? "premium" : o.slot === "B" ? "coding" : "economy";
  const model = modelFor(tier);
  const [pin, pout] = PRICES[model] ?? (tier === "premium" ? [1, 3] : tier === "coding" ? [0.3, 0.9] : [0.06, 0.24]);
  const cost = +((o.inTok * pin + o.outTok * pout) / 1e6).toFixed(6);
  emit(r, "route_decision", { demo: "synthetic", action: "call", slot: o.slot, model_id: model, reasons: o.reasons, reason: o.reasons.at(-1),
    estimated_cost_usd: cost, task: { agent: s.agent, task_type: o.task, failed_attempts: o.failed ?? 0, stage_id: s.id } }, s.id, at);
  emit(r, "model_call_started", { demo: "synthetic", slot: o.slot, model_id: model, attempt: 1 }, s.id, at + 50);
  emit(r, "model_call_ok", { demo: "synthetic", slot: o.slot, model_id: model, input_tokens: o.inTok, output_tokens: o.outTok,
    cost_usd: cost, price_verified: true, latency_s: o.latency, attempt: 1 }, s.id, at + o.latency * 1000);
}

/** The scripted arcade build. Times are offsets in ms; `speed` < 1 plays faster. */
function arcadeTimeline(r: DemoRun, t0: number, speed: number, outcome: "verified" | "budget"): { at: number; fn: (at: number) => void }[] {
  const T = (ms: number) => t0 + ms * speed;
  const planner = stage(r, "planner", "plan_and_contract", ["contract/**"], []);
  const backend = stage(r, "backend", "backend_build", ["backend/**"], [planner.id]);
  const frontend = stage(r, "frontend", "frontend_build", ["frontend/**"], [planner.id]);
  const reviewer = stage(r, "reviewer", "review_and_integrate", [], [backend.id, frontend.id]);
  const steps: { at: number; fn: (at: number) => void }[] = [];
  const at = (ms: number, fn: (at: number) => void) => steps.push({ at: T(ms), fn });
  const start = (s: Stage, ms: number, msg: string) => at(ms, (t) => { s.status = "running"; s.started_at = iso(t);
    emit(r, "stage_started", { name: s.name, agent: s.agent, attempt: s.attempt, message: msg, demo: "synthetic" }, s.id, t); });
  const finish = (s: Stage, ms: number, status: Stage["status"], extra: Record<string, unknown> = {}) => at(ms, (t) => {
    s.status = status; s.finished_at = iso(t);
    emit(r, "stage_finished", { name: s.name, agent: s.agent, status, demo: "synthetic", ...extra }, s.id, t); });

  at(0, (t) => emit(r, "run_created", { prompt: r.detail.prompt, demo: "synthetic" }, null, t));
  at(300, (t) => setStatus(r, "planning", t));
  start(planner, 600, "Designing stages and the OpenAPI contract");
  at(900, (t) => call(r, planner, t, { slot: "A", task: "plan", reasons: ["floor B: risk=critical, complexity=high", "default for plan: slot A"], inTok: 1650, outTok: 6200, latency: 3.1 }));
  at(4400, (t) => emit(r, "agent_message", { agent: "planner", message: "Plan: 4 endpoints (register, login, scores, leaderboard); 2 backend + 7 frontend files", demo: "synthetic" }, planner.id, t));
  at(4800, (t) => setStatus(r, "contract", t));
  at(5200, (t) => emit(r, "agent_message", { agent: "planner", message: "contract/openapi.yaml validated (OpenAPI 3.1, 4 paths)", demo: "synthetic" }, planner.id, t));
  finish(planner, 5400, "passed", { checks_passed: 1, checks_failed: 0 });
  at(5600, (t) => setStatus(r, "building", t));
  start(backend, 5800, "Backend Agent: FastAPI + SQLite against contract/openapi.yaml");
  start(frontend, 5900, "Frontend Agent: Snake, 2048, Memory Match against the contract");

  // Backend: replay the REAL recorded router events, re-timed, with the agent's real per-attempt notes.
  const rec = fx.backendEvents;
  const notes = fx.backendResult.notes;
  let cursor = 6200;
  rec.forEach((e, i) => {
    const gap = e.type === "model_call_ok" ? Math.round(Number((e.data as { latency_s?: number }).latency_s ?? 10) * 280) : 250;
    cursor += gap;
    at(cursor, (t) => emit(r, e.type, { ...e.data, demo: RECORDED }, backend.id, t));
    if (e.type === "model_call_ok") {
      const attempt = rec.slice(0, i + 1).filter((x) => x.type === "model_call_ok").length;
      cursor += 900;
      at(cursor, (t) => {
        backend.attempt = attempt;
        const last = attempt === rec.filter((x) => x.type === "model_call_ok").length;
        emit(r, "stage_progress", { name: backend.name, demo: RECORDED, attempt,
          message: last ? `Self-checks passed: ${fx.backendResult.checks.map((c) => c.name).join(", ")}` : `Self-check failed; model note: ${notes[attempt] ?? notes[attempt - 1] ?? ""}` }, backend.id, t);
      });
    }
  });
  const backendDone = cursor + 600;

  at(6300, (t) => call(r, frontend, t, { slot: "B", task: "code_frontend", reasons: ["floor B: non-trivial code generation", "default for code_frontend: slot B"], inTok: 3900, outTok: 9800, latency: 8 }));
  at(14800, (t) => emit(r, "stage_progress", { name: frontend.name, message: "Self-checks passed: files and references, JS syntax (5 files)", demo: "synthetic" }, frontend.id, t));

  if (outcome === "budget") {
    at(16000, (t) => {
      emit(r, "budget_warning", { demo: "synthetic", run_remaining_fraction: 0.14, message: "Low-budget mode: premium reserved for critical work" }, backend.id, t);
    });
    at(17500, (t) => {
      emit(r, "budget_exhausted", { source: "model_router", reason: "run_budget_exhausted", detail: "max_cost_usd 0.05 reached during backend_build", demo: "synthetic" }, backend.id, t);
      for (const s of [backend, frontend]) { if (s.status === "running") { s.status = "stopped"; s.finished_at = iso(t); } }
      reviewer.status = "skipped";
      r.detail.status = "stopped";
      const spent = usageOf(r).estimated_cost_usd;
      r.detail.error = { error: "budget_exhausted", message: `Stopped by budget during backend_build: $${spent.toFixed(4)} of max_cost_usd $${r.maxCost.toFixed(2)} spent (estimate), and the next model call would exceed the remaining $${Math.max(0, r.maxCost - spent).toFixed(4)}. No checkpoint was created after the contract.` };
      emit(r, "run_finished", { status: "stopped", reason: "budget_exhausted", demo: "synthetic" }, null, t + 200);
    });
    // Nothing happens after a hard stop: drop the rest of the recorded backend replay.
    return steps.filter((s) => s.at <= T(17_500)).sort((a, b) => a.at - b.at);
  }

  finish(frontend, 15200, "passed", { checks_passed: 2, checks_failed: 0 });
  at(15200, () => { frontend.result = { stage_id: frontend.id, status: "done", files_changed: Object.keys(fx.files).filter((f) => f.startsWith("frontend/")),
    checks: [{ name: "files and references", passed: true, log: "7 files" }, { name: "JS syntax", passed: true, log: "5 files ok" }],
    models_used: [`B:${modelFor("coding")}`], tokens: 13700, cost_usd: 0.0100, iterations: 1, notes: ["demo: synthetic frontend result"] }; });
  at(backendDone, (t) => {
    backend.result = { stage_id: backend.id, status: "done", files_changed: fx.backendResult.files_changed, checks: fx.backendResult.checks,
      models_used: fx.backendResult.models_used, tokens: fx.backendResult.tokens, cost_usd: fx.backendResult.cost_usd,
      iterations: fx.backendResult.iterations, notes: fx.backendResult.notes };
    for (const p of Object.keys(fx.files)) r.files[p] = fx.files[p];
    backend.status = "passed"; backend.finished_at = iso(t);
    emit(r, "stage_finished", { name: backend.name, agent: "backend", status: "passed", checks_passed: fx.backendResult.checks.length, checks_failed: 0, demo: RECORDED }, backend.id, t);
  });
  const rv = backendDone + 800;
  at(rv, (t) => setStatus(r, "verifying", t));
  start(reviewer, rv + 200, "Re-running every check in the sandbox (network off)");
  at(rv + 300, (t) => emit(r, "review_started", { message: "pytest, ruff, contract test, JS syntax, frontend↔contract match", demo: "synthetic" }, reviewer.id, t));
  at(rv + 2600, (t) => call(r, reviewer, t, { slot: "C", task: "summarize_log", reasons: ["default for summarize_log: slot C"], inTok: 2100, outTok: 180, latency: 1.2 }));
  at(rv + 4200, (t) => {
    const checks: CheckResult[] = [...fx.backendResult.checks.map((c) => ({ ...c, name: `backend: ${c.name}` })),
      { name: "frontend: files and references", passed: true, exit_code: 0, duration_ms: 12, log: "7 files" },
      { name: "frontend: JS syntax (node --check)", passed: true, exit_code: 0, duration_ms: 410, log: "5 files ok" },
      { name: "integration: API calls match contract", passed: true, exit_code: 0, duration_ms: 9, log: "4 endpoints used, all in contract" },
      { name: "integration: request fields match contract", passed: true, exit_code: 0, duration_ms: 4, log: "all required fields sent" },
      { name: "browser E2E (Playwright)", passed: true, skipped: true, log: "not executed: Playwright runner not configured in this demo" } as CheckResult];
    r.review = { stage_id: reviewer.id, verdict: "approve", checks, required_fixes: [], created_at: iso(t),
      summary: "All executed checks passed. Browser E2E was not executed." };
    reviewer.review = r.review; reviewer.status = "passed"; reviewer.finished_at = iso(t);
    emit(r, "review_finished", { verdict: "approve", checks_passed: checks.filter((c) => c.passed).length, checks_failed: 0, demo: "synthetic" }, reviewer.id, t);
    emit(r, "stage_finished", { name: reviewer.name, agent: "reviewer", status: "passed", checks_passed: 8, checks_failed: 0, demo: "synthetic" }, reviewer.id, t + 10);
  });
  at(rv + 4800, (t) => {
    setStatus(r, "checkpointing", t);
    const cp: Checkpoint = { id: `cp_${r.detail.id}_1`, run_id: r.detail.id, stage_id: reviewer.id, git_tag: "cp-1-green",
      summary: "Arcade: backend + frontend approved on executed checks", tests_passed: 8, created_at: iso(t) };
    r.checkpoints.push(cp);
    r.detail.latest_checkpoint_id = cp.id;
    emit(r, "checkpoint_created", { git_tag: cp.git_tag, checkpoint_id: cp.id, message: cp.summary, demo: "synthetic" }, reviewer.id, t);
  });
  at(rv + 5600, (t) => {
    r.preview = { status: "ready", url: `${window.location.origin}/demo-preview/index.html`, health_check_passed: true, error: null };
    emit(r, "preview_ready", { url: r.preview.url, demo: "synthetic (static frontend; API calls are not served in demo)" }, null, t);
  });
  at(rv + 6000, (t) => {
    r.detail.status = "done"; r.detail.current_stage_id = null;
    emit(r, "run_finished", { status: "done", message: "Verified build: checkpoint cp-1-green", demo: "synthetic" }, null, t);
  });
  return steps.sort((a, b) => a.at - b.at); // event ids must follow time order (Last-Event-ID replay)
}

function newRun(prompt: string, project: string | undefined, maxCost: number, created: number, fixedId?: string): DemoRun {
  // Seeded runs keep stable ids so links survive a reload; runs started in the demo live only in this tab's memory.
  const id = fixedId ?? `run_${created.toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const r: DemoRun = {
    detail: { id, status: "created", prompt, project_name: project, created_at: iso(created), updated_at: iso(created),
      current_stage_id: null, stages: [], latest_checkpoint_id: null, error: null, usage: undefined as unknown as UsageSummary },
    stages: [], events: [], checkpoints: [], review: null, files: {}, timers: [], listeners: new Set(), seq: 0, maxCost,
    preview: { status: "pending", url: null, health_check_passed: null, error: null },
  };
  r.detail.usage = usageOf(r);
  r.detail.stages = r.stages;
  runs.set(id, r);
  return r;
}

function playLive(r: DemoRun, outcome: "verified" | "budget") {
  const t0 = Date.now();
  for (const s of arcadeTimeline(r, t0, 1, outcome)) {
    r.timers.push(setTimeout(() => {
      s.fn(s.at);
      const running = r.stages.find((x) => x.status === "running");
      r.detail.current_stage_id = running?.id ?? r.detail.current_stage_id;
    }, s.at - t0));
  }
}

function seed() {
  const now = Date.now();
  const done = newRun("Build an arcade website with Snake, 2048 and Memory Match, user accounts, score submission and a global leaderboard per game.",
    "arcade", 2, now - 3_600_000, "run_demo_arcade");
  for (const s of arcadeTimeline(done, now - 3_600_000, 1, "verified")) s.fn(s.at);
  const stopped = newRun("Build a notes app with sign-up and login, notes with tags, full-text search, and a REST API with tests.",
    "notes", 0.05, now - 1_800_000, "run_demo_notes");
  for (const s of arcadeTimeline(stopped, now - 1_800_000, 1, "budget")) s.fn(s.at);
}

// ---- HTTP handling --------------------------------------------------------------------------------
const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
const err = (status: number, error: string, message: string) => json({ error, message, request_id: null }, status);

function tree(r: DemoRun): FileTreeResponse {
  const dirs = new Set<string>();
  for (const p of Object.keys(r.files)) { const parts = p.split("/"); for (let i = 1; i < parts.length; i++) dirs.add(parts.slice(0, i).join("/")); }
  return { run_id: r.detail.id, root: "project", entries: [...[...dirs].map((d) => ({ path: d, kind: "directory" as const })),
    ...Object.entries(r.files).map(([p, c]) => ({ path: p, kind: "file" as const, size_bytes: c.length }))] };
}

function sse(r: DemoRun, lastId: string | null, signal?: AbortSignal | null): Response {
  const enc = new TextEncoder();
  let off: (() => void) | null = null;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const send = (ev: RunEvent) => controller.enqueue(enc.encode(`id: ${ev.id}\nevent: ${ev.type}\ndata: ${JSON.stringify(ev)}\n\n`));
      const idx = lastId ? r.events.findIndex((e) => e.id === lastId) + 1 : 0;
      r.events.slice(idx).forEach(send);
      const terminal = ["done", "failed", "cancelled", "stopped"].includes(r.detail.status);
      if (terminal) { controller.close(); return; }
      const l = (ev: RunEvent) => { send(ev); if (ev.type === "run_finished") { off?.(); setTimeout(() => controller.close(), 50); } };
      r.listeners.add(l);
      off = () => r.listeners.delete(l);
      signal?.addEventListener("abort", () => { off?.(); try { controller.close(); } catch { /* closed */ } });
    },
    cancel() { off?.(); },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

async function handle(url: URL, init: RequestInit): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const path = url.pathname.replace(/^.*?\/api\/v1/, "");
  const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
  await new Promise((res) => setTimeout(res, 120)); // network-ish latency
  if (path === "/health") return json({ status: "ok", version: "demo", dependencies: { orchestrator: "ok", sandbox: "ok", model_router: "ok", deploy: "unavailable" } });
  if (path === "/models") return json({ models: MODELS.map((m) => ({ ...m, health: "healthy" })) });
  if (path === "/runs" && method === "GET") {
    const st = url.searchParams.get("status");
    const items = [...runs.values()].map((r) => r.detail).filter((d) => !st || d.status === st).sort((a, b) => b.created_at.localeCompare(a.created_at));
    return json({ items: items.slice(0, Number(url.searchParams.get("limit") ?? 20)), next_cursor: null });
  }
  if (path === "/runs" && method === "POST") {
    if (!body?.prompt || String(body.prompt).trim().length < 5)
      return json({ error: "validation_error", message: "Request validation failed", issues: [{ path: ["prompt"], message: "at least 5 characters" }] }, 422);
    const r = newRun(body.prompt, body.project_name, body.budget?.max_cost_usd ?? 2, Date.now());
    playLive(r, (body.budget?.max_cost_usd ?? 2) < 0.06 ? "budget" : "verified");
    return json(r.detail, 202);
  }
  const m = /^\/runs\/([^/]+)(\/.*)?$/.exec(path);
  if (!m) return err(404, "not_found", `No route ${method} ${path}`);
  const r = runs.get(decodeURIComponent(m[1]));
  if (!r) return err(404, "not_found", `Run ${m[1]} not found`);
  const sub = m[2] ?? "";
  switch (`${method} ${sub}`) {
    case "GET ": return json(r.detail);
    case "DELETE ": {
      if (["done", "failed", "cancelled", "stopped"].includes(r.detail.status)) return err(409, "conflict", "Run already finished.");
      r.timers.forEach(clearTimeout);
      setTimeout(() => { r.stages.forEach((s) => { if (s.status === "running" || s.status === "pending") s.status = "stopped"; });
        r.detail.status = "cancelled"; emit(r, "run_finished", { status: "cancelled", reason: "cancelled by user", demo: "synthetic" }, null); }, 900);
      return json({ accepted: true, message: "Cancellation requested; the orchestrator stops at the next safe point.", operation_id: null }, 202);
    }
    case "GET /stages": return json({ run_id: r.detail.id, stages: r.stages });
    case "GET /events": return sse(r, (init.headers as Record<string, string>)?.["Last-Event-ID"] ?? null, init.signal);
    case "GET /usage": return json(usageOf(r));
    case "GET /review": return r.review ? json(r.review) : err(404, "not_found", "No review yet");
    case "GET /checkpoints": return json({ run_id: r.detail.id, checkpoints: r.checkpoints });
    case "GET /preview": return json(r.preview);
    case "GET /files": return json(tree(r));
    case "GET /files/content": {
      const p = url.searchParams.get("path") ?? "";
      return p in r.files ? json({ path: p, content: r.files[p], encoding: "utf-8", truncated: false }) : err(404, "not_found", `No file ${p}`);
    }
    case "GET /download": return err(503, "unavailable", "ZIP download is not available in demo mode.");
    case "GET /contracts": {
      if (!r.files["contract/openapi.yaml"] && r.stages[0]?.status !== "passed") return err(404, "not_found", "No contract yet");
      const a: ContractArtifact = { run_id: r.detail.id, path: "contract/openapi.yaml", openapi_version: "3.1.0", valid: true, content: fx.contract, updated_at: r.stages[0]?.finished_at ?? undefined };
      return json(a);
    }
    case "POST /rollback": {
      const cp = r.checkpoints.find((c) => c.id === body?.checkpoint_id);
      if (!cp) return json({ error: "validation_error", message: "Unknown checkpoint", issues: [{ path: ["checkpoint_id"], message: "not found" }] }, 422);
      const prev = r.detail.status;
      r.detail.status = "rolling_back";
      emit(r, "rollback_started", { checkpoint_id: cp.id, git_tag: cp.git_tag, message: body.reason ?? "", demo: "synthetic" }, null);
      setTimeout(() => { r.detail.status = prev; emit(r, "rollback_finished", { checkpoint_id: cp.id, git_tag: cp.git_tag, message: `Workspace restored to ${cp.git_tag}`, demo: "synthetic" }, null); }, 1800);
      return json({ accepted: true, message: `Rollback to ${cp.git_tag} accepted.`, operation_id: null }, 202);
    }
    case "POST /followups": {
      const nr = newRun(body.prompt, r.detail.project_name, 2, Date.now());
      playLive(nr, "verified");
      return json(nr.detail, 202);
    }
    case "GET /deployments/latest": return err(404, "not_found", "No deployments");
    case "POST /deployments": return err(409, "conflict", "Deployment is not configured on this server (demo).");
    default: return err(404, "not_found", `No route ${method} ${path}`);
  }
}

export function installDemo(): void {
  seed();
  setTransport(async (input, init) => {
    const url = new URL(input, window.location.origin);
    if (!url.pathname.includes("/api/v1")) return fetch(input, init);
    return handle(url, init);
  });
}

export const __demoForTests = { handle, seed, runs };
