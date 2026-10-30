import { describe, expect, it } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PromptComposer, validatePrompt } from "../components/run/PromptComposer";
import { StagePipeline } from "../components/run/StagePipeline";
import { RunHeader } from "../components/run/RunHeader";
import { CheckpointsPanel } from "../components/panels/CheckpointsPanel";
import { PreviewPanel, safePreviewUrl } from "../components/panels/PreviewPanel";
import { ReviewPanel } from "../components/panels/ReviewPanel";
import { UsageSummaryView } from "../components/panels/UsagePanel";
import { buildTree } from "../components/panels/FilesPanel";
import { render } from "@testing-library/react";
import { event, json, mockApi, renderApp, runDetail, stage, usage } from "./utils";

describe("Prompt composer", () => {
  it("validates input and does not call the API for an invalid prompt", async () => {
    expect(validatePrompt("hi", "")).toMatch(/at least 5/);
    expect(validatePrompt("Build a todo app", "x".repeat(101))).toMatch(/Project name/);
    expect(validatePrompt("Build a todo app", "")).toBeNull();
    const calls = mockApi({});
    renderApp(<PromptComposer />);
    await userEvent.type(await screen.findByLabelText("Describe the application"), "hey");
    await userEvent.click(screen.getByRole("button", { name: /start build/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/at least 5 characters/);
    expect(calls).toHaveLength(0);
  });

  it("starts a run with POST /runs, prevents double submission, and shows the accepted run id and status", async () => {
    let resolve!: (r: Response) => void;
    const calls = mockApi({ "POST /runs": () => new Promise<Response>((r) => { resolve = r; }) });
    renderApp(<PromptComposer />);
    await userEvent.click(await screen.findByRole("button", { name: "Build a SaaS dashboard" }));
    const submit = screen.getByRole("button", { name: /start build/i });
    await userEvent.click(submit);
    await userEvent.click(screen.getByRole("button", { name: /submitting/i })); // second click while pending
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(1);
    const body = calls[0].body as { prompt: string; project_name: string; budget: { max_cost_usd: number } };
    expect(body.prompt).toMatch(/SaaS analytics dashboard/);
    expect(body.project_name).toBe("saas-dashboard");
    expect(body.budget.max_cost_usd).toBe(10);
    expect((body.budget as { max_tokens?: number }).max_tokens).toBe(4_000_000);  // default effort: Large
    expect(calls[0].headers["Idempotency-Key"]).toBeTruthy();
    await act(async () => resolve(json(runDetail({ id: "run_42", status: "created" }), 202)));
    expect(await screen.findByText(/Run run_42 accepted — status: Queued/)).toBeInTheDocument();
    expect(await screen.findByText("observer page")).toBeInTheDocument();
  });
});

describe("Stage pipeline reflects backend state", () => {
  it("shows each stage's real status, parallel builders, retries, and the checkpoint", () => {
    const stages = [
      stage({ id: "p", agent: "planner", status: "passed", started_at: "2026-10-29T08:00:00Z", finished_at: "2026-10-29T08:00:05Z" }),
      stage({ id: "b", agent: "backend", status: "running", attempt: 2, started_at: "2026-10-29T08:00:06Z" }),
      stage({ id: "f", agent: "frontend", status: "running", started_at: "2026-10-29T08:00:06Z" }),
      stage({ id: "r", agent: "reviewer", status: "pending" }),
    ];
    const events = [event({ type: "route_decision", stage_id: "b", data: { model_id: "nvidia/nemotron-3-super-120b-a12b", slot: "B" } })];
    render(<StagePipeline stages={stages} events={events} checkpoints={[]} selectedStage={null} onSelect={() => {}} />);
    expect(screen.getByRole("button", { name: "Planner / Architect: Passed" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Backend Agent: Retrying · attempt 2\/3/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Frontend Agent: Running" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reviewer / Integrator: Pending" })).toBeInTheDocument();
    expect(screen.getByText("parallel")).toBeInTheDocument();
    expect(screen.getByText("nemotron-3-super-120b-a12b")).toBeInTheDocument();
    expect(screen.getByText("None yet")).toBeInTheDocument(); // no checkpoint invented
  });
});

describe("Usage honesty", () => {
  it("shows estimated vs confirmed cost from the response, never confirmed when null", () => {
    render(<UsageSummaryView usage={usage({ estimated_cost_usd: 0.0391, confirmed_cost_usd: null })} />);
    expect(screen.getByText("$0.04")).toBeInTheDocument(); // 0.0391 shown to the cent
    expect(screen.getByText("not confirmed")).toBeInTheDocument();
    expect(screen.getByText("provider has not confirmed billing")).toBeInTheDocument();
  });
});

describe("Run header", () => {
  it("shows the actual budget-stop reason from the server", () => {
    const run = runDetail({ status: "stopped", error: { error: "budget_exhausted", message: "Stopped by budget: max_cost_usd reached during backend_build" } });
    renderApp(<RunHeader run={run} events={[event({ type: "budget_exhausted", data: { reason: "run_budget_exhausted" } })]} />);
    return screen.findByText("Stopped by budget").then(() => {
      expect(screen.getByText(/max_cost_usd reached during backend_build/)).toBeInTheDocument();
    });
  });

  it("flags a finished-but-unverified run and cancels through DELETE /runs/{id} with confirmation", async () => {
    renderApp(<RunHeader run={runDetail({ status: "done" })} review={null} events={[]} />);
    expect(await screen.findByText("Not verified")).toBeInTheDocument();
    const calls = mockApi({ "DELETE /runs/run_1": () => json({ error: "conflict", message: "Run already finished." }, 409) });
    renderApp(<RunHeader run={runDetail({ status: "building" })} events={[]} />);
    await userEvent.click((await screen.findAllByRole("button", { name: /cancel run/i }))[0]);
    await userEvent.click(screen.getByRole("button", { name: /request cancellation/i, hidden: true }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/v1/runs/run_1")).toBe(true));
    expect(await screen.findByText("Run already finished.")).toBeInTheDocument(); // error surfaced, not swallowed
  });
});

describe("Checkpoints and rollback", () => {
  it("requires confirmation, posts RollbackRequest, and does not claim success before the server confirms", async () => {
    const calls = mockApi({
      "GET /runs/run_1/checkpoints": () => json({ run_id: "run_1", checkpoints: [
        { id: "cp1", run_id: "run_1", stage_id: "r", git_tag: "cp-1-green", summary: "green", tests_passed: 8, created_at: "2026-10-29T08:00:00Z" }] }),
      "POST /runs/run_1/rollback": () => json({ accepted: true, message: "Rollback to cp-1-green accepted." }, 202),
    });
    renderApp(<CheckpointsPanel run={runDetail({ status: "done" })} events={[]} />);
    await userEvent.click(await screen.findByRole("button", { name: /roll back here/i }));
    expect(calls.some((c) => c.method === "POST")).toBe(false); // nothing sent before confirming
    await userEvent.type(screen.getByLabelText(/reason/i), "bad deploy");
    await userEvent.click(screen.getByRole("button", { name: /request rollback/i, hidden: true }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ checkpoint_id: "cp1", reason: "bad deploy", retry_after_rollback: false }));
    expect(await screen.findByText("Rollback to cp-1-green accepted.")).toBeInTheDocument();
    expect(screen.queryByText(/Rollback confirmed by the server/)).toBeNull();
  });
});

describe("Preview", () => {
  it("only loads backend-supplied http(s) URLs and explains failures", async () => {
    expect(safePreviewUrl("javascript:alert(1)")).toBeNull();
    expect(safePreviewUrl(null)).toBeNull();
    expect(safePreviewUrl("https://preview.example/run_1/")?.href).toBe("https://preview.example/run_1/");
    mockApi({ "GET /runs/run_1/preview": () => json({ status: "failed", error: "vite build failed: missing module" }) });
    renderApp(<PreviewPanel runId="run_1" />);
    expect(await screen.findByText("vite build failed: missing module")).toBeInTheDocument();
  });

  it("isolates a same-origin preview (no allow-same-origin with scripts)", async () => {
    mockApi({ "GET /runs/run_1/preview": () => json({ status: "ready", url: `${window.location.origin}/p/` }) });
    renderApp(<PreviewPanel runId="run_1" />);
    const frame = await screen.findByTitle("Generated application preview");
    expect(frame.getAttribute("sandbox")).toContain("allow-scripts");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
  });
});

describe("Review", () => {
  it("distinguishes passed, failed and not-executed checks, and builder self-checks from the Reviewer", async () => {
    mockApi({ "GET /runs/run_1/review": () => json({ stage_id: "r", verdict: "reject", required_fixes: ["fix POST /api/scores"], created_at: "2026-10-29T08:00:00Z",
      checks: [{ name: "pytest", passed: true, exit_code: 0, duration_ms: 812 }, { name: "contract test", passed: false, exit_code: 1, log: "FAIL POST /api/scores" },
        { name: "browser E2E", passed: true, skipped: true }] }) });
    const s = stage({ status: "passed", result: { stage_id: "s", status: "done", files_changed: [], checks: [{ name: "ruff", passed: true }], models_used: [], tokens: 0, cost_usd: 0, iterations: 1, notes: [] } });
    renderApp(<ReviewPanel run={runDetail({ status: "done" })} stages={[s]} />);
    expect(await screen.findByText("Verification failed: Reviewer rejected")).toBeInTheDocument();
    expect(screen.getByText("1/3 checks passed · 1 not executed", { exact: false })).toBeInTheDocument();
    const reviewer = screen.getByText(/Reviewer checks/).closest("section")!;
    expect(within(reviewer).getAllByText("failed").length).toBeGreaterThan(0); // badge + screen-reader text
    expect(within(reviewer).getAllByText("not executed").length).toBeGreaterThan(0);
    expect(screen.getByText("fix POST /api/scores")).toBeInTheDocument();
    expect(screen.getByText(/Builder self-checks/)).toBeInTheDocument();
  });

  it("states plainly when there is no Reviewer report", async () => {
    mockApi({ "GET /runs/run_1/review": () => json({ error: "not_found", message: "none" }, 404) });
    renderApp(<ReviewPanel run={runDetail({ status: "done" })} stages={[]} />);
    expect(await screen.findByText("Completed but NOT verified")).toBeInTheDocument();
  });
});

describe("Files", () => {
  it("builds a tree from FileTreeResponse and drops paths that escape the workspace", () => {
    const tree = buildTree([{ path: "backend", kind: "directory" }, { path: "backend/app.py", kind: "file" },
      { path: "../secrets.txt", kind: "file" }, { path: "frontend/index.html", kind: "file" }]);
    expect(tree.map((n) => n.name)).toEqual(["backend", "frontend"]);
    expect(tree[0].children[0].path).toBe("backend/app.py");
  });
});



