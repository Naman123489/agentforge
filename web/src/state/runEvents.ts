// Per-run live event store fed by SSE. Lives outside React so streams survive navigation and a burst of
// events re-renders only the components that read the store (batched, ~10 updates/s max).
import { useEffect, useSyncExternalStore } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { streamRunEvents, type StreamStatus } from "../api/sse";
import type { RunEvent, RunEventType } from "../api/types";
import { keys } from "./queryClient";

export interface RunEventSnapshot {
  events: RunEvent[];
  status: StreamStatus | "idle";
  detail?: string;
  reconnects: number;
}

const INVALIDATE: Partial<Record<RunEventType, ((id: string) => readonly unknown[])[]>> = {
  run_created: [keys.run, () => keys.runs()],
  run_status_changed: [keys.run, keys.stages, () => keys.runs()],
  run_finished: [keys.run, keys.stages, keys.usage, keys.review, keys.files, keys.preview, () => keys.runs()],
  stage_started: [keys.stages, keys.run],
  stage_progress: [keys.stages],
  stage_finished: [keys.stages, keys.run, keys.files],
  review_started: [keys.stages],
  review_finished: [keys.review, keys.stages],
  checkpoint_created: [keys.checkpoints, keys.run],
  rollback_started: [keys.run],
  rollback_finished: [keys.checkpoints, keys.run, keys.stages, keys.files, keys.review],
  model_call_ok: [keys.usage],
  model_call_failed: [keys.usage],
  budget_warning: [keys.usage],
  budget_exhausted: [keys.usage, keys.run],
  contract_change_requested: [keys.contract, keys.stages],
  preview_ready: [keys.preview],
  deployment_status: [keys.deployment],
};

export class RunEventStore {
  private snapshot: RunEventSnapshot = { events: [], status: "idle", reconnects: 0 };
  private ids = new Set<string>();
  private pending: RunEvent[] = [];
  private listeners = new Set<() => void>();
  private flushTimer: ReturnType<typeof setTimeout> | null = null;
  private abort: AbortController | null = null;
  private dirtyKeys = new Map<string, readonly unknown[]>();
  private invalidateTimer: ReturnType<typeof setTimeout> | null = null;
  readonly runId: string;
  private readonly qc: QueryClient;

  constructor(runId: string, qc: QueryClient) {
    this.runId = runId;
    this.qc = qc;
  }

  subscribe = (l: () => void) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };
  getSnapshot = () => this.snapshot;
  get running() {
    return this.abort !== null;
  }

  start(opts: { live: boolean }) {
    if (this.abort) return;
    if (!opts.live && this.snapshot.status === "closed") return; // finished run: history already loaded
    const ac = new AbortController();
    this.abort = ac;
    const lastId = this.snapshot.events.at(-1)?.id;
    void streamRunEvents(this.runId, {
      onEvent: (ev) => this.add(ev),
      onStatus: (status, detail) => {
        this.set({ status, detail, reconnects: this.snapshot.reconnects + (status === "reconnecting" ? 1 : 0) });
        if (status !== "open" && status !== "connecting" && status !== "reconnecting") this.abort = null;
      },
      // After every (re)connect, reconcile with the REST state: SSE is not the only source of truth.
      onOpen: () => this.invalidateAll(),
    }, ac.signal, { lastEventId: lastId, reconnect: opts.live });
  }

  stop() {
    this.abort?.abort();
    this.abort = null;
  }

  /** Exposed for tests and the demo; production events arrive through start(). */
  add(ev: RunEvent) {
    if (ev.run_id !== this.runId || this.ids.has(ev.id)) return; // drop duplicates (replays after reconnect)
    this.ids.add(ev.id);
    this.pending.push(ev);
    for (const k of INVALIDATE[ev.type] ?? []) {
      const key = k(this.runId);
      this.dirtyKeys.set(JSON.stringify(key), key);
    }
    if (ev.type === "run_finished") setTimeout(() => this.stop(), 1500); // terminal: stop after final reconcile
    this.scheduleFlush();
    this.scheduleInvalidate();
  }

  private scheduleFlush() {
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      const merged = [...this.snapshot.events, ...this.pending];
      this.pending = [];
      // Order by timestamp; stable for equal timestamps (arrival order).
      merged.sort((a, b) => (a.timestamp < b.timestamp ? -1 : a.timestamp > b.timestamp ? 1 : 0));
      this.set({ events: merged });
    }, 100);
  }

  private scheduleInvalidate() {
    if (this.invalidateTimer) return;
    this.invalidateTimer = setTimeout(() => {
      this.invalidateTimer = null;
      for (const key of this.dirtyKeys.values()) void this.qc.invalidateQueries({ queryKey: key });
      this.dirtyKeys.clear();
    }, 600);
  }

  private invalidateAll() {
    for (const k of [keys.run, keys.stages, keys.usage, keys.review, keys.checkpoints, keys.preview])
      void this.qc.invalidateQueries({ queryKey: k(this.runId) });
  }

  private set(patch: Partial<RunEventSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((l) => l());
  }
}

const stores = new Map<string, RunEventStore>();

export function getRunEventStore(runId: string, qc: QueryClient): RunEventStore {
  let s = stores.get(runId);
  if (!s) {
    s = new RunEventStore(runId, qc);
    stores.set(runId, s);
  }
  return s;
}

/** Subscribe to a run's live events. The stream keeps running after unmount until the run finishes. */
export function useRunEvents(runId: string | undefined, qc: QueryClient, live: boolean, enabled = true): RunEventSnapshot {
  const store = runId ? getRunEventStore(runId, qc) : null;
  useEffect(() => {
    // Live runs: keep the stream (with reconnects). Finished runs: read the history once.
    if (enabled && store && !store.running) store.start({ live });
  }, [store, live, enabled]);
  return useSyncExternalStore(store?.subscribe ?? noopSubscribe, store?.getSnapshot ?? emptySnapshot);
}

const EMPTY: RunEventSnapshot = { events: [], status: "idle", reconnects: 0 };
const noopSubscribe = () => () => {};
const emptySnapshot = () => EMPTY;
