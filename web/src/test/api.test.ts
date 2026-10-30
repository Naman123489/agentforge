import { describe, expect, it } from "vitest";
import { ApiError, isSafeRelativePath } from "../api/client";
import { api } from "../api/endpoints";
import { createSseParser, parseRunEvent, streamRunEvents, type SseFrame, type StreamStatus } from "../api/sse";
import { json, mockApi } from "./utils";

describe("API client (contract paths, errors)", () => {
  it("maps 422 ValidationErrorResponse issues, 429 Retry-After and proxy 5xx", async () => {
    mockApi({
      "POST /runs": () => json({ error: "validation_error", message: "Invalid", issues: [{ path: ["prompt"], message: "too short" }] }, 422),
      "GET /runs": () => json({ error: "rate_limited", message: "Slow down." }, 429, { "Retry-After": "7" }),
      "GET /health": () => new Response("Internal Server Error", { status: 500 }),
    });
    const e1 = await api.createRun({ prompt: "x" }, "k1").catch((e) => e);
    expect(e1).toBeInstanceOf(ApiError);
    expect(e1.kind).toBe("validation");
    expect(e1.issues[0].path).toEqual(["prompt"]);
    const e2 = await api.listRuns().catch((e) => e);
    expect(e2.kind).toBe("rate_limited");
    expect(e2.retryAfterSeconds).toBe(7);
    expect(e2.retryable).toBe(true);
    const e3 = await api.getHealth().catch((e) => e);
    expect(e3.kind).toBe("network"); // dev proxy failure is "API not reachable", not a server bug
  });

  it("sends Idempotency-Key on run creation and uses /api/v1 paths", async () => {
    const calls = mockApi({ "POST /runs": () => json({ id: "run_9" }, 202) });
    await api.createRun({ prompt: "Build a thing" }, "key-123");
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toBe("/api/v1/runs");
    expect(calls[0].headers["Idempotency-Key"]).toBe("key-123");
  });

  it("refuses workspace paths that could escape the run workspace, without calling the API", () => {
    const calls = mockApi({});
    for (const bad of ["../etc/passwd", "/abs/path", "C:/x", "a/../../b", "a\\b", "./x"]) {
      expect(isSafeRelativePath(bad)).toBe(false);
      expect(() => api.getRunFileContent("run_1", bad)).toThrow(ApiError);
    }
    expect(isSafeRelativePath("backend/app.py")).toBe(true);
    expect(calls).toHaveLength(0);
  });
});

describe("SSE parsing", () => {
  it("parses ids, multi-line data, comments and CRLF across chunk boundaries", () => {
    const frames: SseFrame[] = [];
    const feed = createSseParser((f) => frames.push(f));
    feed(": keep-alive\r\nid: e1\r\nevent: route_decision\r\ndata: {\"a\":");
    feed("1}\r\n\r\nid: e2\ndata: line1\ndata: line2\n\n");
    expect(frames).toEqual([
      { id: "e1", event: "route_decision", retry: undefined, data: "{\"a\":1}" },
      { id: "e2", event: undefined, retry: undefined, data: "line1\nline2" },
    ]);
  });

  it("parseRunEvent rejects malformed payloads and fills id from the SSE frame", () => {
    expect(parseRunEvent({ data: "not json" })).toBeNull();
    expect(parseRunEvent({ data: "{\"type\":1}" })).toBeNull();
    const ev = parseRunEvent({ id: "evt_7", data: JSON.stringify({ run_id: "r", type: "run_created", timestamp: "t" }) });
    expect(ev?.id).toBe("evt_7");
    expect(ev?.data).toEqual({});
  });

  it("reconnects with Last-Event-ID after a dropped stream and stops on 401", async () => {
    let n = 0;
    const calls = mockApi({
      "GET /runs/run_1/events": () => {
        n += 1;
        if (n === 1) return new Response(`id: e1\ndata: ${JSON.stringify({ id: "e1", run_id: "run_1", type: "run_created", timestamp: "t", data: {} })}\n\n`,
          { status: 200, headers: { "Content-Type": "text/event-stream" } });
        return json({ error: "unauthorized", message: "bad token" }, 401);
      },
    });
    const seen: string[] = [];
    const statuses: StreamStatus[] = [];
    await streamRunEvents("run_1", { onEvent: (e) => seen.push(e.id), onStatus: (s) => statuses.push(s) },
      new AbortController().signal, { sleep: async () => {} });
    expect(seen).toEqual(["e1"]);
    expect(calls[1].headers["Last-Event-ID"]).toBe("e1");
    expect(statuses).toEqual(["connecting", "open", "reconnecting", "unauthorized"]);
  });

  it("reads a finished run's history once instead of reconnecting forever", async () => {
    let n = 0;
    mockApi({ "GET /runs/run_1/events": () => { n += 1; return new Response("", { status: 200, headers: { "Content-Type": "text/event-stream" } }); } });
    await streamRunEvents("run_1", { onEvent: () => {}, onStatus: () => {} }, new AbortController().signal, { reconnect: false, sleep: async () => {} });
    expect(n).toBe(1);
  });
});
