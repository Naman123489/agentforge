// GET /runs/{runId}/events as Server-Sent Events. Implemented over fetch (not EventSource) because the
// contract requires a Bearer token, which EventSource cannot send. Reconnects with Last-Event-ID.
import { ApiError, rawRequest } from "./client";
import type { RunEvent } from "./types";

export interface SseFrame {
  id?: string;
  event?: string;
  data: string;
  retry?: number;
}

/** Incremental SSE parser (WHATWG event-stream format). Feed text chunks; complete frames are emitted. */
export function createSseParser(onFrame: (f: SseFrame) => void): (chunk: string) => void {
  let buffer = "";
  let frame: { id?: string; event?: string; retry?: number; data?: string[] } = {};
  return (chunk: string) => {
    buffer += chunk;
    const lines = buffer.split(/\r\n|\r|\n/);
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line === "") {
        if (frame.data) onFrame({ id: frame.id, event: frame.event, retry: frame.retry, data: frame.data.join("\n") });
        frame = {};
        continue;
      }
      if (line.startsWith(":")) continue; // comment / keep-alive
      const i = line.indexOf(":");
      const field = i === -1 ? line : line.slice(0, i);
      let value = i === -1 ? "" : line.slice(i + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "data") (frame.data ??= []).push(value);
      else if (field === "id") frame.id = value;
      else if (field === "event") frame.event = value;
      else if (field === "retry" && /^\d+$/.test(value)) frame.retry = Number(value);
    }
  };
}

export function parseRunEvent(frame: SseFrame): RunEvent | null {
  try {
    const ev = JSON.parse(frame.data) as RunEvent;
    if (!ev || typeof ev !== "object" || typeof ev.type !== "string" || typeof ev.run_id !== "string") return null;
    if (!ev.id && frame.id) ev.id = frame.id;
    ev.data ??= {};
    return ev.id ? ev : null;
  } catch {
    return null;
  }
}

export type StreamStatus = "connecting" | "open" | "reconnecting" | "closed" | "unauthorized" | "not_found" | "error";

export interface StreamHandlers {
  onEvent: (ev: RunEvent) => void;
  onStatus: (s: StreamStatus, detail?: string) => void;
  /** Called after every successful (re)connection, so callers can reconcile with GET endpoints. */
  onOpen?: (reconnect: boolean) => void;
}

const MAX_BACKOFF_MS = 15_000;

/** Connects and keeps the stream alive until `signal` aborts or a terminal error occurs. */
export async function streamRunEvents(runId: string, h: StreamHandlers, signal: AbortSignal,
                                      opts: { lastEventId?: string; sleep?: (ms: number) => Promise<void>;
                                              /** false: read the stream once (history of a finished run). */
                                              reconnect?: boolean } = {}) {
  const sleep = opts.sleep ?? ((ms) => new Promise<void>((r) => setTimeout(r, ms)));
  let lastId = opts.lastEventId;
  let attempt = 0;
  let retryMs = 1000;
  let connectedOnce = false;
  while (!signal.aborted) {
    h.onStatus(connectedOnce ? "reconnecting" : "connecting");
    try {
      const res = await rawRequest(`/runs/${encodeURIComponent(runId)}/events`, {
        signal,
        headers: { Accept: "text/event-stream", "Cache-Control": "no-cache", ...(lastId ? { "Last-Event-ID": lastId } : {}) },
      });
      if (!res.body) throw new ApiError("unexpected", "Event stream has no body");
      h.onStatus("open");
      h.onOpen?.(connectedOnce);
      connectedOnce = true;
      attempt = 0;
      const parse = createSseParser((f) => {
        if (f.retry) retryMs = f.retry;
        const ev = parseRunEvent(f);
        if (!ev) return;
        lastId = ev.id;
        h.onEvent(ev);
      });
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        parse(value);
      }
    } catch (e) {
      if (signal.aborted || (e as Error)?.name === "AbortError") break;
      if (e instanceof ApiError && e.kind === "unauthorized") return h.onStatus("unauthorized", e.message);
      if (e instanceof ApiError && e.kind === "not_found") return h.onStatus("not_found", e.message);
      h.onStatus("reconnecting", e instanceof Error ? e.message : String(e));
    }
    if (signal.aborted || (opts.reconnect === false && connectedOnce)) break;
    attempt += 1;
    await sleep(Math.min(MAX_BACKOFF_MS, retryMs * 2 ** Math.min(attempt - 1, 4)));
  }
  h.onStatus("closed");
}
