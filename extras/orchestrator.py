"""Run state machine (section 7.1 of the requirements).

PLANNING -> (contract valid) checkpoint -> BUILDING (backend || frontend) -> REVIEWING
   approve -> checkpoint -> DONE
   reject  -> FIXING (only the failing side, with the executed failure logs) -> REVIEWING   (max_fix_attempts)
   fixes exhausted -> ROLLED_BACK to last checkpoint -> rebuild once with the failure log
   still failing / router stops / budget exhausted -> FAILED or STOPPED (report, never loop)
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import checks as checks_mod
from . import sandbox
from .agents import Builder, BuildFormatError, Planner, Reviewer
from engine.router import ModelRouter
from engine.router.errors import RouterStopped
from .schemas import Check, Checkpoint, Plan, ReviewReport, RunState, StageStatus

STAGES = ["plan", "backend", "frontend", "review"]


class EventBus:
    """In-process pub/sub for one run; every event is also appended to events.jsonl (replay, audit)."""

    def __init__(self, log_path: Path):
        self.history: list[dict] = []
        self.subscribers: list[asyncio.Queue] = []
        self.log_path = log_path

    def publish(self, ev: dict) -> None:
        ev.setdefault("ts", time.time())
        self.history.append(ev)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, default=str) + "\n")
        for q in self.subscribers:
            q.put_nowait(ev)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        for ev in self.history:
            q.put_nowait(ev)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self.subscribers:
            self.subscribers.remove(q)


class Run:
    def __init__(self, run_id: str, request: str, root: Path, router: ModelRouter, edition_label: str,
                 max_fix_attempts: int = 3):
        from .workspace import Workspace

        self.id, self.request, self.edition = run_id, request, edition_label
        self.dir = root / run_id
        self.ws = Workspace(self.dir / "project")
        self.bus = EventBus(self.dir / "events.jsonl")
        self.router = router
        router.on_event = lambda ev: self.emit("router", **ev)
        self.state = RunState.CREATED
        self.stages: dict[str, StageStatus] = {s: StageStatus.PENDING for s in STAGES}
        self.stage_model: dict[str, str] = {}
        self.plan: Plan | None = None
        self.checkpoints: list[Checkpoint] = []
        self.last_report: ReviewReport | None = None
        self.max_fix_attempts = max_fix_attempts
        self.preview = sandbox.Preview(self.ws.root, run_id)
        self.preview_port: int | None = None
        self.error: str | None = None
        self.started = time.time()
        self.task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    # ---- events ------------------------------------------------------------------
    def emit(self, type_: str, **data: Any) -> None:
        if type_ == "router":
            data["kind"] = data.pop("type", "")
            if data["kind"] == "model_call_ok":
                stage = {"plan": "plan", "build-backend": "backend", "build-frontend": "frontend",
                         "review": "review"}.get((data.get("task") or {}).get("stage_id", ""), None)
                if stage:
                    self.stage_model[stage] = f"{data['slot']} · {data['model_id'].split('/')[-1]}"
        data.pop("run_id", None)
        self.bus.publish({"type": type_, "run_id": self.id, **data})

    def set_state(self, state: RunState, note: str = "") -> None:
        self.state = state
        self.emit("state", state=state.value, note=note)

    def set_stage(self, stage: str, status: StageStatus, note: str = "") -> None:
        self.stages[stage] = status
        self.emit("stage", stage=stage, status=status.value, note=note, model=self.stage_model.get(stage))

    def say(self, agent: str, text: str) -> None:
        self.emit("activity", agent=agent, text=text)

    def snapshot(self) -> dict:
        return {
            "id": self.id, "request": self.request, "edition": self.edition, "state": self.state.value,
            "stages": {k: v.value for k, v in self.stages.items()}, "stage_model": self.stage_model,
            "plan": self.plan.model_dump() if self.plan else None,
            "checkpoints": [c.model_dump() for c in self.checkpoints],
            "report": self.last_report.model_dump() if self.last_report else None,
            "stats": self.router.stats(), "preview_port": self.preview_port, "error": self.error,
            "sandbox": sandbox.backend_name(), "elapsed_s": round(time.time() - self.started, 1),
        }

    # ---- main flow ---------------------------------------------------------------
    async def execute(self, followup: str | None = None) -> None:
        async with self._lock:
            try:
                await self._plan(followup)
                await self._build_and_verify(followup or self.request)
            except RouterStopped as e:
                self.error = f"router stopped: {e.reason} ({e.detail[:300]})"
                self.set_state(RunState.STOPPED, self.error)
            except Exception as e:  # noqa: BLE001 - report every failure to the user, never hang
                self.error = f"{type(e).__name__}: {e}"
                self.set_state(RunState.FAILED, self.error)
            finally:
                self.emit("stats", **self.router.stats())

    async def _plan(self, followup: str | None) -> None:
        self.set_state(RunState.PLANNING)
        self.set_stage("plan", StageStatus.RUNNING)
        self.say("planner", "Designing the plan and the API contract..." if not followup
                 else f"Updating the plan for: {followup}")
        planner, problems = Planner(self.router), []
        for _ in range(2):  # S2-1: valid contract within 2 attempts
            plan = await planner.plan(followup or self.request, existing=self.plan if followup else None,
                                      failures=problems or None)
            problems = checks_mod.contract_problems(plan.contract)
            if not problems:
                break
            self.say("planner", "Contract rejected by validator: " + "; ".join(problems))
        if problems:
            self.set_stage("plan", StageStatus.FAILED, "; ".join(problems))
            raise RuntimeError("planner could not produce a valid contract")
        self.plan = plan
        self.ws.write_json("contract/plan.json", plan.model_dump())
        self.ws.write_json("contract/contract.json", plan.contract.model_dump())
        self.emit("plan", plan=plan.model_dump())
        self.say("planner", f"{plan.title}: {len(plan.contract.endpoints)} endpoints, "
                            f"{len(plan.backend_files)} backend + {len(plan.frontend_files)} frontend files")
        self.set_stage("plan", StageStatus.PASSED)
        self._checkpoint("contract", "Plan and API contract validated", [])

    async def _build_and_verify(self, request: str, report: ReviewReport | None = None) -> None:
        """Build (or, given a rejecting report, go straight to fixing) until approved or out of attempts."""
        assert self.plan
        failing: dict[str, list[Check]] = {"backend": [], "frontend": []}
        todo = {"backend", "frontend"}
        rolled_back = False
        attempt = 0
        if report is not None:
            failing = {s: report.failing(s) for s in ("backend", "frontend")}
            todo = {s for s, f in failing.items() if f} or todo
            attempt = 1
        while True:
            self.set_state(RunState.BUILDING if attempt == 0 else RunState.FIXING)
            await asyncio.gather(*(self._build_side(side, request, failing[side], attempt) for side in sorted(todo)))
            self.ws.commit(f"build attempt {attempt}")

            report = await self._review()
            if report.verdict == "approve":
                cp = self._checkpoint("green", "All checks passed", [c.name for c in report.checks])
                self.preview_port = self.preview.start()
                self.emit("preview", port=self.preview_port)
                self.set_state(RunState.DONE, f"checkpoint {cp.git_tag}")
                return

            failing = {s: report.failing(s) for s in ("backend", "frontend")}
            todo = {s for s, f in failing.items() if f} or {"backend", "frontend"}
            attempt += 1
            if attempt < self.max_fix_attempts:
                self.say("orchestrator", f"Review rejected; fix attempt {attempt}/{self.max_fix_attempts - 1} "
                                         f"for {', '.join(sorted(todo))}")
                continue
            if rolled_back:
                self.set_state(RunState.FAILED, "checks still failing after rollback; stopping (FR-9)")
                return
            tag = self.checkpoints[-1].git_tag
            self.ws.rollback(tag)
            rolled_back, todo = True, {"backend", "frontend"}
            for s in ("backend", "frontend"):
                self.set_stage(s, StageStatus.ROLLED_BACK, f"restored {tag}")
            self.set_state(RunState.ROLLED_BACK, f"retries exhausted; restored {tag}, rebuilding with failure log")

    async def _build_side(self, side: str, request: str, failing: list[Check], attempt: int) -> None:
        self.set_stage(side, StageStatus.RUNNING)
        self.say(side, "Fixing: " + ", ".join(c.name for c in failing) if failing else "Writing code...")
        try:
            res = await Builder(self.router, side).build(request, self.plan, self.ws, failing, attempt)
        except BuildFormatError as e:
            self.say(side, f"Output rejected: {e}")
            self.set_stage(side, StageStatus.FAILED, str(e))
            return
        self.emit("files", agent=side, files=[f.path for f in res.files])
        self.say(side, f"Wrote {len(res.files)} file(s): " + ", ".join(f.path for f in res.files))
        self.set_stage(side, StageStatus.PASSED, "built (awaiting review)")

    async def _review(self) -> ReviewReport:
        self.set_state(RunState.REVIEWING)
        self.set_stage("review", StageStatus.RUNNING)
        self.say("reviewer", f"Running checks in the {sandbox.backend_name()} sandbox...")
        results = await asyncio.to_thread(checks_mod.run_checks, self.ws, self.plan.contract)
        for c in results:
            self.emit("check", **c.model_dump())
        failing = [c for c in results if not c.passed]
        explanation = await Reviewer(self.router).explain(failing)
        report = ReviewReport(checks=results, verdict="reject" if failing else "approve", summary=explanation,
                              required_fixes={s: [c.name for c in failing if c.owner in (s, "integration")]
                                              for s in ("backend", "frontend")})
        self.last_report = report
        self.emit("review", verdict=report.verdict, summary=explanation,
                  passed=sum(c.passed for c in results), total=len(results))
        self.set_stage("review", StageStatus.PASSED if not failing else StageStatus.FAILED,
                       f"{len(results) - len(failing)}/{len(results)} checks passed")
        for side in ("backend", "frontend"):
            if report.failing(side):
                self.set_stage(side, StageStatus.FAILED, ", ".join(c.name for c in report.failing(side)))
        return report

    def _checkpoint(self, kind: str, summary: str, tests: list[str]) -> Checkpoint:
        n = len(self.checkpoints)
        tag = f"cp-{n}-{kind}"
        self.ws.commit(f"checkpoint {tag}: {summary}")
        self.ws.tag(tag, summary)
        cp = Checkpoint(id=uuid.uuid4().hex[:8], stage=kind, git_tag=tag, summary=summary, tests_passed=tests)
        self.checkpoints.append(cp)
        self.ws.write_json(f"contract/checkpoints/{tag}.json",
                           {**cp.model_dump(), "models": self.stage_model, "stats": self.router.stats()})
        self.ws.commit(f"checkpoint metadata {tag}")
        self.emit("checkpoint", **cp.model_dump())
        return cp

    # ---- user actions ------------------------------------------------------------
    async def rollback(self, tag: str) -> None:
        async with self._lock:
            self.ws.rollback(tag)
            self.preview.stop()
            self.preview_port = None
            self.emit("rollback", git_tag=tag)
            self.set_state(RunState.ROLLED_BACK, f"user restored {tag}")
            plan_path = self.ws.root / "contract" / "plan.json"
            if plan_path.exists():
                self.plan = Plan.model_validate_json(plan_path.read_text())

    async def inject_bug(self) -> str:
        """Demo fault: break one API path in the frontend, then let the Reviewer catch and fix it."""
        async with self._lock:
            for rel in self.ws.files("frontend/"):
                if not rel.endswith(".js"):
                    continue
                src = self.ws.read(rel)
                for e in (self.plan.contract.endpoints if self.plan else []):
                    lit = e.path.lstrip("/")
                    if e.method == "POST" and lit in src:
                        bad = lit.rstrip("s") + "z"
                        self.ws.write(rel, src.replace(lit, bad, 1))
                        self.ws.commit("injected fault")
                        note = f"{rel}: '{lit}' -> '{bad}'"
                        self.emit("fault", note=note)
                        self.say("orchestrator", f"Injected a bug for the demo: {note}")
                        break
                else:
                    continue
                break
            else:
                return "nothing to inject"
        await self._verify_only()
        return note

    async def _verify_only(self) -> None:
        async with self._lock:
            try:
                await self._build_and_verify_after_fault()
            except RouterStopped as e:
                self.error = f"router stopped: {e.reason}"
                self.set_state(RunState.STOPPED, self.error)

    async def _build_and_verify_after_fault(self) -> None:
        report = await self._review()
        if report.verdict == "approve":
            self.set_state(RunState.DONE, "fault did not break any check")
            return
        await self._build_and_verify(self.request, report)

    def close(self) -> None:
        self.preview.stop()
