import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PromptComposer } from "../components/run/PromptComposer";
import { buildSteps } from "../components/run/RunProgress";
import { agentStats, platformTotals } from "../components/insights/aggregate";
import LandingPage from "../pages/LandingPage";
import { json, mockApi, renderApp, runDetail, stage, usage } from "./utils";

describe("Create page", () => {
  it("fills the prompt from an example card without starting a build", async () => {
    const calls = mockApi({});
    renderApp(<PromptComposer />);
    await userEvent.click(await screen.findByRole("button", { name: "Create a project management tool" }));
    expect((screen.getByLabelText("Describe the application") as HTMLTextAreaElement).value).toMatch(/project management tool/);
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("points to Settings when the API rejects the token", async () => {
    mockApi({ "POST /runs": () => json({ error: "unauthorized", message: "missing token" }, 401) });
    renderApp(<PromptComposer />);
    await userEvent.type(screen.getByLabelText("Describe the application"), "Build a todo app with login");
    await userEvent.click(screen.getByRole("button", { name: /start build/i }));
    expect(await screen.findByRole("link", { name: /Settings to add your platform token/ })).toHaveAttribute("href", "/settings");
  });
});

describe("Run progress is derived from server state", () => {
  it("is green only for stages that passed, amber while running, red on failure", () => {
    const run = runDetail({ status: "building" });
    const stages = [
      stage({ id: "p", agent: "planner", status: "passed" }),
      stage({ id: "b", agent: "backend", status: "running" }),
      stage({ id: "f", agent: "frontend", status: "passed" }),
    ];
    const steps = buildSteps(run, stages, [], null);
    expect(steps.map((s) => s.tone)).toEqual(["ok", "live", "neutral", "neutral", "neutral"]);

    const failed = buildSteps(runDetail({ status: "failed" }), [...stages.slice(0, 1), stage({ id: "b", agent: "backend", status: "failed" })], [], null);
    expect(failed[1].tone).toBe("bad");
    expect(failed[4].tone).toBe("bad");
  });

  it("does not mark completion verified without an approving review", () => {
    const steps = buildSteps(runDetail({ status: "done" }), [stage({ id: "r", agent: "reviewer", status: "failed" })], [], null);
    expect(steps[4]).toMatchObject({ tone: "warn", sub: "Not verified" });
  });
});

describe("Insights aggregates", () => {
  it("keeps confirmed cost and call counts unavailable unless every run reports them", () => {
    const runs = [
      runDetail({ id: "a", status: "done", usage: usage({ confirmed_cost_usd: 0.01, by_model: [{ model_id: "m", input_tokens: 1, output_tokens: 1, estimated_cost_usd: 0.01 }] }) }),
      runDetail({ id: "b", status: "failed", usage: usage({ confirmed_cost_usd: null }) }),
    ];
    const t = platformTotals(runs);
    expect(t).toMatchObject({ runs: 2, done: 1, failed: 1, confirmedCost: null, modelCalls: null });
    expect(t.estimatedCost).toBeCloseTo(0.0246);
  });

  it("counts passed and failed stages per agent", () => {
    const runs = [runDetail({ stages: [stage({ agent: "frontend", status: "passed" }), stage({ id: "x", agent: "frontend", status: "failed" })] })];
    expect(agentStats(runs).get("frontend")).toMatchObject({ completed: 1, failed: 1, attempts: 2 });
  });
});

describe("Landing page", () => {
  it("leads to the create page from every call to action", async () => {
    mockApi({});
    renderApp(<LandingPage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Your idea. A team of AI engineers. One working product.");
    for (const name of [/Start Building/, /Build with AgentForge/, /Get started/])
      expect(screen.getByRole("link", { name })).toHaveAttribute("href", "/new");
  });
});

describe("Effort", () => {
  it("sets the run's token pool and time from the chosen effort (default High = 4M)", async () => {
    const calls = mockApi({ "POST /runs": () => json(runDetail({ id: "run_e", status: "created" }), 202) });
    renderApp(<PromptComposer />);
    await userEvent.type(screen.getByLabelText("Describe the application"), "Build a todo app with login");
    await userEvent.click(screen.getByRole("button", { name: /Options/ }));
    expect(screen.getAllByRole("radio").map((r) => r.textContent)).toEqual(["Low", "Medium", "High"]);
    expect(screen.getByRole("radio", { name: "High" })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByLabelText(/max cost|project name/i)).toBeNull();
    await userEvent.click(screen.getByRole("radio", { name: "Low" }));
    await userEvent.click(screen.getByRole("button", { name: /start build/i }));
    const body = calls.find((c) => c.method === "POST")!.body as { budget: { max_tokens: number; max_wall_time_seconds: number; max_cost_usd: number } };
    expect(body.budget).toMatchObject({ max_tokens: 1_000_000, max_wall_time_seconds: 1800, max_cost_usd: 10 });
  });
});
