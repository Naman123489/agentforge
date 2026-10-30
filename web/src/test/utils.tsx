import type { ReactNode } from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { setTransport } from "../api/client";
import { ToastProvider } from "../components/ui/Toast";
import type { RunDetail, RunEvent, Stage, UsageSummary } from "../api/types";

export interface Call { method: string; url: string; headers: Record<string, string>; body: unknown }

/** Test-only transport: route table of (method path) -> handler. Records every call. */
export function mockApi(routes: Record<string, (call: Call) => Response | Promise<Response>>) {
  const calls: Call[] = [];
  setTransport(async (input, init) => {
    const url = new URL(input, "http://test.local");
    const method = (init.method ?? "GET").toUpperCase();
    const call: Call = { method, url: url.pathname + url.search, headers: (init.headers ?? {}) as Record<string, string>,
      body: typeof init.body === "string" ? JSON.parse(init.body) : undefined };
    calls.push(call);
    const key = `${method} ${url.pathname.replace(/^\/api\/v1/, "")}`;
    const h = routes[key];
    if (!h) return json({ error: "not_found", message: `no mock for ${key}` }, 404);
    return h(call);
  });
  return calls;
}

export const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

export function renderApp(ui: ReactNode, { path = "/", route = "/" } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false } } });
  // Declarative router: the data router builds fetch Requests, whose AbortSignal jsdom cannot provide.
  return { qc, ...render(
    <QueryClientProvider client={qc}><ToastProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path={route} element={ui} /><Route path="/runs/:runId" element={<p>observer page</p>} /></Routes>
      </MemoryRouter>
    </ToastProvider></QueryClientProvider>) };
}

export const usage = (over: Partial<UsageSummary> = {}): UsageSummary => ({
  input_tokens: 1000, output_tokens: 500, total_tokens: 1500, estimated_cost_usd: 0.0123, confirmed_cost_usd: null,
  premium_cost_usd: 0, free_allowance_used: null, remaining_budget_usd: 1.98, by_model: [], ...over });

export const stage = (over: Partial<Stage> = {}): Stage => ({
  id: "s-backend", run_id: "run_1", name: "backend_build", agent: "backend", status: "pending", attempt: 1, max_attempts: 3,
  started_at: null, finished_at: null, ...over });

export const runDetail = (over: Partial<RunDetail> = {}): RunDetail => ({
  id: "run_1", status: "building", prompt: "Build an arcade", project_name: "arcade", created_at: "2026-10-29T08:00:00Z",
  updated_at: "2026-10-29T08:01:00Z", stages: [], usage: usage(), error: null, ...over });

export const event = (over: Partial<RunEvent> & Pick<RunEvent, "type">): RunEvent => ({
  id: `evt_${Math.random().toString(36).slice(2)}`, run_id: "run_1", stage_id: null, timestamp: "2026-10-29T08:00:00Z", data: {}, ...over });
