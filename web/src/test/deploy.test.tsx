import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { DeployPanel, deployView, VIEW } from "../components/panels/DeployPanel";
import type { DeploymentStatus } from "../api/types";
import { json, mockApi, renderApp, runDetail } from "./utils";

const dep = (over: Partial<DeploymentStatus>): DeploymentStatus => ({
  id: "dep_1", status: "pushed", target: "github", url: null, health_check_passed: null, error: null,
  created_at: "2026-10-30T08:00:00Z", repo_url: "https://github.com/acme/arcade-1", git_tag: "stage-r-green",
  commit_sha: "a".repeat(40), checks: [{ name: "secret_scan", passed: true }], notes: [], ...over });

describe("Deploy states", () => {
  it("derives not configured, waiting and disabled from server facts", () => {
    expect(deployView({ status: "done" }, "unavailable", null)).toBe("not_configured");
    expect(deployView({ status: "building" }, "ok", null)).toBe("waiting");
    expect(deployView({ status: "done" }, "ok", null)).toBe("disabled");
    for (const s of ["queued", "publishing", "pushed", "deploying", "healthy", "failed", "rolled_back"] as const)
      expect(deployView({ status: "done" }, "ok", dep({ status: s }))).toBe(s);
  });

  it("shows a GitHub push as pushed with no live URL, never as healthy", async () => {
    mockApi({
      "GET /health": () => json({ status: "ok", version: "t", dependencies: { deploy: "ok" } }),
      "GET /runs/run_1/deployments/latest": () => json(dep({})),
      "GET /runs/run_1/review": () => json({ stage_id: "r", verdict: "approve", checks: [], required_fixes: [], created_at: "2026-10-30T08:00:00Z" }),
      "GET /runs/run_1/checkpoints": () => json({ run_id: "run_1", checkpoints: [] }),
    });
    renderApp(<DeployPanel run={runDetail({ status: "done" })} />);
    expect(await screen.findByText(VIEW.pushed.label)).toBeInTheDocument();
    expect(screen.getByText("none (no live deployment)")).toBeInTheDocument();
    expect(screen.getByText("not run")).toBeInTheDocument();
    expect(screen.queryByText(VIEW.healthy.label)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /github.com\/acme\/arcade-1/ })).toHaveAttribute("href", "https://github.com/acme/arcade-1");
  });

  it("explains why publishing is unavailable", async () => {
    mockApi({
      "GET /health": () => json({ status: "ok", version: "t", dependencies: { deploy: "unavailable" } }),
      "GET /runs/run_1/review": () => json({ stage_id: "r", verdict: "reject", checks: [], required_fixes: [], created_at: "2026-10-30T08:00:00Z" }),
      "GET /runs/run_1/checkpoints": () => json({ run_id: "run_1", checkpoints: [] }),
    });
    renderApp(<DeployPanel run={runDetail({ status: "done" })} />);
    expect(await screen.findByText(VIEW.not_configured.label)).toBeInTheDocument();
    expect(await screen.findByText(/the Reviewer has not approved the final stage/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Publish/ })).not.toBeInTheDocument();
  });
});
