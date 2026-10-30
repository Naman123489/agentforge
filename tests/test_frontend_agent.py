"""Frontend agent: offline tests with a scripted model and a fake npm/tsc/vite toolchain (python checks run for real).

Real toolchain test (npm install + tsc + vite build + served by the backend): AF_REAL_TOOLCHAIN=1 pytest -k real_toolchain
"""

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ["AF_SERVER_NO_DEFAULT_APP"] = "1"

from engine.agents.contract import load_contract  # noqa: E402
from engine.agents.frontend import FrontendAgent  # noqa: E402
from engine.agents.frontend_context import GENERATED_PATH, generate_client, op_name  # noqa: E402
from engine.agents.runner import ExecResult, LocalRunner  # noqa: E402
from engine.router import ModelRouter, RouterConfig  # noqa: E402
from engine.schemas import CheckResult, TaskAssignment  # noqa: E402
from tests.fakes import FakeAPIError, FakeClient, ok, planner_reply  # noqa: E402
from tests.test_backend_agent import GOOD as GOOD_BACKEND, LIGHTNING  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "examples" / "arcade_openapi.yaml"
FIXTURE = ROOT / "tests" / "fixtures" / "frontend_arcade" / "src"


def fe_files(**overrides: str) -> str:
    blocks = []
    for p in sorted(FIXTURE.rglob("*.tsx")):
        rel = f"frontend/src/{p.relative_to(FIXTURE).as_posix()}"
        blocks.append(f"=== FILE: {rel} ===\n{overrides.get(rel, p.read_text(encoding='utf-8'))}\n=== END FILE ===")
    return "\n\n".join(blocks) + "\n=== NOTES ===\nhash routing, typed client\n=== END NOTES ==="


GOOD = fe_files()
TSC_ERROR = "src/App.tsx(3,10): error TS2305: Module '\"./api/generated\"' has no exported member 'nope'."


class FakeToolRunner:
    """npm/node are simulated; python (the served-with-backend smoke test) runs for real."""
    name = "fake"

    def __init__(self, tsc_fail: list[str] | None = None, npm_missing: bool = False):
        self.calls: list[list[str]] = []
        self.tsc_fail = list(tsc_fail or [])
        self.npm_missing = npm_missing
        self.local = LocalRunner()

    def run(self, cmd, workdir: Path, timeout: int = 120) -> ExecResult:
        self.calls.append(cmd)
        if cmd[0] == "npm":
            if self.npm_missing:
                return ExecResult(127, "", "npm not found on PATH")
            (workdir / "node_modules").mkdir(exist_ok=True)
            return ExecResult(0, "added 69 packages", "")
        if cmd[0] == "node" and "typescript" in cmd[1]:
            return ExecResult(2, self.tsc_fail.pop(0), "") if self.tsc_fail else ExecResult(0, "", "")
        if cmd[0] == "node" and "vite" in cmd[1]:
            (workdir / "dist" / "assets").mkdir(parents=True, exist_ok=True)
            (workdir / "dist" / "index.html").write_text('<div id="root"></div><script type="module" src="/assets/app.js"></script>')
            (workdir / "dist" / "assets" / "app.js").write_text("console.log('app')")
            return ExecResult(0, "built", "")
        return self.local.run(cmd, workdir, timeout)


def make(*responses, runner=None, max_iterations=3, progress=None):
    cfg = RouterConfig.load("config/profiles/web-light.toml", env={})
    client = FakeClient()
    client.script(LIGHTNING, *responses)
    agent = FrontendAgent(ModelRouter(cfg, client, run_id="t"), runner or FakeToolRunner(), max_iterations=max_iterations,
                          on_progress=progress)
    return agent, client


def task(**kw) -> TaskAssignment:
    kw.setdefault("contract_ref", str(CONTRACT))
    kw.setdefault("files_allowed", ["frontend/**"])
    return TaskAssignment(stage_id="frontend", agent="frontend", instructions="Build the arcade frontend.", **kw)


def run(agent, t, root):
    return asyncio.run(agent.run(t, root))


def with_backend(root: Path) -> Path:
    shutil.copytree(ROOT / "extras" / "demo_app" / "backend", root / "backend")
    return root


# ---- generated client ------------------------------------------------------------------------------------------
def test_client_generated_from_contract():
    c = load_contract(CONTRACT)
    assert [op_name(e) for e in c.endpoints] == ["postRegister", "postLogin", "postScores", "getLeaderboard"]
    ts = generate_client(c)
    assert 'game: "snake" | "2048" | "memory";' in ts  # enums become unions: tsc enforces the contract
    assert "export type GetLeaderboardResponse = GetLeaderboardItem[];" in ts
    assert 'request<PostScoresResponse>("POST", `/api/scores`, { body, auth: true })' in ts
    assert 'request<PostLoginResponse>("POST", `/api/login`, { body, auth: false })' in ts
    assert "fetch(" in ts and ts.startswith("// GENERATED")


def test_path_params_are_encoded():
    spec = {"openapi": "3.1.0", "info": {"title": "x", "version": "1"}, "paths": {"/api/games/{game_id}": {"get": {
        "parameters": [{"name": "game_id", "in": "path", "required": True, "schema": {"type": "integer"}}],
        "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object",
                      "properties": {"id": {"type": "integer"}}}}}}}}}}}
    ts = generate_client(load_contract(spec))
    assert "getGamesByGameId: (gameId: number)" in ts and "${encodeURIComponent(String(gameId))}" in ts


# ---- the agent --------------------------------------------------------------------------------------------------
def test_builds_frontend_and_passes_its_checks(tmp_path):
    phases = []
    agent, client = make(ok(GOOD, 4000, 6000), progress=lambda p, m: phases.append(p))
    r = run(agent, task(), tmp_path)
    assert r.status == "done", [(c.name, c.log) for c in r.checks if not c.passed]
    names = {c.name: c for c in r.checks}
    assert {"generated API client unchanged", "contract usage", "no placeholders", "typecheck (tsc)", "build (vite)"} <= set(names)
    assert names["served with backend"].skipped  # backend not built yet: reported as not executed, not as passed proof
    assert (tmp_path / GENERATED_PATH).read_text() == generate_client(load_contract(CONTRACT))
    assert "frontend/src/pages/AccountPage.tsx" in r.files_changed and "frontend/package.json" in r.files_changed
    assert r.models_used == [f"B:{LIGHTNING}"] and r.tokens == 10_000 and r.notes == ["hash routing, typed client"]
    assert (tmp_path / ".agentforge" / "frontend_iter1.md").exists()  # diagnostics preserved
    assert phases[0] == "plan" and "implement" in phases and "verify" in phases and phases[-1] == "done"
    req = client.calls[0]
    assert "[agent:frontend]" in req["messages"][0]["content"] and "api.postScores(body)" in req["messages"][1]["content"]


def test_integration_with_generated_backend_is_verified(tmp_path):
    agent, _ = make(ok(GOOD))
    r = run(agent, task(), with_backend(tmp_path))
    served = next(c for c in r.checks if c.name == "served with backend")
    assert r.status == "done" and served.passed and not served.skipped, served.log
    assert "GET /api/leaderboard -> 200" in served.log


def test_repairs_from_real_tsc_errors_and_bounds_retries(tmp_path):
    agent, client = make(ok(GOOD), ok(GOOD), runner=FakeToolRunner(tsc_fail=[TSC_ERROR]))
    r = run(agent, task(), tmp_path)
    assert r.status == "done" and r.iterations == 2
    fix = client.calls[1]["messages"][-1]["content"]
    assert "EXECUTED" in fix and "TS2305" in fix  # the real compiler error reached the model
    agent, _ = make(runner=FakeToolRunner(tsc_fail=[TSC_ERROR] * 5), max_iterations=2)
    agent.router.client.default = ok(GOOD)
    r = run(agent, task(), tmp_path / "b")
    assert r.status == "failed" and r.iterations == 2
    assert any(c.name == "typecheck (tsc)" and not c.passed and "TS2305" in c.log for c in r.checks)


def test_partial_success_is_not_done(tmp_path):
    lazy = fe_files(**{"frontend/src/pages/LeaderboardPage.tsx": "export default function LeaderboardPage() { return <p>Leaderboard</p>; }"})
    agent, _ = make(ok(lazy), max_iterations=1)
    r = run(agent, task(), tmp_path)
    usage = next(c for c in r.checks if c.name == "contract usage")
    assert r.status == "failed" and not usage.passed and "getLeaderboard" in usage.log
    assert next(c for c in r.checks if c.name == "build (vite)").passed  # it builds, but it is not done


def test_placeholders_and_raw_fetch_are_rejected(tmp_path):
    bad = fe_files(**{"frontend/src/App.tsx": (FIXTURE / "App.tsx").read_text().replace(
        'href={`#/${n.route}`}', 'href="#"') + "\n// TODO wire up\nexport const x = () => fetch('/api/scores');\n"})
    agent, _ = make(ok(bad), max_iterations=1)
    r = run(agent, task(), tmp_path)
    logs = {c.name: c.log for c in r.checks}
    assert "TODO" in logs["no placeholders"] and 'href="#"' in logs["no placeholders"]
    assert "raw HTTP call" in logs["contract usage"]


def test_ownership_backend_and_locked_files(tmp_path):
    with_backend(tmp_path)
    before = (tmp_path / "backend" / "app.py").read_text()
    sneaky = GOOD + "\n=== FILE: backend/app.py ===\nhacked = True\n=== END FILE ==="
    agent, client = make(ok(sneaky), ok(GOOD))
    r = run(agent, task(), tmp_path)
    assert (tmp_path / "backend" / "app.py").read_text() == before
    assert "may only write frontend/**" in client.calls[1]["messages"][-1]["content"] and r.status == "done"
    for locked in ("frontend/package.json", "frontend/src/api/generated.ts", "../outside.ts", "frontend/node_modules/x.js"):
        agent, client = make(ok(GOOD + f"\n=== FILE: {locked} ===\nx\n=== END FILE ==="), max_iterations=1)
        r = run(agent, task(), tmp_path / f"w{abs(hash(locked))}")
        assert r.checks[0].name == "file ownership", locked
    assert not (tmp_path.parent / "outside.ts").exists()


def test_secrets_are_refused(tmp_path):
    leak = fe_files(**{"frontend/src/App.tsx": "const KEY = 'sk-" + "a" * 30 + "';\n" + (FIXTURE / "App.tsx").read_text()})
    agent, _ = make(ok(leak), max_iterations=1)
    r = run(agent, task(), tmp_path)
    assert r.status == "failed" and "secret" in r.checks[0].log
    assert "sk-aaaa" not in (tmp_path / "frontend" / "src" / "App.tsx").read_text()


def test_existing_frontend_is_preserved(tmp_path):
    (tmp_path / "frontend" / "src").mkdir(parents=True)
    (tmp_path / "frontend" / "package.json").write_text('{"name": "existing", "scripts": {}}')
    (tmp_path / "frontend" / "src" / "Keep.tsx").write_text("export const keep = 1;\n")
    agent, client = make(ok(GOOD), max_iterations=1)
    run(agent, task(), tmp_path)
    assert json.loads((tmp_path / "frontend" / "package.json").read_text())["name"] == "existing"  # no re-scaffold
    assert (tmp_path / "frontend" / "src" / "Keep.tsx").exists()
    assert "Keep.tsx" in client.calls[0]["messages"][1]["content"]  # existing work shown to the model


def test_invalid_assignments_and_contracts(tmp_path):
    agent, client = make()
    with pytest.raises(ValueError, match="agent 'backend'"):
        run(agent, TaskAssignment(stage_id="s", agent="backend", instructions="x"), tmp_path)
    with pytest.raises(ValueError, match="files_allowed"):
        run(agent, task(files_allowed=["backend/**"]), tmp_path)
    r = run(agent, task(contract_ref="contract/missing.yaml"), tmp_path)
    assert r.status == "contract_change_requested" and "not found" in r.contract_change.problem
    bad = tmp_path / "bad.yaml"
    # A declared request body without a usable JSON object schema (a body-less POST is valid since 30 Oct).
    bad.write_text("openapi: 3.1.0\ninfo: {title: x, version: '1'}\npaths:\n  /api/x:\n    post:\n"
                   "      requestBody: {content: {application/json: {schema: {type: object}}}}\n"
                   "      responses: {'200': {description: ok}}\n")
    r = run(agent, task(contract_ref=str(bad)), tmp_path)
    assert r.status == "contract_change_requested" and not client.calls  # no model spend on a broken contract


def test_contract_change_request_from_model(tmp_path):
    agent, _ = make(ok("=== CONTRACT_CHANGE_REQUEST ===\nproblem: no endpoint lists a user's own scores\n"
                       "proposed_change: add GET /api/me/scores\nreason: profile page\naffected_paths: /api/me/scores\n=== END ==="))
    r = run(agent, task(), tmp_path)
    assert r.status == "contract_change_requested" and r.contract_change.affected_paths == ["/api/me/scores"]


def test_reviewer_feedback_starts_repair(tmp_path):
    agent, client = make(ok(GOOD))
    run(agent, task(failed_attempts=1, reviewer_failures=[CheckResult(name="served with backend", passed=False,
                                                                      log="GET /api/leaderboard -> 500")]), tmp_path)
    first = client.calls[0]["messages"][-1]["content"]
    assert "GET /api/leaderboard -> 500" in first and "EXECUTED" in first


@pytest.mark.parametrize("err,reason", [(FakeAPIError(402, "Payment Required"), "provider_budget_exhausted"),
                                        (FakeAPIError(401, "bad key"), "auth_failed")])
def test_budget_and_key_failures_stop_cleanly(tmp_path, err, reason):
    agent, client = make(err)
    r = run(agent, task(), tmp_path)
    assert r.status == "stopped" and any(reason in n for n in r.notes) and len(client.calls) == 1


def test_npm_missing_is_not_executed_not_passed(tmp_path):
    agent, _ = make(ok(GOOD), runner=FakeToolRunner(npm_missing=True), max_iterations=1)
    r = run(agent, task(), tmp_path)
    dep = next(c for c in r.checks if c.name == "dependencies installed")
    assert r.status == "failed" and dep.skipped and not dep.passed


# ---- orchestrator integration -----------------------------------------------------------------------------------
class AgentAwareClient(FakeClient):
    """Backend and frontend run in parallel, so answers are chosen by the agent marker, not call order."""

    def __init__(self, answers: dict[str, object]):
        super().__init__()
        self.answers = answers
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._route))

    async def _route(self, **req):
        self.calls.append(req)
        system = req["messages"][0]["content"]
        return next(v for k, v in self.answers.items() if f"[agent:{k}]" in system)


def test_orchestrator_runs_backend_and_frontend_and_reviewer_verifies_both(tmp_path):
    from fastapi.testclient import TestClient
    from engine.router import AccountLedger
    from server.app import create_app
    from tests.test_contract_conformance import validate

    contract_yaml = CONTRACT.read_text(encoding="utf-8")
    client = AgentAwareClient({"planner": ok(planner_reply(contract_yaml)), "backend": ok(GOOD_BACKEND), "frontend": ok(GOOD)})
    cfg = RouterConfig.load("config/profiles/web-light.toml", env={})
    app = create_app(base_router=ModelRouter(cfg, client, account=AccountLedger(cfg)), data_dir=tmp_path / "runs",
                     runner=FakeToolRunner(), token="tok-for-tests-123", auth=True, serve_web=False, frontend_agent=FrontendAgent)
    c, H = TestClient(app), {"Authorization": "Bearer tok-for-tests-123"}
    rid = c.post("/api/v1/runs", json={"prompt": "Build the arcade"}, headers=H).json()["id"]
    for _ in range(600):
        run_ = c.get(f"/api/v1/runs/{rid}", headers=H).json()
        if run_["status"] in ("done", "failed", "stopped"):
            break
        time.sleep(0.2)
    try:
        assert run_["status"] == "done", run_["error"]
        st = {s["agent"]: s for s in run_["stages"]}
        assert st["frontend"]["status"] == "passed" and st["backend"]["status"] == "passed"
        validate("BuildResult", st["frontend"]["result"])
        review = c.get(f"/api/v1/runs/{rid}/review", headers=H).json()
        served = next(x for x in review["checks"] if x["name"] == "frontend: served with backend")
        assert served["passed"] and not served.get("skipped")  # required at review time: both sides exist
        assert {"frontend: typecheck (tsc)", "frontend: build (vite)", "backend: pytest"} <= {x["name"] for x in review["checks"]}
        prev = c.get(f"/api/v1/runs/{rid}/preview", headers=H).json()
        assert prev["status"] == "ready" and prev["url"].endswith("/")  # UI + API on one origin
        phases = [e["data"].get("phase") for e in _events(c, rid, H) if e["type"] == "stage_progress" and e["data"].get("agent") == "frontend"]
        assert {"plan", "implement", "verify", "done"} <= set(phases)
    finally:
        app.state.preview.stop_all()


def _events(c, rid, H):
    out = []
    with c.stream("GET", f"/api/v1/runs/{rid}/events", headers=H) as r:
        for line in r.iter_lines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


# ---- real toolchain (opt-in) ------------------------------------------------------------------------------------
@pytest.mark.skipif(os.environ.get("AF_REAL_TOOLCHAIN") != "1" or not shutil.which("npm"), reason="set AF_REAL_TOOLCHAIN=1 (needs npm)")
def test_real_toolchain_builds_and_serves(tmp_path):
    agent, _ = make(ok(GOOD), runner=LocalRunner(), max_iterations=1)
    r = run(agent, task(), with_backend(tmp_path))
    assert r.status == "done", [(c.name, c.log[-500:]) for c in r.checks if not c.passed]
    assert all(c.passed and not c.skipped for c in r.checks)


def test_truncated_file_is_not_written_and_model_is_told(tmp_path):
    cut = GOOD.split("=== FILE: frontend/src/pages/PlayPage.tsx ===")[0] + "=== FILE: frontend/src/pages/PlayPage.tsx ===\nimport {"
    agent, client = make(ok(cut), ok(GOOD))
    r = run(agent, task(), tmp_path)
    assert r.status == "done" and r.iterations == 2
    assert any("PlayPage.tsx not written" in n for n in r.notes)
    assert "stopped in the middle of frontend/src/pages/PlayPage.tsx" in client.calls[1]["messages"][-1]["content"]
    agent, client = make(ok("=== FILE: frontend/src/App.tsx ===\nexport default function App() {"), ok(GOOD))
    r = run(agent, task(), tmp_path / "b")
    assert r.status == "done" and "output truncated" in client.calls[1]["messages"][-1]["content"]


def test_check_logs_are_kept_per_attempt(tmp_path):
    agent, _ = make(ok(GOOD), ok(GOOD), runner=FakeToolRunner(tsc_fail=[TSC_ERROR]))
    run(agent, task(), tmp_path)
    first = (tmp_path / ".agentforge" / "frontend_iter1_checks.md").read_text(encoding="utf-8")
    assert "## typecheck (tsc): FAILED" in first and "TS2305" in first
    assert "## typecheck (tsc): ok" in (tmp_path / ".agentforge" / "frontend_iter2_checks.md").read_text(encoding="utf-8")


def test_template_has_action_hook_and_prompt_warns_about_known_type_errors(tmp_path):
    agent, client = make(ok(GOOD), max_iterations=1)
    run(agent, task(), tmp_path)
    assert (tmp_path / "frontend" / "src" / "lib" / "useAction.ts").is_file()
    system = client.calls[0]["messages"][0]["content"]
    assert "useAction" in system and "Never cast errors" in system


def test_repair_shows_source_at_each_error_and_asks_for_minimal_fix(tmp_path):
    agent, client = make(ok(GOOD), ok(GOOD), runner=FakeToolRunner(tsc_fail=[TSC_ERROR]))
    run(agent, task(), tmp_path)
    fix = client.calls[1]["messages"][-1]["content"]
    line3 = (FIXTURE / "App.tsx").read_text(encoding="utf-8").splitlines()[2]
    assert "SMALLEST change" in fix and "frontend/src/App.tsx line 3:" in fix and f">   3 | {line3}" in fix


def test_broken_backend_mid_build_is_not_blamed_on_frontend(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "__init__.py").write_text("")
    (tmp_path / "backend" / "app.py").write_text("raise RuntimeError('backend half written')\n")
    agent, _ = make(ok(GOOD))
    r = run(agent, task(), tmp_path)
    served = next(c for c in r.checks if c.name == "served with backend")
    assert r.status == "done" and served.skipped and "backend did not import" in served.log
    from engine.agents.frontend_checks import run_frontend_checks
    review = run_frontend_checks(tmp_path, load_contract(CONTRACT), FakeToolRunner(), require_integration=True)
    served = next(c for c in review if c.name == "served with backend")
    assert not served.passed and not served.skipped  # at review time a broken backend is a real failure


def test_repeated_errors_are_called_out(tmp_path):
    moved = TSC_ERROR.replace("(3,10)", "(4,10)")  # same error, shifted line
    agent, client = make(ok(GOOD), ok(GOOD), ok(GOOD), runner=FakeToolRunner(tsc_fail=[TSC_ERROR, moved]))
    r = run(agent, task(), tmp_path)
    assert r.status == "done" and r.iterations == 3
    assert "repeated errors" not in client.calls[1]["messages"][-1]["content"]
    third = client.calls[2]["messages"][-1]["content"]
    assert "unchanged since your previous attempt" in third and "TS2305" in third
