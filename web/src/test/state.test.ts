import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { RunEventStore } from "../state/runEvents";
import { explainRouting, describeEvent } from "../lib/events";
import { countChecks, stageDisplay } from "../lib/status";
import { fmtUsd } from "../lib/format";
import { redact } from "../components/run/EventStream";
import { event, stage } from "./utils";

describe("RunEventStore", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("drops duplicate and foreign events, orders by timestamp, and refreshes the affected queries", () => {
    const qc = new QueryClient();
    const inv = vi.spyOn(qc, "invalidateQueries");
    const store = new RunEventStore("run_1", qc);
    store.add(event({ id: "b", type: "stage_started", timestamp: "2026-10-29T08:00:02Z" }));
    store.add(event({ id: "a", type: "run_created", timestamp: "2026-10-29T08:00:01Z" }));
    store.add(event({ id: "b", type: "stage_started", timestamp: "2026-10-29T08:00:02Z" })); // replay after reconnect
    store.add(event({ id: "x", run_id: "other", type: "run_created" }));
    vi.advanceTimersByTime(1000);
    expect(store.getSnapshot().events.map((e) => e.id)).toEqual(["a", "b"]);
    const keys = inv.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
    expect(keys).toContain(JSON.stringify(["stages", "run_1"]));
    expect(keys).toContain(JSON.stringify(["run", "run_1"]));
  });

  it("batches bursts: many events cause one snapshot update", () => {
    const store = new RunEventStore("run_1", new QueryClient());
    const listener = vi.fn();
    store.subscribe(listener);
    for (let i = 0; i < 200; i++) store.add(event({ id: `e${i}`, type: "model_call_ok", timestamp: `2026-10-29T08:00:${String(i % 60).padStart(2, "0")}Z` }));
    vi.advanceTimersByTime(150);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().events).toHaveLength(200);
  });
});

describe("Routing explanations come only from the router's own reasons", () => {
  it("decodes the real escalation reason recorded in the live Backend Agent run", () => {
    expect(explainRouting({ reasons: ["default for test_repair: slot B", "escalate B->A after 2 failed attempt(s)"] }))
      .toBe("Escalated to the premium model after 2 failed attempts.");
    expect(explainRouting({ reasons: ["default for code_backend: slot B", "using fallback A (rate_limited)"] }))
      .toBe("Fell back to the premium model: the preferred model was rate-limited.");
    expect(explainRouting({ reasons: ["default for summarize_log: slot C"] })).toBe("Default economy model for summarize log.");
  });

  it("does not invent a reason when the event has none", () => {
    expect(explainRouting({})).toBe("Routing decision (no reason supplied).");
    expect(describeEvent(event({ type: "model_call_failed", data: { error_kind: "rate_limit" } })).title).toBe("Model call failed: rate limit");
  });
});

describe("Honest status and money", () => {
  it("unknown cost is never shown as $0", () => {
    expect(fmtUsd(null)).toBe("unknown");
    expect(fmtUsd(0)).toBe("$0.00");
    expect(fmtUsd(0.00123)).toBe("$0.0012");
  });

  it("not-executed checks are never counted as passed", () => {
    const c = countChecks([{ passed: true }, { passed: false }, { passed: true, skipped: true }]);
    expect(c).toMatchObject({ passed: 1, failed: 1, notExecuted: 1, total: 3 });
    expect(c.label).toBe("1/3 checks passed · 1 not executed");
  });

  it("'Retrying' is derived only from the backend's attempt counter", () => {
    expect(stageDisplay(stage({ status: "running", attempt: 1 })).label).toBe("Running");
    expect(stageDisplay(stage({ status: "running", attempt: 2, max_attempts: 3 })).label).toBe("Retrying · attempt 2/3");
    expect(stageDisplay(stage({ status: "failed" })).tone).toBe("bad");
  });

  it("event details never render prompts, reasoning or credentials", () => {
    expect(redact({ a: 1, prompt: "secret prompt", nested: [{ reasoning_content: "cot", Authorization: "Bearer x" }] }))
      .toEqual({ a: 1, prompt: "[hidden]", nested: [{ reasoning_content: "[hidden]", Authorization: "[hidden]" }] });
  });
});
