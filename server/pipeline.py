"""Run pipeline (requirements 7.1), executed in a per-run worker thread with its own event loop.

PLANNING -> CONTRACT (validated, checkpoint) -> BUILDING (backend || frontend) -> VERIFYING (Reviewer re-runs the
checks) -> approve: CHECKPOINTING -> DONE (+ preview)
          reject: retry the builder with the Reviewer's failures (max_retries_per_stage) -> exhausted:
          ROLLING_BACK to the last checkpoint -> FAILED with the reason.
Hard limits: tokens / cost / premium (model router, server-side), wall time (here), cancellation (cooperative).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import urllib.request
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from engine.agents import deploy_platform
from engine.agents.backend import BackendAgent
from engine.agents.deploy import redact
from engine.agents.deploy_models import DeployRequest
from engine.agents.contract import load_contract
from engine.agents.runner import Runner, default_runner
from engine.planner import PlannerAgent, PlannerFailed, tasks_brief, write_artifacts
from engine.reviewer import agentforge as reviewer_af
from engine.reviewer.analysis import analyze_failures
from engine.reviewer.checkpoint import create_checkpoint
from engine.reviewer.events import EventEmitter
from engine.reviewer.rollback import restore
from engine.reviewer.schemas import CheckResult as ReviewCheck, InternalCheckResult, ReviewReport as ReviewReportModel
from engine.router import RouterStopped
from engine.schemas import TaskAssignment

from .state import RunRecord, now_iso

CONTRACT_PATH = "contract/openapi.yaml"


# ---- git checkpoints (FR-7) --------------------------------------------------------------------
def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def git_init(root: Path) -> None:
    if (root / ".git").exists():
        return
    git(root, "init", "-q")
    git(root, "config", "user.email", "agentforge@local")
    git(root, "config", "user.name", "AgentForge")
    (root / ".gitignore").write_text("__pycache__/\n*.db\n.pytest_cache/\n.agentforge/\n", encoding="utf-8")


def checkpoint(rec: RunRecord, stage: dict, summary: str, tests_passed: int, report: dict | None = None) -> dict:
    """Commit the approved state and tag it with the Reviewer's annotated `stage-{stage_id}-green` convention.

    create_checkpoint() refuses a report whose verdict is not "approve" and a dirty tree, so a rejected stage can
    never get a green tag."""
    root = rec.workspace
    git(root, "add", "-A")
    if git(root, "status", "--porcelain"):
        git(root, "commit", "-q", "-m", f"{stage['name']}: {summary}")
    report = report or {"stage_id": stage["id"], "verdict": "approve", "checks": [], "created_at": now_iso()}
    model = ReviewReportModel(stage_id=stage["id"], verdict=report["verdict"], created_at=report["created_at"],
                              required_fixes=[], checks=[ReviewCheck(name=c["name"], passed=c["passed"],
                                                                     exit_code=c.get("exit_code"),
                                                                     duration_ms=c.get("duration_ms"))
                                                         for c in report.get("checks", [])])
    made = create_checkpoint(root, model, CONTRACT_PATH, summary=summary, run_id=rec.id)
    n = len(rec.checkpoints)
    commit = git(root, "rev-parse", f"refs/tags/{made.git_tag}^{{commit}}")
    cp = {"id": f"cp_{rec.id}_{n}", "run_id": rec.id, "stage_id": stage["id"], "git_tag": made.git_tag, "summary": summary,
          "tests_passed": tests_passed, "created_at": made.created_at, "commit": commit}
    meta = root.parent / "checkpoints"
    meta.mkdir(exist_ok=True)
    (meta / f"{made.git_tag}.json").write_text(json.dumps({**cp, "usage": rec.usage()}, indent=1), encoding="utf-8")
    rec.checkpoints.append(cp)
    rec.latest_checkpoint_id = cp["id"]
    rec.emit("checkpoint_created", stage["id"], git_tag=made.git_tag, checkpoint_id=cp["id"], message=summary)
    return cp


def rollback_to(rec: RunRecord, cp: dict) -> None:
    """Reviewer restore(): only an existing annotated stage-*-green tag, verified after the reset. Untracked files
    the failed attempt created are then removed; ignored files (.agentforge/ logs) are kept."""
    restore(rec.workspace, cp["git_tag"], allow_dirty=True)
    git(rec.workspace, "clean", "-q", "-fd")


def _rel(path: str, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).name


# ---- deploy (optional final operation) -------------------------------------------------------------------
class DeployRefused(Exception):
    pass


def deploy_preconditions(rec: RunRecord, target: str) -> None:
    """Raises DeployRefused with the exact reason. Deployment needs the server opt-in, a token, a supported target,
    and the run's final, Reviewer-approved checkpoint."""
    cap = deploy_platform.capability()
    if target not in deploy_platform.TARGETS:
        raise DeployRefused(f"unknown deployment target {target!r}")
    if target == "nebius_serverless":
        raise DeployRefused("Nebius deployment is not available: " + "; ".join(cap["nebius_missing"]))
    if target == "configured_target":
        raise DeployRefused("no deployment target is configured on this server (only GitHub publishing is available)")
    if not cap["github_ready"]:
        raise DeployRefused("deployment is not configured on this server: " + "; ".join(cap["missing"]))
    if not rec.checkpoints:
        raise DeployRefused("the run has no checkpoint")
    cp = rec.checkpoints[-1]
    why = deploy_platform.RunCheckpointVerifier(rec).why_not(rec.id, cp["id"], cp["git_tag"])
    if why:
        raise DeployRefused(f"the final checkpoint is not deployable: {why}")
    if any(d["status"] in ("queued", "publishing", "deploying") for d in rec.deployments):
        raise DeployRefused("a deployment for this run is already in progress")


def start_deployment(rec: RunRecord, target: str = "github") -> dict:
    """Record the queued deployment and its stage (call only after deploy_preconditions)."""
    cp = rec.checkpoints[-1]
    dep = deploy_platform.new_status(rec, target, cp)
    rec.deployments.append(dep)
    stage = next((st for st in rec.stages if st["agent"] == "deploy"), None)
    if stage is None:
        stage = rec.new_stage("deploy", "deploy_publish", [], [cp["stage_id"]], 1)
    else:
        stage["attempt"] += 1
    dep["stage_id"] = stage["id"]
    rec.emit("deployment_status", stage["id"], **_dep_event(dep))
    return dep


def _dep_event(dep: dict) -> dict:
    return redact({k: dep.get(k) for k in ("id", "status", "target", "url", "repo_url", "git_tag", "health_check_passed", "error")})


async def deploy_final(rec: RunRecord, dep: dict, repo_name: str | None = None, visibility: str = "private",
                       health_check_path: str = "/health", rollback_on_failure: bool = True, factory=None) -> dict:
    """Run the Deploy agent on the run's final approved checkpoint. Events go through rec.emit (secrets redacted);
    cancellation is the run's cancel flag. A push without a live deployment ends as `pushed`, never `healthy`."""
    stage = next(st for st in rec.stages if st["id"] == dep["stage_id"])
    cp = next(c for c in rec.checkpoints if c["id"] == dep["checkpoint_id"])
    rec.start_stage(stage, f"Publishing approved checkpoint {cp['git_tag']}")

    def on_event(ev: dict) -> None:  # the agent's own stage_started/finished are replaced by the run's stage events
        kind, data = ev.get("type"), redact(ev.get("data") or {})
        if kind == "stage_progress":
            step = str(data.get("step", ""))
            if step in ("checkpoint_verified", "approved_tag_verified", "staged", "secret_scan", "repo_ready"):
                dep["status"] = "publishing"
            elif step == "deploying":
                dep["status"] = "deploying"
            rec.emit("stage_progress", stage["id"], name=stage["name"], agent="deploy", step=step, message=f"deploy: {step}")
            if step in ("checkpoint_verified", "deploying"):
                rec.emit("deployment_status", stage["id"], **_dep_event(dep))
        elif kind == "deployment_status":
            rec.emit("deployment_status", stage["id"], **{**_dep_event(dep), **{k: data.get(k) for k in ("url", "error")}})
        elif kind == "error":
            rec.emit("error", stage["id"], source="deploy_agent", detail=str(data.get("message", ""))[:500])

    try:
        request = DeployRequest(run_id=rec.id, stage_id=stage["id"], checkpoint_id=cp["id"], git_tag=cp["git_tag"],
                                repo_name=deploy_platform.repo_name_for(rec, repo_name), visibility=visibility,
                                target=dep["target"], health_check_path=health_check_path,
                                rollback_on_failure=rollback_on_failure)
        agent = (factory or deploy_platform.build_agent)(rec, dep["target"], on_event, lambda: rec.cancel_requested)
        result = await agent.run_async(request, rec.workspace)
        deploy_platform.apply_result(dep, result)
    except Exception as e:  # noqa: BLE001 - invalid input or a factory error: the run stays verified, the deploy failed
        dep.update(status="failed", error=redact(f"{type(e).__name__}: {e}")[:300])
        result = None
    passed = sum(c["passed"] for c in dep["checks"])
    status = "passed" if dep["status"] in ("pushed", "healthy") else "stopped" if (result and result.status == "stopped") else "failed"
    msg = {"pushed": f"Pushed {cp['git_tag']} to {dep['repo_url']} (no live deployment)",
           "healthy": f"Deployed and healthy at {dep['url']}"}.get(dep["status"], dep.get("error") or "deployment failed")
    rec.emit("deployment_status", stage["id"], **_dep_event(dep))
    rec.finish_stage(stage, status, redact(msg)[:300], checks_passed=passed, checks_failed=len(dep["checks"]) - passed)
    return dep


# ---- preview (FR-15) ------------------------------------------------------------------------------
class PreviewManager:
    """Runs the generated backend with uvicorn (local process; the Docker sandbox owner can replace this)."""

    def __init__(self, host: str = "127.0.0.1"):
        self.host = host
        self.procs: dict[str, subprocess.Popen] = {}

    def start(self, rec: RunRecord) -> dict:
        self.stop(rec.id)

        with socket.socket() as s:
            s.bind((self.host, 0))
            port = s.getsockname()[1]
        env = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE")}
        # With a built frontend, serve UI + API on one origin (wrapper written by the frontend checks); else the API docs.
        with_ui = (rec.workspace / "frontend" / "dist" / "index.html").is_file() and \
            (rec.workspace / ".agentforge" / "preview_app.py").is_file()
        target = ["preview_app:app", "--app-dir", ".agentforge"] if with_ui else ["backend.app:app"]
        p = subprocess.Popen([sys.executable, "-m", "uvicorn", *target, "--host", self.host, "--port", str(port)],
                             cwd=rec.workspace, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs[rec.id] = p
        url = f"http://{'localhost' if self.host == '127.0.0.1' else self.host}:{port}/{'' if with_ui else 'docs'}"
        ok = False
        for _ in range(40):  # health check: wait for the generated app to answer
            if p.poll() is not None:
                break
            try:

                urllib.request.urlopen(f"http://{self.host}:{port}/openapi.json", timeout=1)
                ok = True
                break
            except OSError:
                time.sleep(0.25)
        if not ok:
            self.stop(rec.id)
            return {"status": "failed", "url": None, "health_check_passed": False,
                    "error": "the generated backend did not start (uvicorn backend.app:app)"}
        return {"status": "ready", "url": url, "health_check_passed": True, "error": None}

    def stop(self, run_id: str) -> None:
        p = self.procs.pop(run_id, None)
        if p and p.poll() is None:
            p.terminate()

    def stop_all(self) -> None:
        for rid in list(self.procs):
            self.stop(rid)


# ---- the pipeline ---------------------------------------------------------------------------------
def frontend_agent_factory() -> Callable[..., Any] | None:
    """The Frontend agent is a teammate's module; use it if installed (engine.agents.frontend.FrontendAgent)."""
    try:
        return importlib.import_module("engine.agents.frontend").FrontendAgent
    except (ImportError, AttributeError):
        return None


class Pipeline:
    def __init__(self, rec: RunRecord, preview: PreviewManager, runner: Runner | None = None,
                 source_workspace: Path | None = None, on_done: Callable[[RunRecord], None] | None = None,
                 frontend_agent: Any = "auto", deploy_factory: Callable[..., Any] | None = None):
        self.rec, self.preview, self.runner = rec, preview, runner or default_runner()
        self.deploy_factory = deploy_factory  # None: real GitHub client (tests inject fakes)
        # Visual QA repair rounds after the first approval (real browser); 0 disables. Env: AF_VISUAL_QA_ROUNDS.
        self.visual_rounds = int(os.environ.get("AF_VISUAL_QA_ROUNDS", "2"))
        self.visual_inspect = None  # tests inject a fake; None -> engine.agents.visual.inspect_workspace
        self.source_workspace = source_workspace
        self.on_done = on_done
        # "auto": use engine.agents.frontend if installed; None/False: no frontend stage; or an agent class.
        self.frontend_cls = frontend_agent_factory() if frontend_agent == "auto" else (frontend_agent or None)

    def start_thread(self) -> threading.Thread:
        t = threading.Thread(target=self._thread_main, name=f"run-{self.rec.id}", daemon=True)
        t.start()
        return t

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        self.rec.worker_loop = loop
        try:
            self.rec.worker_task = loop.create_task(self._guarded())
            loop.run_until_complete(self.rec.worker_task)
        finally:
            loop.close()
            if self.on_done:
                self.on_done(self.rec)

    async def _guarded(self) -> None:
        rec = self.rec
        wall = int(rec.budget.get("max_wall_time_seconds") or 1800)
        try:
            await asyncio.wait_for(self._run(), timeout=wall)
        except asyncio.TimeoutError:
            self._end("stopped", "max_wall_time", f"Stopped: max_wall_time_seconds ({wall}s) reached.")
        except asyncio.CancelledError:
            self._end("cancelled", "cancelled", "Cancelled by user.")
        except RouterStopped as e:
            budget = e.reason in ("run_budget_exhausted", "budget_exhausted", "provider_budget_exhausted")
            self._end("stopped", e.reason, ("Stopped by budget: " if budget else "Stopped by the model router: ")
                      + (e.detail[:400] or e.reason))
        except ContractInvalid as e:
            self._end("failed", "contract_invalid", str(e))
        except ReviewFailed as e:
            self._end("failed", "review_failed", str(e))
        except Exception as e:  # noqa: BLE001 - every failure becomes a reported terminal state
            self._end("failed", "internal_error", f"{type(e).__name__}: {e}"[:500])
        finally:
            rec.save()

    def _end(self, status: str, code: str, message: str) -> None:
        rec = self.rec
        for s in rec.stages:
            if s["status"] == "running":
                s["status"], s["finished_at"] = ("stopped" if status != "failed" else "failed"), now_iso()
            elif s["status"] == "pending":
                s["status"] = "skipped"
        rec.status, rec.error, rec.current_stage_id = status, {"error": code, "message": message}, None
        rec.emit("run_finished", None, status=status, reason=code, message=message)

    async def _run(self) -> None:
        rec = self.rec
        retries = int(rec.budget.get("max_retries_per_stage", 3))
        root = rec.workspace
        if self.source_workspace:  # follow-up: start from the source run's latest approved state
            shutil.copytree(self.source_workspace, root, dirs_exist_ok=True)
        root.mkdir(parents=True, exist_ok=True)
        (root / "contract").mkdir(exist_ok=True)
        rec.emit("run_created", None, prompt=rec.prompt[:2000], parent_run_id=rec.parent_run_id)

        # ---- plan + contract ----
        rec.set_status("planning")
        plan = rec.new_stage("planner", "plan_and_contract", [CONTRACT_PATH], [], 1)
        backend = rec.new_stage("backend", "backend_build", ["backend/**"], [plan["id"]], retries + 1)
        Frontend = self.frontend_cls
        frontend = rec.new_stage("frontend", "frontend_build", ["frontend/**"], [plan["id"]], retries + 1)
        reviewer = rec.new_stage("reviewer", "review_and_integrate", [], [backend["id"], frontend["id"]], retries + 1)
        rec.start_stage(plan, "Planning tasks and writing the API contract (OpenAPI 3.1)")
        existing = (root / CONTRACT_PATH).read_text(encoding="utf-8") if (root / CONTRACT_PATH).exists() else None
        try:
            arts = await PlannerAgent(rec.router).plan(rec.prompt, plan["id"], existing)
        except PlannerFailed as e:
            rec.contract_errors, rec.contract_updated_at = e.problems, now_iso()
            rec.finish_stage(plan, "failed", "planner output did not parse", checks_passed=0, checks_failed=len(e.problems))
            raise ContractInvalid(str(e)[:400]) from None
        self.plan = arts
        if not rec.project_name and arts.plan.project_name:
            rec.project_name = arts.plan.project_name[:100]  # the website no longer asks for a name
        write_artifacts(root, arts, CONTRACT_PATH)
        rec.contract_errors, rec.contract_updated_at = arts.problems, now_iso()
        if arts.problems:
            rec.finish_stage(plan, "failed", "plan or contract rejected by the validator", checks_passed=0,
                             checks_failed=len(arts.problems))
            raise ContractInvalid("; ".join(arts.problems)[:400])
        c = load_contract(root / CONTRACT_PATH)
        rec.set_status("contract")
        rec.emit("agent_message", plan["id"], agent="planner",
                 message=f"Plan validated: {len(arts.plan.tasks)} tasks in {len(arts.parallel_groups)} waves, "
                         f"{len(c.endpoints)} endpoints ({', '.join(f'{e.method} {e.path}' for e in c.endpoints)[:300]})")
        if arts.plan.open_questions:
            rec.emit("agent_message", plan["id"], agent="planner",
                     message="Assumed (open questions): " + "; ".join(arts.plan.open_questions)[:400])
        rec.finish_stage(plan, "passed", "plan and contract validated", checks_passed=1, checks_failed=0)
        git_init(root)
        checkpoint(rec, plan, f"Plan and API contract validated ({len(c.endpoints)} endpoints)", 1)
        self._check_cancel()

        # ---- build (backend || frontend) + review, with stage retries ----
        rec.set_status("building")
        be_fail: list[str] = []
        fe_fail: list[str] = []
        for attempt in range(1, retries + 2):
            reviewer["attempt"] = attempt
            # First attempt builds both sides in parallel; retries rebuild only the side(s) the Reviewer failed.
            jobs = []
            if attempt == 1 or be_fail:
                backend["attempt"] = attempt
                jobs.append(self._build_backend(backend, attempt, be_fail))
            if Frontend is not None and (attempt == 1 or fe_fail):
                frontend["attempt"] = attempt
                jobs.append(self._build_frontend(Frontend, frontend, attempt, fe_fail))
            elif Frontend is None and attempt == 1:
                frontend["status"] = "skipped"
                rec.emit("stage_finished", frontend["id"], name=frontend["name"], agent="frontend", status="skipped",
                         message="Frontend agent not installed on this server (engine.agents.frontend)")
            await asyncio.gather(*jobs)
            self._check_cancel()
            for st in (backend, frontend):
                if st["result"] and st["result"]["status"] == "contract_change_requested":
                    ccr = st["result"].get("contract_change") or {}
                    rec.change_requests.append({**ccr, "id": f"ccr_{rec.id}_{len(rec.change_requests)}", "run_id": rec.id,
                                                "stage_id": st["id"], "status": "pending", "created_at": now_iso()})
                    rec.emit("contract_change_requested", st["id"], **ccr)
                    raise ContractInvalid(f"{st['agent'].title()} agent requested a contract change: {ccr.get('problem', '')}"[:400])

            rec.set_status("verifying")
            report = await asyncio.to_thread(self._review, reviewer, c, Frontend is not None)
            if report["verdict"] == "approve":
                rec.set_status("checkpointing")
                what = "Backend and frontend" if Frontend is not None else "Backend"
                checkpoint(rec, reviewer, f"{what} approved on executed checks",
                           sum(ch["passed"] and not ch.get("skipped") for ch in report["checks"]), report)
                if Frontend is not None and self.visual_rounds > 0:
                    try:
                        await self._visual_polish(reviewer, frontend, Frontend, c)
                    except (RouterStopped, asyncio.CancelledError):
                        raise
                    except Exception as e:  # noqa: BLE001 - QA must never fail a verified build
                        rec.emit("agent_message", reviewer["id"], agent="reviewer", message=f"Visual QA stopped: {e}"[:300])
                if rec.options.get("enable_preview", True):
                    rec.preview = {"status": "building", "url": None, "health_check_passed": None, "error": None}
                    rec.preview = await asyncio.to_thread(self.preview.start, rec)
                    if rec.preview["status"] == "ready":
                        rec.emit("preview_ready", None, url=rec.preview["url"], health_check_passed=True)
                else:
                    rec.preview = {"status": "unavailable", "url": None, "health_check_passed": None, "error": "preview disabled for this run"}
                if rec.options.get("enable_deploy_agent"):  # off by default; never reached before the final approval
                    await self._deploy()
                rec.status, rec.current_stage_id = "done", None
                rec.emit("run_finished", None, status="done", message=f"Verified build, checkpoint {rec.checkpoints[-1]['git_tag']}")
                return
            failing = [ch for ch in report["checks"] if not ch["passed"] and ch.get("required", True)]
            text = lambda ch: f"{ch['name']}: {ch.get('log', '')[-2500:]}"  # noqa: E731
            # Integration failures concern both sides; everything else goes to its owner.
            be_fail = [text(ch) for ch in failing if ch["name"].startswith("backend:") or "served with backend" in ch["name"]]
            fe_fail = [text(ch) for ch in failing if ch["name"].startswith("frontend:")]
            if any(ch["name"].startswith("contract") for ch in failing):  # contract/types drift: the backend rebuilds
                be_fail += [text(ch) for ch in failing if ch["name"].startswith("contract")]
            for owner, notes in (await self._analyze(reviewer, report, failing)).items():
                (be_fail if owner == "backend" else fe_fail).extend(notes)
            if attempt <= retries:
                rec.set_status("building", note=f"review rejected; retry {attempt}/{retries}")
                continue

        # retries exhausted: restore the last approved checkpoint and report (FR-8/FR-9)
        cp = rec.checkpoints[-1]
        rec.set_status("rolling_back")
        rec.emit("rollback_started", None, checkpoint_id=cp["id"], git_tag=cp["git_tag"], message="review retries exhausted")
        await asyncio.to_thread(rollback_to, rec, cp)
        rec.emit("rollback_finished", None, checkpoint_id=cp["id"], git_tag=cp["git_tag"], message=f"workspace restored to {cp['git_tag']}")
        raise ReviewFailed(f"Reviewer rejected the build after {retries + 1} attempts; workspace restored to {cp['git_tag']}.")

    async def _build_backend(self, stage: dict, attempt: int, failures: list[str]) -> None:
        rec = self.rec
        rec.start_stage(stage, "Implementing the backend against contract/openapi.yaml" if attempt == 1
                        else f"Fixing Reviewer findings (attempt {attempt})")
        agent = BackendAgent(rec.router, self.runner)
        task = TaskAssignment(stage_id=stage["id"], agent="backend", contract_ref=CONTRACT_PATH,
                              instructions=self._instructions("backend"), failed_attempts=(attempt - 1) * 2,
                              reviewer_failures=failures)
        # BackendAgent runs blocking checks; it already lives in this worker thread, so the server loop stays free.
        result = await agent.run(task, rec.workspace)
        stage["result"] = result.to_contract()
        for ch in result.checks:
            rec.emit("stage_progress", stage["id"], name=stage["name"], message=f"self-check {ch.name}: {'passed' if ch.passed else 'FAILED'}")
        passed = sum(c.passed for c in result.checks)
        if result.status == "stopped":
            raise RouterStopped("budget_exhausted" if any("budget" in n for n in result.notes) else "no_eligible_model",
                                "; ".join(result.notes)[:400])
        rec.finish_stage(stage, "passed" if result.ok else "failed", "; ".join(result.notes)[-300:],
                         checks_passed=passed, checks_failed=len(result.checks) - passed)

    async def _build_frontend(self, Frontend, stage: dict, attempt: int, failures: list[str]) -> None:
        rec = self.rec
        rec.start_stage(stage, "Implementing the frontend against contract/openapi.yaml" if attempt == 1
                        else f"Fixing Reviewer findings (attempt {attempt})")

        def progress(phase: str, message: str) -> None:  # plan | implement | verify | repair | done | failed
            rec.emit("stage_progress", stage["id"], name=stage["name"], agent="frontend", phase=phase, message=message)

        result = await Frontend(rec.router, self.runner, on_progress=progress).run(
            TaskAssignment(stage_id=stage["id"], agent="frontend", contract_ref=CONTRACT_PATH, files_allowed=["frontend/**"],
                           instructions=self._instructions("frontend"), failed_attempts=(attempt - 1) * 2,
                           reviewer_failures=failures), rec.workspace)
        stage["result"] = result.to_contract()
        if result.status == "stopped":
            raise RouterStopped("budget_exhausted" if any("budget" in n for n in result.notes) else "no_eligible_model",
                                "; ".join(result.notes)[:400])
        passed = sum(c.passed and not c.skipped for c in result.checks)
        rec.finish_stage(stage, "passed" if result.ok else "failed", "; ".join(result.notes)[-300:],
                         checks_passed=passed, checks_failed=sum(not c.passed for c in result.checks))

    async def _deploy(self) -> None:
        rec = self.rec
        try:
            deploy_preconditions(rec, "github")
        except DeployRefused as e:  # requested but unavailable: say so, and keep the verified build
            stage = rec.new_stage("deploy", "deploy_publish", [], [rec.checkpoints[-1]["stage_id"]], 1)
            rec.finish_stage(stage, "skipped", f"Deployment requested but not run: {e}"[:300])
            return
        await deploy_final(rec, start_deployment(rec, "github"), factory=self.deploy_factory)

    async def _visual_polish(self, reviewer: dict, frontend: dict, Frontend, contract) -> None:
        """Open the approved app in a real browser (1440px and 390px), and send measured defects (layout, overflow,
        console/page errors, failing forms) to the Frontend agent. Every repaired build must pass the full
        deterministic review again and gets its own green checkpoint; a repair that breaks a check is rolled back to
        the last green checkpoint. The approval gate is never relaxed."""
        rec = self.rec
        inspect = self.visual_inspect
        if inspect is None:
            from engine.agents.visual import inspect_workspace as inspect
        for n in range(1, self.visual_rounds + 2):
            try:
                out = rec.workspace / ".agentforge" / "visual" / f"round{n}"
                rep = await asyncio.to_thread(inspect, rec.workspace, out)
            except Exception as e:  # noqa: BLE001 - no browser, app did not start: report, keep the verified build
                rec.emit("agent_message", reviewer["id"], agent="reviewer", message=f"Visual QA skipped: {e}"[:300])
                return
            defects = rep.defects()
            rec.visual = {"round": n, "defects": defects, "screenshots": [
                {"route": p.route, "viewport": p.viewport, "path": _rel(p.screenshot, rec.workspace)}
                for p in rep.pages], "forms": f"{rep.forms_ok}/{rep.forms_tried}"}
            rec.emit("stage_progress", reviewer["id"], name=reviewer["name"], agent="reviewer", phase="visual",
                     message=f"Visual QA round {n}: {len(defects)} defect(s) in {len(rep.pages)} screenshots",
                     defects=defects[:12])
            if not defects or n > self.visual_rounds:
                return
            if defects == getattr(self, "_last_visual", None):  # the last repair changed nothing measurable: stop
                rec.emit("agent_message", reviewer["id"], agent="reviewer",
                         message="Visual QA: the same defects remain after a repair; stopping to save credit.")
                return
            self._last_visual = defects
            failures = ["visual QA (the built app opened in Chrome at 1440px and 390px; these were measured, fix each):"
                        + "".join(f"\n- {d}" for d in defects[:15])]
            await self._build_frontend(Frontend, frontend, frontend["attempt"] + 1, failures)
            report = await asyncio.to_thread(self._review, reviewer, contract, True)
            if report["verdict"] != "approve":
                cp = rec.checkpoints[-1]
                await asyncio.to_thread(rollback_to, rec, cp)
                rec.emit("agent_message", reviewer["id"], agent="reviewer",
                         message=f"Visual repair broke a required check; restored the approved build {cp['git_tag']}.")
                await asyncio.to_thread(self._review, reviewer, contract, True)  # re-record the approved report
                return
            checkpoint(rec, reviewer, f"Visual QA round {n} fixes approved on executed checks",
                       sum(ch["passed"] and not ch.get("skipped") for ch in report["checks"]), report)

    def _instructions(self, owner: str) -> str:
        brief = tasks_brief(self.plan, owner) if getattr(self, "plan", None) else ""
        text = f"Implement the {owner} for: {self.rec.prompt}"
        return f"{text}\n\nPlanner tasks for the {owner} (from plan.json):\n{brief}" if brief else text

    def _review(self, stage: dict, contract, with_frontend: bool = False) -> dict:
        """Evidence over opinion: the AI2 Reviewer re-runs every check itself; the verdict is the check results."""
        rec = self.rec
        rec.start_stage(stage, "Re-running all checks independently")

        def bridge(ev: dict) -> None:  # AI2 Reviewer events -> platform RunEvents
            if ev["event"] == "review.started":
                rec.emit("review_started", stage["id"], message="contract and backend checks"
                         + ("; frontend checks, served with the backend" if with_frontend else ""))
            elif ev["event"] == "check.result":
                rec.emit("stage_progress", stage["id"], name=stage["name"], agent="reviewer",
                         message=f"{ev['name']}: {'passed' if ev['passed'] else 'FAILED'}{'' if ev['required'] else ' (advisory)'}")

        emitter = EventEmitter(run_id=rec.id, callback=bridge, strict_callback=False)
        report = reviewer_af.review(rec.workspace, contract, self.runner, stage["id"], int(stage.get("attempt") or 1),
                                    with_frontend, emitter)
        req = [c for c in report["checks"] if c["required"]]
        failed = [c for c in req if not c["passed"]]
        stage["review"] = rec.review = report
        rec.emit("review_finished", stage["id"], verdict=report["verdict"], checks_passed=len(req) - len(failed),
                 checks_failed=len(failed), summary=report["summary"])
        rec.finish_stage(stage, "passed" if report["verdict"] == "approve" else "failed", report["summary"],
                         checks_passed=len(req) - len(failed), checks_failed=len(failed))
        return report

    async def _analyze(self, stage: dict, report: dict, failing: list[dict]) -> dict[str, list[str]]:
        """Model-assisted root-cause analysis of a REJECTED review, for the builders' next attempt. It never changes
        the verdict; if it fails, the deterministic explanations already in the report stand."""
        rec = self.rec
        if not failing or os.environ.get("AF_REVIEW_ANALYSIS", "1") == "0":
            return {}
        checks = [InternalCheckResult(name=c["name"], passed=False, exit_code=c.get("exit_code"), log_tail=c.get("log") or "")
                  for c in failing]
        path = rec.workspace / CONTRACT_PATH
        contract = path.read_text(encoding="utf-8") if path.exists() else ""
        fixes, meta = await analyze_failures(rec.router, rec.workspace, checks, stage["id"], contract,
                                             previous_fixes=getattr(self, "_last_fixes", None))
        if meta["source"] != "model":
            if "model_error" in meta:
                rec.emit("agent_message", stage["id"], agent="reviewer",
                         message=f"Failure analysis unavailable: {meta['model_error'][:200]}")
            return {}
        texts = [reviewer_af.fix_text(f) for f in fixes]
        self._last_fixes = texts
        report["required_fixes"] = texts
        report["analysis"] = meta
        rec.emit("agent_message", stage["id"], agent="reviewer", message="Root cause: " + " | ".join(texts)[:600])
        out: dict[str, list[str]] = {}
        for f, t in zip(fixes, texts):
            for owner in (["backend", "frontend"] if f.owner in ("shared", "planner") else [f.owner]):
                out.setdefault(owner, []).append(f"Reviewer analysis: {t}")
        return out

    def _check_cancel(self) -> None:
        if self.rec.cancel_requested:
            raise asyncio.CancelledError()


class ContractInvalid(Exception):
    pass


class ReviewFailed(Exception):
    pass
