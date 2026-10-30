"""Deploy agent inside AgentForge: lifecycle position, configuration gates, the run's checkpoint service, the API
(contract-validated), events and redaction, cancellation, live-target outcomes and the disabled Nebius target.
Offline: GitHub is a local bare repository behind the real GitPublisher; no network, no real token."""

from __future__ import annotations

import asyncio
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine.agents import deploy_platform
from engine.agents.deploy_github import GitHubError, NotConfigured
from engine.agents.deploy_models import DeploymentStatus, RepoInfo
from engine.agents.deploy_support import FakeDeployTarget, FakeHealthChecker, HealthResult, NebiusServerlessTarget
from engine.agents.runner import LocalRunner
from engine.router import AccountLedger, ModelRouter, RouterConfig
from server.app import create_app
from server.pipeline import DeployRefused, deploy_final, deploy_preconditions, start_deployment
from tests.fakes import FakeClient, ok, planner_reply
from tests.test_backend_agent import AUTH_HOLE, GOOD, LIGHTNING
from tests.test_contract_conformance import validate
from tests.test_server import CONTRACT_YAML, H, PROMPT, sse_events, wait_terminal

TOKEN = "ghp_" + "A1b2C3d4" * 5  # fake, but shaped like a real token so redaction is exercised


class LocalGitHub:
    """GitHub stand-in: repositories are local bare repos (created on demand, like create_repo)."""

    def __init__(self, base: Path, fail: Exception | None = None):
        self.base, self.fail, self.created = base, fail, []

    def _info(self, name: str) -> RepoInfo:
        return RepoInfo(html_url=f"https://github.com/fake/{name}", clone_url=str(self.base / f"{name}.git"))

    def get_repo(self, name):
        if self.fail:
            raise self.fail
        return self._info(name) if (self.base / f"{name}.git").exists() else None

    def create_repo(self, name, private, description):
        assert private, "repositories must be private by default"
        subprocess.run(["git", "init", "--bare", "-q", str(self.base / f"{name}.git")], check=True)
        self.created.append(name)
        return self._info(name)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AF_DEPLOY_ENABLED", "1")
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)


def factory_for(tmp_path, calls: list, **kw):
    gh = LocalGitHub(tmp_path / "gh", kw.pop("fail", None))
    (tmp_path / "gh").mkdir(exist_ok=True)

    def factory(rec, target, on_event, should_stop):
        calls.append(rec.id)
        return deploy_platform.build_agent(rec, target, on_event, should_stop, github=gh, **kw)
    factory.github = gh
    return factory


def client(tmp_path, *responses, factory=None, default=None):
    cfg = RouterConfig.load("config/profiles/web-light.toml", env={})
    fake = FakeClient(default=default)
    fake.script(LIGHTNING, *responses)
    app = create_app(base_router=ModelRouter(cfg, fake, account=AccountLedger(cfg)), data_dir=tmp_path / "runs",
                     runner=LocalRunner(), token="t0ken-for-tests", auth=True, serve_web=False, frontend_agent=None,
                     deploy_factory=factory)
    return TestClient(app), app


PLAN = ok(planner_reply(CONTRACT_YAML), 2000, 3000)


def start(c, deploy: bool, n: int = 1):
    body = {"prompt": PROMPT, "project_name": "arcade", "options": {"enable_deploy_agent": deploy}}
    r = c.post("/api/v1/runs", json=body, headers={**H, "Idempotency-Key": f"deploy-key-{n:04d}"})
    assert r.status_code == 202
    return wait_terminal(c, r.json()["id"])


def git(repo: Path, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


# ---- lifecycle ---------------------------------------------------------------------------------------------
def test_pipeline_publishes_only_after_the_final_approval(tmp_path, env):
    calls: list = []
    f = factory_for(tmp_path, calls)
    c, app = client(tmp_path, PLAN, ok(GOOD, 3000, 2500), factory=f)
    final = start(c, deploy=True)
    rid = final["id"]
    assert final["status"] == "done", final["error"]
    stages = {s["agent"]: s for s in final["stages"]}
    assert stages["deploy"]["status"] == "passed" and stages["deploy"]["depends_on"] == [stages["reviewer"]["id"]]
    dep = c.get(f"/api/v1/runs/{rid}/deployments/latest", headers=H).json()
    validate("DeploymentStatus", dep)
    assert dep["status"] == "pushed" and dep["health_check_passed"] is None and dep["url"] is None  # not "healthy"
    cps = c.get(f"/api/v1/runs/{rid}/checkpoints", headers=H).json()["checkpoints"]
    assert dep["checkpoint_id"] == cps[-1]["id"] and dep["git_tag"] == cps[-1]["git_tag"]
    assert dep["source_commit"] == cps[-1]["commit"]
    remote = tmp_path / "gh" / f"{f.github.created[0]}.git"
    assert git(remote, "rev-parse", f"refs/tags/{dep['git_tag']}^{{commit}}") == dep["commit_sha"]
    types = [(e["type"], e.get("stage_id")) for e in sse_events(c, rid)]
    green = max(i for i, (t, s) in enumerate(types) if t == "checkpoint_created" and s == stages["reviewer"]["id"])
    first_deploy = min(i for i, (t, s) in enumerate(types) if s == stages["deploy"]["id"])
    assert first_deploy > green  # nothing of the deploy stage happens before the final green checkpoint
    statuses = [e["data"]["status"] for e in sse_events(c, rid) if e["type"] == "deployment_status"]
    assert statuses[0] == "queued" and "publishing" in statuses and statuses[-1] == "pushed"


def test_deployment_is_off_by_default(tmp_path, env):
    calls: list = []
    c, _ = client(tmp_path, PLAN, ok(GOOD, 3000, 2500), factory=factory_for(tmp_path, calls))
    final = start(c, deploy=False)
    assert final["status"] == "done" and not any(s["agent"] == "deploy" for s in final["stages"]) and calls == []


def test_requested_but_unconfigured_deployment_is_skipped_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.delenv("AF_DEPLOY_ENABLED", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    calls: list = []
    c, _ = client(tmp_path, PLAN, ok(GOOD, 3000, 2500), factory=factory_for(tmp_path, calls))
    final = start(c, deploy=True)
    deploy = next(s for s in final["stages"] if s["agent"] == "deploy")
    assert final["status"] == "done" and deploy["status"] == "skipped" and calls == []
    msg = [e for e in sse_events(c, final["id"]) if e["type"] == "stage_finished" and e["stage_id"] == deploy["id"]][0]
    assert "AF_DEPLOY_ENABLED" in msg["data"]["message"] and "GITHUB_TOKEN" in msg["data"]["message"]
    assert c.get("/api/v1/health", headers=H).json()["dependencies"]["deploy"] == "unavailable"


def test_rejected_build_never_reaches_deployment(tmp_path, env):
    calls: list = []
    c, _ = client(tmp_path, PLAN, factory=factory_for(tmp_path, calls), default=ok(AUTH_HOLE))
    body = {"prompt": PROMPT, "budget": {"max_retries_per_stage": 0}, "options": {"enable_deploy_agent": True}}
    rid = c.post("/api/v1/runs", json=body, headers=H).json()["id"]
    final = wait_terminal(c, rid)
    assert final["status"] == "failed" and calls == [] and not any(s["agent"] == "deploy" for s in final["stages"])
    assert c.post(f"/api/v1/runs/{rid}/deployments", json={"target": "github"}, headers=H).status_code == 409


# ---- API --------------------------------------------------------------------------------------------------
def test_api_deploys_a_finished_run_and_repeat_runs_are_idempotent(tmp_path, env):
    calls: list = []
    f = factory_for(tmp_path, calls)
    c, app = client(tmp_path, PLAN, ok(GOOD, 3000, 2500), factory=f)
    rid = start(c, deploy=False)["id"]
    assert c.get(f"/api/v1/runs/{rid}/deployments/latest", headers=H).status_code == 404
    assert c.get("/api/v1/health", headers=H).json()["dependencies"]["deploy"] == "ok"
    shas = []
    for _ in range(2):
        r = c.post(f"/api/v1/runs/{rid}/deployments", json={"target": "github"}, headers=H)
        assert r.status_code == 202, r.text
        validate("DeploymentStatus", r.json())
        assert r.json()["status"] == "queued"
        for _ in range(300):
            dep = c.get(f"/api/v1/runs/{rid}/deployments/latest", headers=H).json()
            if dep["status"] not in ("queued", "publishing", "deploying"):
                break
            time.sleep(0.1)
        validate("DeploymentStatus", dep)
        assert dep["status"] == "pushed", dep
        shas.append(dep["commit_sha"])
    assert shas[0] == shas[1] and len(f.github.created) == 1  # same commit, repo reused, no force push needed
    assert TOKEN not in c.get(f"/api/v1/runs/{rid}/deployments/latest", headers=H).text


@pytest.mark.parametrize("body,needle", [
    ({"target": "nebius_serverless"}, "Nebius deployment is not available"),
    ({"target": "configured_target"}, "no deployment target is configured"),
])
def test_unavailable_targets_are_refused_with_the_reason(tmp_path, env, body, needle):
    c, _ = client(tmp_path, PLAN, ok(GOOD, 3000, 2500), factory=factory_for(tmp_path, []))
    rid = start(c, deploy=False)["id"]
    r = c.post(f"/api/v1/runs/{rid}/deployments", json=body, headers=H)
    assert r.status_code == 409 and needle in r.json()["message"]


def test_api_rejects_invalid_deployment_input(tmp_path, env):
    c, _ = client(tmp_path, PLAN, ok(GOOD, 3000, 2500), factory=factory_for(tmp_path, []))
    rid = start(c, deploy=False)["id"]
    for body in ({"target": "github", "health_check_path": "//evil.example"}, {"target": "github", "repo_name": "a/b"},
                 {"target": "github", "visibility": "internal"}, {"target": "s3"}):
        assert c.post(f"/api/v1/runs/{rid}/deployments", json=body, headers=H).status_code == 422, body


def test_missing_github_credentials_are_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("AF_DEPLOY_ENABLED", "1")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    cap = deploy_platform.capability()
    assert not cap["github_ready"] and cap["missing"] == ["GITHUB_TOKEN is not set on the server"]
    c, _ = client(tmp_path, PLAN, ok(GOOD, 3000, 2500), factory=factory_for(tmp_path, []))
    rid = start(c, deploy=False)["id"]
    r = c.post(f"/api/v1/runs/{rid}/deployments", json={"target": "github"}, headers=H)
    assert r.status_code == 409 and "GITHUB_TOKEN is not set" in r.json()["message"]


# ---- checkpoint verification against the run's checkpoint service ------------------------------------------
def _done_rec(tmp_path, env_on=True):
    c, app = client(tmp_path, PLAN, ok(GOOD, 3000, 2500), factory=factory_for(tmp_path, []))
    rid = start(c, deploy=False)["id"]
    return c, app.state.runs.get(rid)


def test_verifier_accepts_only_the_final_approved_annotated_tag(tmp_path, env):
    c, rec = _done_rec(tmp_path)
    v = deploy_platform.RunCheckpointVerifier(rec)
    final, contract_cp = rec.checkpoints[-1], rec.checkpoints[0]
    assert v.why_not(rec.id, final["id"], final["git_tag"]) is None
    assert v.why_not(rec.id, contract_cp["id"], contract_cp["git_tag"]) == "not the run's final checkpoint"
    assert v.why_not("run_other", final["id"], final["git_tag"]) == "not the run's final checkpoint"
    assert v.why_not(rec.id, final["id"], "stage-x-green") == "git tag does not match the checkpoint"
    reviewer = next(s for s in rec.stages if s["agent"] == "reviewer")
    reviewer["review"] = {**reviewer["review"], "verdict": "reject"}
    assert v.why_not(rec.id, final["id"], final["git_tag"]) == "the Reviewer did not approve the final stage"
    with pytest.raises(DeployRefused, match="not deployable"):
        deploy_preconditions(rec, "github")


def test_a_moved_tag_is_refused_even_though_it_still_exists(tmp_path, env):
    c, rec = _done_rec(tmp_path)
    ws, final = rec.workspace, rec.checkpoints[-1]
    git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "after approval")
    git(ws, "-c", "user.email=t@t", "-c", "user.name=t", "tag", "-a", "-f", final["git_tag"], "-m", "moved")
    dep = start_deployment(rec, "github")
    out = asyncio.run(deploy_final(rec, dep, factory=factory_for(tmp_path, [])))
    assert out["status"] == "failed" and any("does not match the tag commit" in n for n in out["notes"])


# ---- outcomes, events, redaction, cancellation ----------------------------------------------------------------
def _deploy_with(tmp_path, rec, **kw) -> dict:
    return asyncio.run(deploy_final(rec, start_deployment(rec, "github"), factory=factory_for(tmp_path, [], **kw)))


def test_github_api_failure_is_redacted_in_status_and_events(tmp_path, env):
    c, rec = _done_rec(tmp_path)
    dep = _deploy_with(tmp_path, rec, fail=GitHubError(f"bad credentials for token {TOKEN}", 401))
    assert dep["status"] == "failed" and rec.status == "done"  # the verified build is not undone
    blob = str(dep) + str(rec.events)
    assert TOKEN not in blob and "[REDACTED]" in blob


def test_cancellation_stops_the_deployment(tmp_path, env):
    c, rec = _done_rec(tmp_path)
    rec.cancel_requested = True
    dep = _deploy_with(tmp_path, rec)
    stage = next(s for s in rec.stages if s["agent"] == "deploy")
    assert dep["status"] == "failed" and dep["error"].startswith("stopped") and stage["status"] == "stopped"


_N = iter(range(10**6))


def _live(status: str, url="https://app.example.test"):
    return DeploymentStatus(id=f"d-{status}-{next(_N)}", status=status, target="fake", url=url)


@pytest.mark.parametrize("health,expected", [([HealthResult(True, 200, 1)], "healthy"),
                                             ([HealthResult(False, 503, 5, "HTTP 503")], "failed")])
def test_live_target_outcomes_map_to_platform_states(tmp_path, env, health, expected):
    c, rec = _done_rec(tmp_path)
    dep = _deploy_with(tmp_path, rec, target=FakeDeployTarget([_live("deploying")]), health=FakeHealthChecker(health))
    assert dep["status"] == expected and dep["health_check_passed"] is (expected == "healthy")
    validate("DeploymentStatus", {k: dep[k] for k in dep if k != "stage_id"})


def test_failed_health_check_rolls_back_to_the_previous_healthy_deployment(tmp_path, env):
    c, rec = _done_rec(tmp_path)
    target = FakeDeployTarget([_live("deploying"), _live("deploying", "https://v2.example.test")],
                              rollback_outcomes=[_live("deploying")])
    health = FakeHealthChecker([HealthResult(True, 200, 1), HealthResult(False, 500, 5, "boom"), HealthResult(True, 200, 1)])
    first = _deploy_with(tmp_path, rec, target=target, health=health)
    assert first["status"] == "healthy"
    # A healthy deployment of this checkpoint makes a redeploy a no-op; record it as an earlier checkpoint's.
    history = rec.workspace / ".agentforge" / "deploy" / "deployments.json"
    history.write_text(history.read_text(encoding="utf-8").replace(rec.checkpoints[-1]["id"], "cp_previous"), encoding="utf-8")
    second = _deploy_with(tmp_path, rec, target=target, health=health)
    assert second["status"] == "rolled_back" and target.rollback_calls


def test_nebius_target_stays_disabled_and_says_what_is_missing(monkeypatch):
    monkeypatch.delenv("NEBIUS_PROJECT_ID", raising=False)
    t = NebiusServerlessTarget()
    missing = t.missing_requirements({})
    assert not t.is_configured() and missing[0].startswith("adapter not implemented")
    assert any("NEBIUS_PROJECT_ID" in m for m in missing)
    with pytest.raises(NotConfigured):
        t.deploy(Path("."), None, None)
    assert deploy_platform.capability({"AF_DEPLOY_ENABLED": "1", "GITHUB_TOKEN": "x"})["targets"]["nebius_serverless"] is False
