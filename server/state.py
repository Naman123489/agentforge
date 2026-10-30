"""Run records and their event logs for the Control Plane API.

A run's pipeline executes in its own worker thread/event loop (agent checks run blocking subprocesses), so
events are appended under a lock and subscribers (SSE connections on the server loop) are woken with
call_soon_threadsafe.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TERMINAL = {"done", "failed", "cancelled", "stopped"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class RunRecord:
    id: str
    prompt: str
    project_name: str | None
    workspace: Path
    budget: dict
    options: dict
    status: str = "created"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    current_stage_id: str | None = None
    latest_checkpoint_id: str | None = None
    error: dict | None = None
    stages: list[dict] = field(default_factory=list)
    checkpoints: list[dict] = field(default_factory=list)
    review: dict | None = None
    preview: dict = field(default_factory=lambda: {"status": "pending", "url": None, "health_check_passed": None, "error": None})
    contract_errors: list[str] = field(default_factory=list)
    contract_updated_at: str | None = None
    change_requests: list[dict] = field(default_factory=list)
    deployments: list[dict] = field(default_factory=list)  # platform DeploymentStatus, newest last
    visual: dict | None = None  # latest visual QA: defects + screenshot paths (workspace-relative)
    parent_run_id: str | None = None
    # runtime (not serialised)
    router: Any = None
    events: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _subscribers: set = field(default_factory=set)  # (loop, asyncio.Queue)
    worker_loop: asyncio.AbstractEventLoop | None = None
    worker_task: asyncio.Task | None = None
    cancel_requested: bool = False

    # ---- events -----------------------------------------------------------------------
    def emit(self, type_: str, stage_id: str | None = None, **data: Any) -> dict:
        return self.append({"type": type_, "stage_id": stage_id, "data": data})

    def append(self, ev: dict) -> dict:
        """Add an event (from the pipeline or the model router). The server assigns ids so they are ordered."""
        with self._lock:
            seq = len(self.events) + 1
            out = {"id": f"evt_{self.id}_{seq:06d}", "run_id": self.id, "stage_id": ev.get("stage_id"),
                   "type": ev["type"], "timestamp": ev.get("timestamp") or now_iso(), "data": ev.get("data") or {}}
            self.events.append(out)
            self.updated_at = out["timestamp"]
            subs = list(self._subscribers)
        self._persist_event(out)
        for loop, q in subs:
            loop.call_soon_threadsafe(q.put_nowait, out)
        return out

    def subscribe(self, loop: asyncio.AbstractEventLoop, last_event_id: str | None) -> tuple[list[dict], asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            idx = 0
            if last_event_id:
                idx = next((i + 1 for i, e in enumerate(self.events) if e["id"] == last_event_id), 0)
            backlog = self.events[idx:]
            self._subscribers.add((loop, q))
        return backlog, q

    def unsubscribe(self, loop: asyncio.AbstractEventLoop, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard((loop, q))

    def _persist_event(self, ev: dict) -> None:
        try:
            with open(self.workspace.parent / "events.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, default=str) + "\n")
        except OSError:
            pass

    # ---- stages -----------------------------------------------------------------------
    def new_stage(self, agent: str, name: str, files_allowed: list[str], depends_on: list[str],
                  max_attempts: int) -> dict:
        s = {"id": f"{self.id}-{name}", "run_id": self.id, "name": name, "agent": agent, "status": "pending",
             "attempt": 1, "max_attempts": max_attempts, "started_at": None, "finished_at": None,
             "depends_on": depends_on, "files_allowed": files_allowed, "result": None, "review": None}
        self.stages.append(s)
        return s

    def start_stage(self, s: dict, message: str) -> None:
        s["status"], s["started_at"], s["finished_at"] = "running", now_iso(), None
        self.current_stage_id = s["id"]
        self.emit("stage_started", s["id"], name=s["name"], agent=s["agent"], attempt=s["attempt"], message=message)

    def finish_stage(self, s: dict, status: str, message: str = "", **extra: Any) -> None:
        s["status"], s["finished_at"] = status, now_iso()
        self.emit("stage_finished", s["id"], name=s["name"], agent=s["agent"], status=status, message=message, **extra)

    def set_status(self, status: str, **data: Any) -> None:
        self.status = status
        self.emit("run_status_changed", None, status=status, **data)

    # ---- serialisation (contract shapes) --------------------------------------------------
    def usage(self) -> dict:
        if self.router is None:
            frozen = getattr(self, "_frozen_usage", None)  # run loaded from disk after a restart
            if frozen:
                return frozen
            return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0,
                    "confirmed_cost_usd": None, "premium_cost_usd": 0.0, "free_allowance_used": None,
                    "remaining_budget_usd": None, "by_model": []}
        return self.router.usage_summary()

    def detail(self) -> dict:
        d = {"id": self.id, "status": self.status, "prompt": self.prompt,
             "created_at": self.created_at, "updated_at": self.updated_at, "current_stage_id": self.current_stage_id,
             "stages": self.stages, "latest_checkpoint_id": self.latest_checkpoint_id, "usage": self.usage(),
             "error": self.error}
        if self.project_name:  # contract: string, not nullable -> omit when unset
            d["project_name"] = self.project_name
        return d

    def save(self) -> None:
        meta = {k: getattr(self, k) for k in ("id", "prompt", "project_name", "budget", "options", "status", "created_at",
                                             "updated_at", "current_stage_id", "latest_checkpoint_id", "error", "stages",
                                             "checkpoints", "review", "preview", "contract_errors", "contract_updated_at",
                                             "change_requests", "parent_run_id", "deployments", "visual")}
        meta["usage"] = self.usage()
        (self.workspace.parent / "run.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")

    @classmethod
    def load(cls, run_dir: Path) -> "RunRecord":
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        usage = meta.pop("usage", None)
        rec = cls(workspace=run_dir / "project", **meta)
        if rec.status not in TERMINAL:  # the process that ran it is gone; be truthful about it
            rec.status = "failed"
            rec.error = {"error": "server_restarted", "message": "The server restarted while this run was in progress."}
        for d in rec.deployments:  # a deployment cannot still be running in a new process
            if d.get("status") in ("queued", "publishing", "deploying"):
                d.update(status="failed", error="server restarted during the deployment")
        if (run_dir / "events.jsonl").exists():
            rec.events = [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines() if line]
        rec._frozen_usage = usage
        return rec
