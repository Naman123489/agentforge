"""AgentForge Control Plane API (contracts/agentforge-openapi.yaml), FastAPI.

    uvicorn server.app:app --port 8000          # dashboard dev server proxies /api here
    AF_PLATFORM_TOKEN=...   bearer token clients must send (generated and printed if unset)
    AF_AUTH=off             disable auth for local development only
    AF_ROUTER_CONFIG=...    model profile (see docs/MODEL_ROUTER.md); NEBIUS_API_KEY stays server-side
    AF_MAX_CONCURRENT_RUNS  default 2

If agentforge/web/dist exists (npm run build), the dashboard is served from "/" by the same process.
"""

from __future__ import annotations

import asyncio
import threading
import base64
import io
import json
import logging
import os
import secrets
import subprocess
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

import yaml

from fastapi import APIRouter, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from engine.agents.runner import default_runner
from engine.router import AccountLedger, ModelRouter, RouterConfig

from engine.agents import deploy_platform
from engine.agents.deploy import redact

from .pipeline import (
    CONTRACT_PATH,
    DeployRefused,
    Pipeline,
    PreviewManager,
    deploy_final,
    deploy_preconditions,
    rollback_to,
    start_deployment,
)
from . import planner
from .state import TERMINAL, RunRecord, new_id, now_iso

log = logging.getLogger("agentforge.server")
ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
MAX_FILE_BYTES = 512_000


# ---- request schemas (contract components.schemas) -------------------------------------------------
class RunBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_tokens: int = Field(200_000, ge=1, le=5_000_000)
    max_cost_usd: float = Field(2, ge=0, le=1000)
    max_premium_cost_usd: float = Field(1, ge=0, le=1000)
    max_wall_time_seconds: int = Field(1800, ge=30, le=86_400)
    max_retries_per_stage: int = Field(3, ge=0, le=10)


class RunOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable_deploy_agent: bool = False
    enable_preview: bool = True
    run_e2e_tests: bool = True
    visibility: Literal["private", "team"] = "private"


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=5, max_length=20_000)
    project_name: str | None = Field(None, min_length=1, max_length=100)
    examples: list[str] = Field(default_factory=list, max_length=10)
    budget: RunBudget = Field(default_factory=RunBudget)
    options: RunOptions = Field(default_factory=RunOptions)


class FollowupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=3, max_length=20_000)
    from_checkpoint_id: str | None = None
    budget: RunBudget = Field(default_factory=RunBudget)


class RollbackRequest(BaseModel):
    checkpoint_id: str
    reason: str | None = Field(None, max_length=2000)
    retry_after_rollback: bool = False


class PutContractRequest(BaseModel):
    content: str = Field(min_length=1)
    format: Literal["yaml", "json"] = "yaml"


class ContractChangeRequestBody(BaseModel):
    problem: str = Field(min_length=1)
    proposed_change: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    affected_paths: list[str] = Field(default_factory=list)
    breaking_change: bool = False


class DeploymentRequest(BaseModel):
    # "github" (added 2026-10-30): publish the final approved checkpoint to GitHub without a live deployment.
    target: Literal["github", "nebius_serverless", "configured_target"]
    health_check_path: str = Field(default="/health", pattern=r"^/([^/\s][^\s]{0,198})?$")
    rollback_on_failure: bool = True
    repo_name: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._ -]+$")
    visibility: Literal["private", "public"] = "private"

    @field_validator("health_check_path")
    @classmethod
    def _no_traversal(cls, v: str) -> str:
        if ".." in v:
            raise ValueError("health_check_path must not contain '..'")
        return v


# ---- errors (ErrorResponse / ValidationErrorResponse) ----------------------------------------------
class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str, headers: dict[str, str] | None = None):
        self.status, self.error, self.message, self.headers = status, error, message, headers


def error_body(error: str, message: str, request: Request | None = None) -> dict:
    return {"error": error, "message": message, "request_id": getattr(getattr(request, "state", None), "request_id", None)}


def public_deployment(dep: dict) -> dict:
    """Contract DeploymentStatus (no internal bookkeeping such as stage_id)."""
    keys = ("id", "status", "target", "url", "health_check_passed", "error", "created_at", "checkpoint_id", "git_tag",
            "repo_url", "commit_sha", "source_commit", "notes", "checks")
    return {k: dep.get(k) for k in keys}


def create_app(base_router: ModelRouter | None = None, data_dir: Path | None = None, runner=None,
               token: str | None = None, auth: bool | None = None, serve_web: bool = True,
               frontend_agent: object = "auto", deploy_factory=None) -> FastAPI:
    data_dir = Path(data_dir or ROOT / "data" / "runs")
    data_dir.mkdir(parents=True, exist_ok=True)
    if base_router is None:
        cfg = RouterConfig.load()
        base_router = ModelRouter(cfg, account=AccountLedger(cfg, ROOT / "data" / "ledger.json"))
    runner = runner or default_runner()
    auth = (os.environ.get("AF_AUTH", "on").lower() != "off") if auth is None else auth
    token = token or os.environ.get("AF_PLATFORM_TOKEN") or ""
    token_file = data_dir.parent / "platform_token.txt"
    if auth and not token and token_file.exists():  # keep the same dev token across restarts
        token = token_file.read_text(encoding="utf-8").strip()
    if auth and not token:
        token = secrets.token_urlsafe(24)
        token_file.write_text(token, encoding="utf-8")
        log.warning("AF_PLATFORM_TOKEN not set: generated one (also in data/platform_token.txt). Paste it in the dashboard Settings.")
        print(f"\n  AgentForge platform token: {token}\n  (dashboard -> Settings -> Platform access token)\n", flush=True)
    max_runs = int(os.environ.get("AF_MAX_CONCURRENT_RUNS", "2"))
    preview = PreviewManager()
    runs: dict[str, RunRecord] = {}
    idem: dict[str, str] = {}

    for d in sorted(data_dir.iterdir()) if data_dir.exists() else []:  # history survives restarts
        if (d / "run.json").exists():
            try:
                rec = RunRecord.load(d)
                runs[rec.id] = rec
            except (OSError, ValueError, TypeError) as e:
                log.warning("skipping unreadable run %s: %s", d.name, e)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        preview.stop_all()  # shutdown: stop preview processes and cancel in-flight runs
        for r in runs.values():
            if r.status not in TERMINAL and r.worker_loop and r.worker_task:
                r.worker_loop.call_soon_threadsafe(r.worker_task.cancel)

    app = FastAPI(title="AgentForge Control Plane API", version=VERSION, lifespan=lifespan)
    app.state.runs, app.state.base_router, app.state.preview = runs, base_router, preview
    api = APIRouter(prefix="/api/v1")

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, e: ApiError):
        return JSONResponse(error_body(e.error, e.message, request), status_code=e.status, headers=e.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, e: RequestValidationError):
        issues = [{"path": [p for p in err["loc"] if p != "body"], "message": err["msg"], "code": err["type"]} for err in e.errors()]
        return JSONResponse({"error": "validation_error", "message": "Request validation failed.", "issues": issues}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, e: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "http_error")
        return JSONResponse(error_body(code, str(e.detail), request), status_code=e.status_code)

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        request.state.request_id = new_id("req")
        p = request.url.path
        if auth and p.startswith("/api/v1") and p != "/api/v1/health":
            if request.headers.get("authorization", "") != f"Bearer {token}":
                return JSONResponse(error_body("unauthorized", "Missing or invalid bearer token.", request), status_code=401,
                                    headers={"WWW-Authenticate": "Bearer"})
        return await call_next(request)

    def get_run(run_id: str) -> RunRecord:
        rec = runs.get(run_id)
        if not rec:
            raise ApiError(404, "not_found", f"Run {run_id} not found.")
        return rec

    def active() -> int:
        return sum(r.status not in TERMINAL for r in runs.values())

    def start(rec: RunRecord, source: Path | None = None) -> None:
        rec.router = base_router.for_run(rec.id, on_event=rec.append, budget=rec.budget)
        rec.workspace.parent.mkdir(parents=True, exist_ok=True)
        rec.save()
        Pipeline(rec, preview, runner, source_workspace=source, on_done=lambda r: r.save(),
                 frontend_agent=frontend_agent, deploy_factory=deploy_factory).start_thread()

    # ---- health / models ------------------------------------------------------------------------
    @api.get("/health")
    def health():
        models = base_router.model_statuses()
        usable = [m for m in models if m["enabled"] and m["health"] != "unavailable"]
        deps = {"model_router": "ok" if len(usable) == len(models) else ("degraded" if usable else "unavailable"),
                "sandbox": "ok" if runner.name == "docker" else "degraded",  # local runner: no container isolation
                "git": "ok", "deploy": "ok" if deploy_platform.capability()["github_ready"] else "unavailable"}
        return {"status": "ok" if deps["model_router"] != "unavailable" else "degraded", "version": VERSION, "dependencies": deps}

    @api.get("/models")
    def models():
        return {"models": base_router.model_statuses()}

    # ---- runs ------------------------------------------------------------------------------------
    @api.post("/runs", status_code=202)
    def create_run(body: CreateRunRequest, idempotency_key: Annotated[str | None, Header(min_length=8, max_length=128)] = None):
        if idempotency_key and idempotency_key in idem:
            return runs[idem[idempotency_key]].detail()  # retry of an accepted request: same run
        if active() >= max_runs:
            raise ApiError(429, "rate_limited", f"{max_runs} runs are already in progress.", {"Retry-After": "30"})
        rid = new_id("run")
        rec = RunRecord(id=rid, prompt=body.prompt.strip(), project_name=body.project_name, workspace=data_dir / rid / "project",
                        budget=body.budget.model_dump(), options=body.options.model_dump())
        runs[rid] = rec
        if idempotency_key:
            idem[idempotency_key] = rid
        start(rec)
        return rec.detail()

    @api.get("/runs")
    def list_runs(status: str | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 20, cursor: str | None = None):
        items = sorted(runs.values(), key=lambda r: r.created_at, reverse=True)
        if status:
            items = [r for r in items if r.status == status]
        off = int(cursor) if cursor and cursor.isdigit() else 0
        page = items[off:off + limit]
        return {"items": [r.detail() for r in page], "next_cursor": str(off + limit) if off + limit < len(items) else None}

    @api.get("/runs/{run_id}")
    def get_run_detail(run_id: str):
        return get_run(run_id).detail()

    @api.delete("/runs/{run_id}", status_code=202)
    def cancel_run(run_id: str):
        rec = get_run(run_id)
        if rec.status in TERMINAL:
            raise ApiError(409, "conflict", f"Run already finished ({rec.status}).")
        rec.cancel_requested = True
        if rec.worker_loop and rec.worker_task:
            rec.worker_loop.call_soon_threadsafe(rec.worker_task.cancel)
        return {"accepted": True, "message": "Cancellation requested; the run stops at the next safe point.", "operation_id": None}

    @api.post("/runs/{run_id}/followups", status_code=202)
    def followup(run_id: str, body: FollowupRequest, idempotency_key: Annotated[str | None, Header(min_length=8, max_length=128)] = None):
        src = get_run(run_id)
        if idempotency_key and idempotency_key in idem:
            return runs[idem[idempotency_key]].detail()
        if src.status not in TERMINAL:
            raise ApiError(409, "conflict", "The source run is still in progress.")
        cp = next((c for c in src.checkpoints if c["id"] == body.from_checkpoint_id), None) if body.from_checkpoint_id \
            else (src.checkpoints[-1] if src.checkpoints else None)
        if not cp:
            raise ApiError(409, "conflict", "The source run has no approved checkpoint to continue from.")
        if active() >= max_runs:
            raise ApiError(429, "rate_limited", f"{max_runs} runs are already in progress.", {"Retry-After": "30"})
        rid = new_id("run")
        rec = RunRecord(id=rid, prompt=f"{src.prompt}\n\nFollow-up request: {body.prompt.strip()}", project_name=src.project_name,
                        workspace=data_dir / rid / "project", budget=body.budget.model_dump(), options=src.options,
                        parent_run_id=src.id)
        runs[rid] = rec
        if idempotency_key:
            idem[idempotency_key] = rid
        # Exact files of the approved checkpoint, without the source repo's git metadata.
        snapshot = data_dir / rid / "source"
        archive = subprocess.run(["git", "archive", "--format=zip", cp["git_tag"]], cwd=src.workspace, capture_output=True,
                                 timeout=60, check=True).stdout
        zipfile.ZipFile(io.BytesIO(archive)).extractall(snapshot)
        start(rec, source=snapshot)
        return rec.detail()

    # ---- events (SSE) -----------------------------------------------------------------------------
    @api.get("/runs/{run_id}/events")
    async def events(run_id: str, request: Request, last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None):
        rec = get_run(run_id)
        loop = asyncio.get_running_loop()
        backlog, q = rec.subscribe(loop, last_event_id)

        def frame(ev: dict) -> str:
            return f"id: {ev['id']}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"

        async def gen():
            try:
                yield "retry: 2000\n\n"
                for ev in backlog:
                    yield frame(ev)
                if rec.status in TERMINAL:  # finished: send whatever arrived since the snapshot, then close
                    while not q.empty():
                        yield frame(q.get_nowait())
                    return
                while not await request.is_disconnected():
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield frame(ev)
                    if ev["type"] == "run_finished":
                        return
            finally:
                rec.unsubscribe(loop, q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- stages / contract / review / usage / preview ----------------------------------------------
    @api.get("/runs/{run_id}/stages")
    def stages(run_id: str):
        return {"run_id": run_id, "stages": get_run(run_id).stages}

    @api.get("/runs/{run_id}/contracts")
    def get_contract(run_id: str):
        rec = get_run(run_id)
        p = rec.workspace / CONTRACT_PATH
        if not p.exists():
            raise ApiError(404, "not_found", "The Planner has not written the contract yet.")
        return {"run_id": rec.id, "path": CONTRACT_PATH, "openapi_version": "3.1.0", "valid": not rec.contract_errors,
                "validation_errors": rec.contract_errors, "content": p.read_text(encoding="utf-8"),
                "updated_at": rec.contract_updated_at or rec.updated_at}

    @api.put("/runs/{run_id}/contracts")
    def put_contract(run_id: str, body: PutContractRequest):
        rec = get_run(run_id)
        if rec.status not in ("created", "planning") or any(s["agent"] in ("backend", "frontend") and s["status"] != "pending" for s in rec.stages):
            raise ApiError(409, "conflict", "The build has started; use a ContractChangeRequest instead.")
        text = body.content if body.format == "yaml" else yaml.safe_dump(json.loads(body.content), sort_keys=False)
        problems = planner.validate(text)
        if problems:
            return JSONResponse({"error": "validation_error", "message": "Contract is invalid.",
                                 "issues": [{"path": ["content"], "message": p} for p in problems]}, status_code=422)
        (rec.workspace / "contract").mkdir(parents=True, exist_ok=True)
        (rec.workspace / CONTRACT_PATH).write_text(text, encoding="utf-8")
        rec.contract_errors, rec.contract_updated_at = [], now_iso()
        return get_contract(run_id)

    @api.post("/runs/{run_id}/stages/{stage_id}/contract-change-requests", status_code=201)
    def change_request(run_id: str, stage_id: str, body: ContractChangeRequestBody):
        rec = get_run(run_id)
        if not any(s["id"] == stage_id for s in rec.stages):
            raise ApiError(404, "not_found", f"Stage {stage_id} not found.")
        record = {**body.model_dump(), "id": new_id("ccr"), "run_id": rec.id, "stage_id": stage_id, "status": "pending",
                  "created_at": now_iso()}
        rec.change_requests.append(record)
        rec.emit("contract_change_requested", stage_id, **body.model_dump())
        return record

    @api.get("/runs/{run_id}/review")
    def review(run_id: str):
        rec = get_run(run_id)
        if not rec.review:
            raise ApiError(404, "not_found", "No review report yet.")
        return rec.review

    @api.get("/runs/{run_id}/usage")
    def usage(run_id: str):
        return get_run(run_id).usage()

    @api.get("/runs/{run_id}/preview")
    def preview_info(run_id: str):
        return get_run(run_id).preview

    # ---- files ------------------------------------------------------------------------------------
    def safe(rec: RunRecord, rel: str) -> Path:
        root = rec.workspace.resolve()
        if not rel:
            return root
        if rel.startswith(("/", "\\")) or "\\" in rel or ":" in rel or any(seg in ("..", ".git") for seg in rel.split("/")):
            raise ApiError(400, "bad_request", "Path must be relative to the run workspace.")
        p = (root / rel).resolve()
        if not p.is_relative_to(root):
            raise ApiError(400, "bad_request", "Path escapes the run workspace.")
        return p

    def visible(p: Path, root: Path) -> bool:
        parts = p.relative_to(root).parts
        return not any(x in (".git", "__pycache__", ".pytest_cache", "node_modules") for x in parts) and p.suffix not in (".db", ".pyc")

    @api.get("/runs/{run_id}/files")
    def files(run_id: str, path: str = ""):
        rec = get_run(run_id)
        base, root = safe(rec, path), rec.workspace.resolve()
        if not base.exists():
            raise ApiError(404, "not_found", "No such directory in the run workspace.")
        entries = []
        for p in sorted(base.rglob("*")):
            if visible(p, root):
                st = p.stat()
                entries.append({"path": p.relative_to(root).as_posix(), "kind": "directory" if p.is_dir() else "file",
                                **({"size_bytes": st.st_size} if p.is_file() else {}),
                                "modified_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")})
        return {"run_id": rec.id, "root": "project", "entries": entries}

    @api.get("/runs/{run_id}/files/content")
    def file_content(run_id: str, path: Annotated[str, Query(min_length=1)]):
        rec = get_run(run_id)
        p = safe(rec, path)
        if not p.is_file() or not visible(p, rec.workspace.resolve()):
            raise ApiError(404, "not_found", f"No file {path}.")
        raw = p.read_bytes()
        truncated = len(raw) > MAX_FILE_BYTES
        raw = raw[:MAX_FILE_BYTES]
        try:
            return {"path": path, "content": raw.decode("utf-8"), "encoding": "utf-8", "truncated": truncated}
        except UnicodeDecodeError:

            return {"path": path, "content": base64.b64encode(raw).decode(), "encoding": "base64", "truncated": truncated}

    @api.get("/runs/{run_id}/download")
    def download(run_id: str):
        rec = get_run(run_id)
        root = rec.workspace.resolve()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(root.rglob("*")):
                if p.is_file() and visible(p, root) and ".agentforge" not in p.parts:
                    z.write(p, p.relative_to(root).as_posix())
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{rec.project_name or rec.id}.zip"'})

    # ---- checkpoints / rollback ---------------------------------------------------------------------
    @api.get("/runs/{run_id}/checkpoints")
    def checkpoints(run_id: str):
        return {"run_id": run_id, "checkpoints": get_run(run_id).checkpoints}

    @api.post("/runs/{run_id}/rollback", status_code=202)
    async def rollback(run_id: str, body: RollbackRequest):
        rec = get_run(run_id)
        cp = next((c for c in rec.checkpoints if c["id"] == body.checkpoint_id), None)
        if not cp:
            return JSONResponse({"error": "validation_error", "message": "Unknown checkpoint.",
                                 "issues": [{"path": ["checkpoint_id"], "message": "not a checkpoint of this run"}]}, status_code=422)
        if rec.status not in TERMINAL:
            raise ApiError(409, "conflict", "Cancel or wait for the run to finish before rolling back.")
        if body.retry_after_rollback:
            raise ApiError(409, "conflict", "retry_after_rollback is not supported yet; start a follow-up from the checkpoint instead.")
        previous = rec.status
        rec.status = "rolling_back"
        rec.emit("rollback_started", None, checkpoint_id=cp["id"], git_tag=cp["git_tag"], message=body.reason or "")

        async def do():
            try:
                await asyncio.to_thread(rollback_to, rec, cp)
                rec.emit("rollback_finished", None, checkpoint_id=cp["id"], git_tag=cp["git_tag"], message=f"workspace restored to {cp['git_tag']}")
            except Exception as e:  # noqa: BLE001
                rec.emit("error", None, source="checkpoint_service", reason="rollback_failed", detail=str(e)[:300])
            finally:
                rec.status = previous
                rec.save()

        asyncio.get_running_loop().create_task(do())
        return {"accepted": True, "message": f"Rollback to {cp['git_tag']} accepted.", "operation_id": None}

    # ---- deployments: optional, after the final Reviewer approval; GitHub publishing, live targets not available ----
    @api.post("/runs/{run_id}/deployments", status_code=202)
    async def deploy(run_id: str, body: DeploymentRequest):
        rec = get_run(run_id)
        if rec.status != "done":
            raise ApiError(409, "conflict", f"The run is {rec.status}; only a finished, verified run can be deployed.")
        try:
            deploy_preconditions(rec, body.target)
        except DeployRefused as e:
            raise ApiError(409, "conflict", str(e)) from None
        dep = start_deployment(rec, body.target)

        async def do() -> None:
            try:
                await deploy_final(rec, dep, repo_name=body.repo_name, visibility=body.visibility,
                                   health_check_path=body.health_check_path,
                                   rollback_on_failure=body.rollback_on_failure, factory=deploy_factory)
            except Exception as e:  # noqa: BLE001 - never leave a deployment stuck in "queued"
                dep.update(status="failed", error=redact(f"{type(e).__name__}: {e}")[:300])
                rec.emit("deployment_status", dep.get("stage_id"), id=dep["id"], status="failed", error=dep["error"])
            finally:
                rec.save()

        # Own worker thread + loop, like a run: independent of the request's loop and never garbage-collected.
        threading.Thread(target=lambda: asyncio.run(do()), name=f"deploy-{rec.id}", daemon=True).start()
        return public_deployment(dep)

    @api.get("/runs/{run_id}/deployments/latest")
    def latest_deployment(run_id: str):
        rec = get_run(run_id)
        if not rec.deployments:
            raise ApiError(404, "not_found", "No deployments for this run.")
        return public_deployment(rec.deployments[-1])

    app.include_router(api)


    dist = ROOT / "web" / "dist"
    if serve_web and dist.is_dir():  # single-process deploy: the dashboard and its API on one origin
        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            f = (dist / full_path).resolve()
            if full_path and f.is_file() and f.is_relative_to(dist.resolve()):
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app


def _make_default_app() -> FastAPI:
    from engine.router.config import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    return create_app()


app = _make_default_app() if os.environ.get("AF_SERVER_NO_DEFAULT_APP") != "1" else None
