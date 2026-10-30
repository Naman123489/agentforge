"""Control Plane API end to end: real pipeline thread, sandbox checks, git checkpoints and uvicorn preview, with a
scripted model. Every response body is validated against contracts/agentforge-openapi.yaml."""

import io
import json
import os
import subprocess
import time
import zipfile
from pathlib import Path

import pytest

os.environ["AF_SERVER_NO_DEFAULT_APP"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from engine.agents.runner import LocalRunner  # noqa: E402
from engine.router import AccountLedger, ModelRouter, RouterConfig  # noqa: E402
from server.app import create_app  # noqa: E402
from tests.fakes import FakeClient, ok, planner_reply  # noqa: E402
from tests.test_backend_agent import AUTH_HOLE, GOOD, LIGHTNING  # noqa: E402
from tests.test_contract_conformance import EVENT_TYPES, validate  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_YAML = (ROOT / "examples" / "arcade_openapi.yaml").read_text(encoding="utf-8")
PLAN = ok(planner_reply(CONTRACT_YAML), 2000, 3000)
H = {"Authorization": "Bearer t0ken-for-tests"}
PROMPT = "Build an arcade backend with accounts, scores and a leaderboard."


def make_client(tmp_path, *responses, default=None, frontend_agent=None, runner=None):
    """Backend-only pipeline by default; frontend tests pass frontend_agent explicitly."""
    cfg = RouterConfig.load("config/profiles/web-light.toml", env={})
    fake = FakeClient(default=default)
    fake.script(LIGHTNING, *responses)
    base = ModelRouter(cfg, fake, account=AccountLedger(cfg))
    app = create_app(base_router=base, data_dir=tmp_path / "runs", runner=runner or LocalRunner(), token="t0ken-for-tests",
                     auth=True, serve_web=False, frontend_agent=frontend_agent)
    return TestClient(app), app, fake


def wait_terminal(c, run_id, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get(f"/api/v1/runs/{run_id}", headers=H).json()
        if r["status"] in ("done", "failed", "cancelled", "stopped"):
            return r
        time.sleep(0.3)
    raise AssertionError(f"run {run_id} did not finish: {r['status']}")


def sse_events(c, run_id, last_id=None):
    hdr = {**H, **({"Last-Event-ID": last_id} if last_id else {})}
    out = []
    with c.stream("GET", f"/api/v1/runs/{run_id}/events", headers=hdr) as resp:
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


@pytest.fixture
def done_run(tmp_path):
    c, app, fake = make_client(tmp_path, PLAN, ok(GOOD, 3000, 2500))
    r = c.post("/api/v1/runs", json={"prompt": PROMPT, "project_name": "arcade"}, headers={**H, "Idempotency-Key": "idem-key-0001"})
    assert r.status_code == 202
    validate("RunDetail", r.json())
    final = wait_terminal(c, r.json()["id"])
    yield c, app, final
    app.state.preview.stop_all()


def test_auth_validation_and_errors(tmp_path):
    c, app, _ = make_client(tmp_path)
    h = c.get("/api/v1/health")  # the only unauthenticated endpoint
    assert h.status_code == 200
    validate("HealthResponse", h.json())
    r = c.get("/api/v1/runs")
    assert r.status_code == 401
    validate("ErrorResponse", r.json())
    r = c.post("/api/v1/runs", json={"prompt": "hi"}, headers=H)
    assert r.status_code == 422
    validate("ValidationErrorResponse", r.json())
    assert r.json()["issues"][0]["path"] == ["prompt"]
    r = c.post("/api/v1/runs", json={"prompt": "Build a thing please", "surprise": 1}, headers=H)
    assert r.status_code == 422  # additionalProperties: false
    r = c.get("/api/v1/runs/run_nope", headers=H)
    assert r.status_code == 404
    validate("ErrorResponse", r.json())
    m = c.get("/api/v1/models", headers=H).json()
    for x in m["models"]:
        validate("ModelStatus", x)


def test_full_run_end_to_end(done_run):
    c, app, final = done_run
    rid = final["id"]
    validate("RunDetail", final)
    assert final["status"] == "done", final["error"]
    st = {s["agent"]: s for s in c.get(f"/api/v1/runs/{rid}/stages", headers=H).json()["stages"]}
    assert st["planner"]["status"] == "passed" and st["backend"]["status"] == "passed"
    assert st["frontend"]["status"] == "skipped" and st["reviewer"]["status"] == "passed"
    for s in st.values():
        validate("Stage", s)
    validate("BuildResult", st["backend"]["result"])

    review = c.get(f"/api/v1/runs/{rid}/review", headers=H).json()
    validate("ReviewReport", review)
    assert review["verdict"] == "approve" and {"backend: pytest", "backend: contract test"} <= {x["name"] for x in review["checks"]}

    cps = c.get(f"/api/v1/runs/{rid}/checkpoints", headers=H).json()["checkpoints"]
    # Reviewer convention: one annotated stage-{stage_id}-green tag per approved stage (planner, then reviewer).
    assert [x["git_tag"] for x in cps] == [f"stage-{x['stage_id']}-green" for x in cps]
    assert [x["stage_id"] for x in cps] == [st["planner"]["id"], st["reviewer"]["id"]]
    ws = app.state.runs.get(rid).workspace
    for x in cps:
        validate("Checkpoint", x)
        assert subprocess.run(["git", "cat-file", "-t", x["git_tag"]], cwd=ws, capture_output=True, text=True).stdout.strip() == "tag"
    for name in ("plan.json", "types.ts", "task_assignments.json", "contract/openapi.yaml"):  # Planner artifacts
        assert (ws / name).is_file(), name

    u = c.get(f"/api/v1/runs/{rid}/usage", headers=H).json()
    validate("UsageSummary", u)
    assert u["total_tokens"] == 10500 and u["confirmed_cost_usd"] is None

    contract = c.get(f"/api/v1/runs/{rid}/contracts", headers=H).json()
    validate("ContractArtifact", contract)
    assert contract["valid"] and "/api/scores" in contract["content"]

    p = c.get(f"/api/v1/runs/{rid}/preview", headers=H).json()
    validate("PreviewInfo", p)
    assert p["status"] == "ready" and p["health_check_passed"] is True and p["url"].endswith("/docs")

    # idempotent retry returns the same run
    again = c.post("/api/v1/runs", json={"prompt": PROMPT, "project_name": "arcade"}, headers={**H, "Idempotency-Key": "idem-key-0001"})
    assert again.json()["id"] == rid


def test_events_stream_and_resume(done_run):
    c, _, final = done_run
    evs = sse_events(c, final["id"])
    for e in evs:
        validate("RunEvent", e)
    types = [e["type"] for e in evs]
    assert set(types) <= EVENT_TYPES
    assert types[0] == "run_created" and types[-1] == "run_finished"
    assert {"route_decision", "model_call_ok", "stage_started", "review_finished", "checkpoint_created", "preview_ready"} <= set(types)
    assert [e["id"] for e in evs] == sorted(e["id"] for e in evs)  # ids increase: Last-Event-ID is meaningful
    tail = sse_events(c, final["id"], last_id=evs[-4]["id"])
    assert [e["id"] for e in tail] == [e["id"] for e in evs[-3:]]


def test_files_download_and_path_safety(done_run):
    c, _, final = done_run
    rid = final["id"]
    tree = c.get(f"/api/v1/runs/{rid}/files", headers=H).json()
    validate("FileTreeResponse", tree)
    paths = {e["path"] for e in tree["entries"]}
    assert {"backend/app.py", "contract/openapi.yaml"} <= paths and not any(".git" in x.split("/") for x in paths)
    f = c.get(f"/api/v1/runs/{rid}/files/content", params={"path": "backend/app.py"}, headers=H).json()
    validate("FileContent", f)
    assert "FastAPI" in f["content"]
    for bad in ("../../etc/passwd", "/etc/passwd", ".git/config", "C:/Windows/win.ini", "backend\\app.py"):
        r = c.get(f"/api/v1/runs/{rid}/files/content", params={"path": bad}, headers=H)
        assert r.status_code == 400, bad
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/v1/runs/{rid}/download", headers=H).content))
    assert "backend/app.py" in z.namelist() and not any(n.startswith(".git/") for n in z.namelist())


def test_rollback_cancel_deploy_conflicts(done_run):
    c, _, final = done_run
    rid = final["id"]
    assert c.delete(f"/api/v1/runs/{rid}", headers=H).status_code == 409
    assert c.post(f"/api/v1/runs/{rid}/deployments", json={"target": "nebius_serverless"}, headers=H).status_code == 409
    assert c.get(f"/api/v1/runs/{rid}/deployments/latest", headers=H).status_code == 404
    cp0 = c.get(f"/api/v1/runs/{rid}/checkpoints", headers=H).json()["checkpoints"][0]
    r = c.post(f"/api/v1/runs/{rid}/rollback", json={"checkpoint_id": cp0["id"], "reason": "test"}, headers=H)
    assert r.status_code == 202
    validate("ActionAccepted", r.json())
    for _ in range(50):
        if not c.get(f"/api/v1/runs/{rid}/files/content", params={"path": "backend/app.py"}, headers=H).status_code == 200:
            break
        time.sleep(0.1)
    assert c.get(f"/api/v1/runs/{rid}/files/content", params={"path": "backend/app.py"}, headers=H).status_code == 404
    types = [e["type"] for e in sse_events(c, rid)]
    assert "rollback_started" in types


def test_rejected_build_rolls_back_and_fails_with_reason(tmp_path):
    c, app, _ = make_client(tmp_path, PLAN, default=ok(AUTH_HOLE))
    r = c.post("/api/v1/runs", json={"prompt": PROMPT, "budget": {"max_retries_per_stage": 0}}, headers=H).json()
    final = wait_terminal(c, r["id"])
    validate("RunDetail", final)
    assert final["status"] == "failed" and final["error"]["error"] == "review_failed"
    assert "plan_and_contract-green" in final["error"]["message"]  # restored to the planner's checkpoint
    cps = c.get(f"/api/v1/runs/{r['id']}/checkpoints", headers=H).json()["checkpoints"]
    assert len(cps) == 1  # the rejected review produced no green checkpoint
    review = c.get(f"/api/v1/runs/{r['id']}/review", headers=H).json()
    assert review["verdict"] == "reject" and review["required_fixes"]
    types = [e["type"] for e in sse_events(c, r["id"])]
    assert "rollback_finished" in types and types[-1] == "run_finished"


def test_budget_stop_is_reported(tmp_path):
    c, app, _ = make_client(tmp_path, PLAN, ok(GOOD, 3000, 2500))
    r = c.post("/api/v1/runs", json={"prompt": PROMPT, "budget": {"max_tokens": 6000}}, headers=H).json()
    final = wait_terminal(c, r["id"])
    assert final["status"] == "stopped" and "budget" in final["error"]["error"]
    assert final["error"]["message"].startswith("Stopped by budget")
    assert "budget_exhausted" in [e["type"] for e in sse_events(c, r["id"])]


def test_followup_starts_from_checkpoint(done_run, tmp_path):
    c, app, final = done_run
    fake = app.state.base_router.client
    fake.script(LIGHTNING, PLAN, ok(GOOD))
    r = c.post(f"/api/v1/runs/{final['id']}/followups", json={"prompt": "Add a Tic-Tac-Toe game"}, headers=H)
    assert r.status_code == 202
    nxt = wait_terminal(c, r.json()["id"])
    assert nxt["status"] == "done", nxt["error"]
    assert "Follow-up request: Add a Tic-Tac-Toe game" in nxt["prompt"]
    planner_call = next(x for x in fake.calls[::-1] if "[agent:planner]" in x["messages"][0]["content"])
    assert "FOLLOW-UP" in planner_call["messages"][-1]["content"]  # planner saw the existing contract
    app.state.preview.stop_all()
