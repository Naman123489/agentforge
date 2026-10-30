"""Phase 3 Part B Step 1 tests using local Git and fake services only."""

from __future__ import annotations

import asyncio
import hashlib
import base64
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from datetime import datetime

import httpx
import pytest

from engine.agents.deploy_agent import DeployAgent, to_deployment_status
from engine.agents.deploy_git import GitPublishError, GitPublisher
from engine.agents.deploy_git import PublishResult
from engine.agents.deploy_github import FakeGitHubClient, GitHubError, HttpxGitHubClient, NotConfigured
from engine.agents.deploy_models import DeployRequest, DeploymentStatus, RepoInfo
from engine.agents.deploy_runner import SubprocessRunner
from engine.agents.deploy_support import DeploymentHistory, DisabledTarget, FakeDeployTarget, FakeHealthChecker, FileCheckpointVerifier, HealthResult


GIT_FALLBACK = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "native" / "git" / "cmd" / "git.exe"


class LocalGitHubClient(FakeGitHubClient):
    def __init__(self, bare: Path, existing: bool = False) -> None:
        self.repo = RepoInfo(
            html_url="https://github.com/fake/arcade", clone_url=str(bare), default_branch="main"
        )
        super().__init__({"arcade": self.repo} if existing else None)
        self.get_calls = 0

    def get_repo(self, name: str) -> RepoInfo | None:
        self.get_calls += 1
        return super().get_repo(name)

    def create_repo(self, name: str, private: bool, description: str) -> RepoInfo:
        self.create_calls += 1
        self.repos[name] = self.repo
        return self.repo


class CountingRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.inner = SubprocessRunner()

    def run(self, cmd: list[str], workdir: Path, timeout: float, env: dict[str, str] | None = None):
        self.calls += 1
        return self.inner.run(cmd, workdir, timeout, env)


class FalseVerifier:
    def is_approved(self, run_id: str, checkpoint_id: str, git_tag: str) -> bool:
        return False


@pytest.fixture
def sample(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    executable = shutil.which("git") or (str(GIT_FALLBACK) if GIT_FALLBACK.exists() else None)
    if executable is None:
        pytest.skip("git executable unavailable")
    monkeypatch.setenv("PATH", str(Path(executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    root = tmp_path / "generated project with space"
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "app.py").write_text("print('hello')\n")
    checkpoints = root / ".agentforge" / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / "cp_1.json").write_text(json.dumps({"id": "cp_1", "run_id": "run_1", "git_tag": "cp-1"}))
    bare = tmp_path / "local bare remote.git"
    subprocess.run([executable, "init", "--bare", str(bare)], cwd=tmp_path, check=True, capture_output=True, timeout=15)
    request = DeployRequest(
        run_id="run_1", stage_id="final", checkpoint_id="cp_1", git_tag="cp-1", repo_name="Arcade"
    )
    return root, bare, executable, request


_DEFAULT = object()


def make_agent(
    root: Path,
    bare: Path,
    *,
    existing: bool = False,
    verifier=_DEFAULT,
    include_ci: bool = False,
    github=None,
    publisher=None,
    health=None,
    target=None,
    history=None,
    on_event=None,
    should_stop=None,
    clock=None,
    max_seconds: float = 300,
):
    github = github if github is not None else LocalGitHubClient(bare, existing=existing)
    runner = CountingRunner()
    agent = DeployAgent(
        github=github,
        publisher=publisher if publisher is not None else GitPublisher(runner),
        health=health if health is not None else FakeHealthChecker(),
        target=target if target is not None else DisabledTarget(),
        history=history if history is not None else DeploymentHistory(root / ".agentforge" / "deploy" / "deployments.json"),
        verifier=FileCheckpointVerifier(root) if verifier is _DEFAULT else verifier,
        include_ci=include_ci,
        on_event=on_event,
        should_stop=should_stop,
        clock=clock,
        max_seconds=max_seconds,
        content_mode="working_tree",
    )
    return agent, github, runner


def git(executable: str, root: Path, *args: str) -> str:
    return subprocess.run([executable, *args], cwd=root, text=True, capture_output=True, check=True, timeout=15).stdout.strip()


def source_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(root.rglob("*")):
        if not file.is_file() or ".agentforge" in file.relative_to(root).parts:
            continue
        digest.update(file.relative_to(root).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def run_preserving_source(agent: DeployAgent, request: DeployRequest, root: Path):
    before = source_hash(root)
    result = agent.run(request, root)
    assert source_hash(root) == before
    return result


def test_happy_path_push_only(sample) -> None:
    root, bare, executable, request = sample
    agent, github, runner = make_agent(root, bare)
    result = agent.run(request, root)
    assert result.status == "done" and result.repo_url == github.repo.html_url
    assert result.commit_sha and result.git_tag == "cp-1"
    assert any("push-only" in note for note in result.notes)
    assert git(executable, bare, "rev-parse", "refs/heads/main") == result.commit_sha
    assert git(executable, bare, "rev-parse", "refs/tags/cp-1^{commit}") == result.commit_sha
    assert {"README.md", "LICENSE", ".gitignore", "backend/app.py"}.issubset(result.files_pushed)
    assert ".github/workflows/ci.yml" not in result.files_pushed
    assert github.create_calls == 1 and runner.calls > 0


def test_unapproved_checkpoint_makes_no_repo_or_git_calls(sample) -> None:
    root, bare, _, request = sample
    agent, github, runner = make_agent(root, bare, verifier=FalseVerifier())
    result = agent.run(request, root)
    assert result.status == "failed" and "no approved checkpoint" in " ".join(result.notes)
    assert github.get_calls == github.create_calls == runner.calls == 0
    assert not (root / ".agentforge" / "deploy" / "staging").exists()


def test_run_async_honors_should_stop_without_side_effects(sample) -> None:
    root, bare, _, request = sample
    agent, github, runner = make_agent(root, bare, should_stop=lambda: True)
    result = asyncio.run(agent.run_async(request, root))
    assert result.status == "stopped"
    assert "checkpoint validation" in " ".join(result.notes)
    assert github.get_calls == github.create_calls == runner.calls == 0
    assert not (root / ".agentforge" / "deploy" / "staging").exists()


def test_run_async_converts_component_exception_to_failed_result(sample) -> None:
    root, bare, _, request = sample

    class RaisingVerifier:
        def is_approved(self, run_id: str, checkpoint_id: str, git_tag: str) -> bool:
            raise RuntimeError("verifier failed")

    agent, _, _ = make_agent(root, bare, verifier=RaisingVerifier())
    result = asyncio.run(agent.run_async(request, root))
    assert result.status == "failed"
    assert "verifier failed" in " ".join(result.notes)


def test_run_async_converts_wrapper_exception_to_failed_result(sample, monkeypatch) -> None:
    root, bare, _, request = sample
    agent, _, _ = make_agent(root, bare)
    monkeypatch.setattr(agent, "run", lambda *_: (_ for _ in ()).throw(RuntimeError("ghp_" + "x" * 24)))
    result = asyncio.run(agent.run_async(request, root))
    assert result.status == "failed"
    assert "ghp_" not in result.model_dump_json()
    assert "[REDACTED]" in " ".join(result.notes)


def test_run_async_matches_sync_result_on_success(tmp_path: Path) -> None:
    request = DeployRequest(
        run_id="run_1", stage_id="final", checkpoint_id="cp_1", git_tag="cp-1", repo_name="Arcade"
    )

    class FakePublisher:
        def publish(self, staged, repo, branch, tag, message):
            (staged / ".git").mkdir(exist_ok=True)
            return PublishResult("a" * 40, tag, True, [".gitignore", "LICENSE", "README.md", "backend/app.py"])

    def scenario(folder: str, async_mode: bool):
        root = tmp_path / folder
        (root / "backend").mkdir(parents=True)
        (root / "backend" / "app.py").write_text("print('hello')\n")
        checkpoint = root / ".agentforge" / "checkpoints"
        checkpoint.mkdir(parents=True)
        (checkpoint / "cp_1.json").write_text(json.dumps({"id": "cp_1", "run_id": "run_1", "git_tag": "cp-1"}))
        events: list[dict] = []
        agent, _, _ = make_agent(root, tmp_path / "fake.git", publisher=FakePublisher(), on_event=events.append)
        result = asyncio.run(agent.run_async(request, root)) if async_mode else agent.run(request, root)
        return result, events

    sync, sync_events = scenario("sync workspace", False)
    async_result, async_events = scenario("async workspace", True)
    fields = ("status", "repo_url", "commit_sha", "git_tag", "files_pushed")
    assert tuple(getattr(sync, field) for field in fields) == tuple(getattr(async_result, field) for field in fields)
    assert sync.status == "done"
    assert [(item.name, item.passed) for item in sync.checks] == [
        (item.name, item.passed) for item in async_result.checks
    ]
    assert sync_events and async_events
    assert sync_events[0]["type"] == async_events[0]["type"] == "stage_started"
    assert sync_events[-1]["type"] == async_events[-1]["type"] == "stage_finished"


def test_run_async_cancellation_prevents_later_steps(sample, monkeypatch) -> None:
    root, bare, _, request = sample
    before = source_hash(root)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original_prepare = __import__("engine.agents.deploy_agent", fromlist=["prepare_publish_files"]).prepare_publish_files

    def blocking_prepare(*args, **kwargs):
        entered.set()
        assert release.wait(5), "test failed to release the in-progress step"
        return original_prepare(*args, **kwargs)

    monkeypatch.setattr("engine.agents.deploy_agent.prepare_publish_files", blocking_prepare)

    class FakePublisher:
        calls = 0

        def publish(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("push must not start after cancellation")

    publisher = FakePublisher()
    agent, github, _ = make_agent(
        root, bare, publisher=publisher,
        on_event=lambda event: finished.set() if event["type"] == "stage_finished" else None,
    )

    async def cancel_during_step() -> None:
        task = asyncio.create_task(agent.run_async(request, root))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
        assert await asyncio.to_thread(finished.wait, 5)

    asyncio.run(cancel_during_step())
    assert github.get_calls == github.create_calls == 0
    assert publisher.calls == 0
    assert source_hash(root) == before


def test_secret_in_workspace_blocks_repo_creation(sample) -> None:
    root, bare, _, request = sample
    (root / "backend" / "settings.py").write_text("API_KEY = 'nvapi-" + "x" * 24 + "'\n")
    agent, github, runner = make_agent(root, bare)
    result = agent.run(request, root)
    assert result.status == "failed" and "backend/settings.py" in " ".join(result.notes)
    assert "nvapi-" not in " ".join(result.notes)
    assert github.get_calls == github.create_calls == runner.calls == 0


@pytest.mark.parametrize("failure", ["unapproved", "secret", "github", "git"])
def test_status_is_none_when_run_fails_before_deployment(sample, failure: str) -> None:
    root, bare, _, request = sample
    if failure == "unapproved":
        agent, _, _ = make_agent(root, bare, verifier=FalseVerifier())
    elif failure == "secret":
        (root / "backend" / "settings.py").write_text("ghp_" + "b" * 36)
        agent, _, _ = make_agent(root, bare)
    elif failure == "github":
        class FailingGitHub(FakeGitHubClient):
            def get_repo(self, name: str):
                raise GitHubError("fake lookup failed")

        agent, _, _ = make_agent(root, bare, github=FailingGitHub())
    else:
        class FailingPublisher:
            def publish(self, *args, **kwargs):
                raise GitPublishError("fake push failed")

        agent, _, _ = make_agent(root, bare, existing=True, publisher=FailingPublisher())
    result = run_preserving_source(agent, request, root)
    assert result.status == "failed"
    assert result.deployment is None
    assert to_deployment_status(result) is None


def test_existing_repo_is_reused(sample) -> None:
    root, bare, _, request = sample
    agent, github, _ = make_agent(root, bare, existing=True)
    result = agent.run(request, root)
    assert result.status == "done" and github.get_calls == 1 and github.create_calls == 0


def test_rerun_same_checkpoint_is_idempotent(sample) -> None:
    root, bare, executable, request = sample
    agent, github, _ = make_agent(root, bare)
    first = agent.run(request, root)
    second = agent.run(request, root)
    assert first.status == second.status == "done", (first.notes, second.notes)
    assert first.commit_sha == second.commit_sha
    assert github.create_calls == 1
    assert git(executable, bare, "rev-list", "--count", "refs/heads/main") == "1"
    assert git(executable, bare, "tag", "--list") == "cp-1"


def test_original_source_tree_unchanged(sample) -> None:
    root, bare, _, request = sample
    before = source_hash(root)
    agent, _, _ = make_agent(root, bare)
    assert agent.run(request, root).status == "done"
    assert source_hash(root) == before
    assert not (root / "README.md").exists()
    assert not (root / "LICENSE").exists()
    assert not (root / ".gitignore").exists()


# Group 1: safety


def test_events_and_all_written_files_contain_no_token(sample, monkeypatch: pytest.MonkeyPatch) -> None:
    root, bare, _, request = sample
    token = "ghp_" + "z" * 36
    monkeypatch.setenv("GITHUB_TOKEN", token)
    encoded = base64.b64encode(f"x-access-token:{token}".encode())
    events: list[dict] = []
    target = FakeDeployTarget([DeploymentStatus(id="new", status="deploying", target="fake", url="https://app.example")])
    health = FakeHealthChecker([HealthResult(True, 200, 1)])
    agent, _, _ = make_agent(root, bare, target=target, health=health, on_event=events.append)
    result = run_preserving_source(agent, request, root)
    assert result.status == "done"
    assert result.deployment is not None and result.deployment.status == "healthy"
    assert events[0]["type"] == "stage_started" and events[-1]["type"] == "stage_finished"
    assert len({event["id"] for event in events}) == len(events)
    allowed = {"stage_started", "stage_progress", "deployment_status", "stage_finished", "error"}
    for event in events:
        assert event["type"] in allowed
        assert {"id", "run_id", "type", "timestamp", "data"}.issubset(event)
        assert event["id"].startswith("evt_") and event["run_id"] == "run_1"
        assert event["timestamp"].endswith("Z")
        assert datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).tzinfo is not None
    serialized = [json.dumps(events).encode(), result.model_dump_json().encode()]
    history_file = root / ".agentforge" / "deploy" / "deployments.json"
    serialized.append(history_file.read_bytes())
    serialized.extend(file.read_bytes() for file in (root / ".agentforge").rglob("*") if file.is_file())
    assert all(token.encode() not in data and encoded not in data for data in serialized)


@pytest.mark.parametrize("component", ["github", "publisher", "health", "target", "history", "verifier"])
def test_unexpected_component_exception_never_escapes(sample, monkeypatch: pytest.MonkeyPatch, component: str) -> None:
    root, bare, _, request = sample
    token = "ghp_" + "q" * 36
    monkeypatch.setenv("GITHUB_TOKEN", token)
    target = FakeDeployTarget([DeploymentStatus(id="new", status="deploying", target="fake", url="https://app.example")])
    health = FakeHealthChecker([HealthResult(True, 200, 1)])
    agent, _, _ = make_agent(root, bare, target=target, health=health)

    def broken(*args, **kwargs):
        raise RuntimeError("component broke with " + token)

    object_to_break, method = {
        "github": (agent.github, "get_repo"),
        "publisher": (agent.publisher, "publish"),
        "health": (agent.health, "check"),
        "target": (agent.target, "deploy"),
        "history": (agent.history, "for_checkpoint"),
        "verifier": (agent.verifier, "is_approved"),
    }[component]
    monkeypatch.setattr(object_to_break, method, broken)
    result = run_preserving_source(agent, request, root)
    assert result.status == "failed"
    assert "[REDACTED]" in " ".join(result.notes)
    assert token not in result.model_dump_json()


def test_persistent_github_503_returns_failed_after_three_requests(sample, monkeypatch: pytest.MonkeyPatch) -> None:
    root, bare, _, request = sample
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "t" * 36)
    calls: list[httpx.Request] = []

    def server_error(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(503)

    github = HttpxGitHubClient(httpx.MockTransport(server_error), sleep=lambda _: None)
    agent, _, runner = make_agent(root, bare, github=github)
    result = run_preserving_source(agent, request, root)
    assert result.status == "failed" and len(calls) == 3 and runner.calls == 0
    assert "GitHub server error" in " ".join(result.notes)


def test_missing_token_with_real_client_returns_failed(sample) -> None:
    root, bare, _, request = sample
    github = HttpxGitHubClient(httpx.MockTransport(lambda req: pytest.fail("unexpected request")))
    agent, _, runner = make_agent(root, bare, github=github)
    result = run_preserving_source(agent, request, root)
    assert result.status == "failed" and runner.calls == 0
    assert "GITHUB_TOKEN is not set" in " ".join(result.notes)


def test_raising_event_callback_does_not_break_run(sample) -> None:
    root, bare, _, request = sample

    def broken_callback(event: dict) -> None:
        raise RuntimeError("callback failure")

    agent, _, _ = make_agent(root, bare, on_event=broken_callback)
    result = run_preserving_source(agent, request, root)
    assert result.status == "done" and "event callback failed" in result.notes


@pytest.mark.parametrize("kind", ["none", "false", "raises"])
def test_verifier_failure_touches_no_stage_repo_or_git(sample, kind: str) -> None:
    root, bare, _, request = sample

    class RaisingVerifier:
        def is_approved(self, run_id: str, checkpoint_id: str, git_tag: str) -> bool:
            raise RuntimeError("verifier unavailable")

    verifier = {"none": None, "false": FalseVerifier(), "raises": RaisingVerifier()}[kind]
    agent, github, runner = make_agent(root, bare, verifier=verifier)
    result = run_preserving_source(agent, request, root)
    assert result.status == "failed"
    assert github.get_calls == github.create_calls == runner.calls == 0
    assert not (root / ".agentforge" / "deploy" / "staging").exists()


# Group 2: deployment and rollback


def test_push_and_healthy_deploy_records_history(sample) -> None:
    root, bare, _, request = sample
    target = FakeDeployTarget([DeploymentStatus(id="new", status="deploying", target="fake", url="https://app.example")])
    health = FakeHealthChecker([HealthResult(True, 200, 1)])
    history = DeploymentHistory(root / ".agentforge" / "deploy" / "deployments.json")
    agent, _, _ = make_agent(root, bare, target=target, health=health, history=history)
    result = run_preserving_source(agent, request, root)
    assert result.status == "done" and result.repo_url and result.commit_sha
    assert result.deployment is not None and result.deployment.status == "healthy"
    assert result.deployment.health_check_passed is True and result.deployment.url == "https://app.example"
    assert history.for_checkpoint("run_1", "cp_1").status == "healthy"
    assert len(target.deploy_calls) == len(health.calls) == 1


def test_unhealthy_deploy_rolls_back_previous_healthy(sample) -> None:
    root, bare, _, request = sample
    history = DeploymentHistory(root / ".agentforge" / "deploy" / "deployments.json")
    previous = DeploymentStatus(id="old", status="healthy", target="fake", url="https://old.example", health_check_passed=True)
    history.add("run_1", "cp_0", previous)
    target = FakeDeployTarget(
        [DeploymentStatus(id="new", status="deploying", target="fake", url="https://new.example")],
        [DeploymentStatus(id="rollback", status="deploying", target="fake", url="https://old.example")],
    )
    health = FakeHealthChecker([HealthResult(False, 503, 2, "HTTP 503"), HealthResult(True, 200, 1)])
    agent, _, _ = make_agent(root, bare, target=target, health=health, history=history)
    result = run_preserving_source(agent, request, root)
    assert result.status == "failed" and result.repo_url and result.commit_sha
    assert result.deployment is not None and result.deployment.status == "rolled_back"
    assert len(target.rollback_calls) == 1 and target.rollback_calls[0].id == "old"
    assert [call[0] for call in health.calls] == ["https://new.example", "https://old.example"]
    assert "rolled back" in " ".join(result.notes)
    assert any(entry["deployment"]["id"] == "new" and entry["deployment"]["status"] == "rolled_back" for entry in json.loads(history.path.read_text()))


def test_unhealthy_deploy_without_previous_has_clear_note(sample) -> None:
    root, bare, _, request = sample
    target = FakeDeployTarget([DeploymentStatus(id="new", status="deploying", target="fake", url="https://new.example")])
    health = FakeHealthChecker([HealthResult(False, 503, 2, "HTTP 503")])
    agent, _, _ = make_agent(root, bare, target=target, health=health)
    result = run_preserving_source(agent, request, root)
    assert result.status == "failed"
    assert "no previous deployment to roll back to" in result.notes
    assert target.rollback_calls == []


def test_rollback_disabled_never_calls_target_rollback(sample) -> None:
    root, bare, _, request = sample
    request = request.model_copy(update={"rollback_on_failure": False})
    target = FakeDeployTarget([DeploymentStatus(id="new", status="deploying", target="fake", url="https://new.example")])
    health = FakeHealthChecker([HealthResult(False, 503, 2, "HTTP 503")])
    agent, _, _ = make_agent(root, bare, target=target, health=health)
    result = run_preserving_source(agent, request, root)
    assert result.status == "failed" and "rollback disabled" in result.notes
    assert target.rollback_calls == []


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_rollback_failure_never_claims_healthy(sample, rollback_fails: bool) -> None:
    root, bare, _, request = sample
    history = DeploymentHistory(root / ".agentforge" / "deploy" / "deployments.json")
    history.add("run_1", "cp_0", DeploymentStatus(id="old", status="healthy", target="fake", url="https://old.example"))
    target = FakeDeployTarget(
        [DeploymentStatus(id="new", status="deploying", target="fake", url="https://new.example")],
        [DeploymentStatus(id="rollback", status="deploying", target="fake", url="https://old.example")],
    )
    if rollback_fails:
        def broken_rollback(previous):
            raise RuntimeError("rollback service failed")
        target.rollback = broken_rollback
    health = FakeHealthChecker([HealthResult(False, 503, 1, "HTTP 503"), HealthResult(False, 503, 1, "HTTP 503")])
    agent, _, _ = make_agent(root, bare, target=target, health=health, history=history)
    result = run_preserving_source(agent, request, root)
    assert result.status == "failed" and result.deployment is not None
    assert result.deployment.status != "healthy"
    assert "rollback" in " ".join(result.notes).lower()
    assert history.for_checkpoint("run_1", "cp_1").status != "healthy"


def test_same_checkpoint_healthy_history_skips_redeploy(sample) -> None:
    root, bare, _, request = sample
    history = DeploymentHistory(root / ".agentforge" / "deploy" / "deployments.json")
    history.add("run_1", "cp_1", DeploymentStatus(id="existing", status="healthy", target="fake", url="https://app.example"))
    target = FakeDeployTarget([DeploymentStatus(id="new", status="deploying", target="fake", url="https://new.example")])
    health = FakeHealthChecker()
    agent, _, _ = make_agent(root, bare, target=target, health=health, history=history)
    result = run_preserving_source(agent, request, root)
    assert result.status == "done" and result.deployment is not None and result.deployment.id == "existing"
    assert "redeploy skipped" in " ".join(result.notes)
    assert target.deploy_calls == [] and health.calls == []


def test_notconfigured_deploy_target_keeps_push_only_success(sample) -> None:
    root, bare, _, request = sample

    class UnconfiguredTarget(FakeDeployTarget):
        def deploy(self, staged_dir, request, repo):
            raise NotConfigured("deployment not configured")

    target = UnconfiguredTarget()
    agent, _, _ = make_agent(root, bare, target=target)
    result = run_preserving_source(agent, request, root)
    assert result.status == "done" and result.commit_sha and result.repo_url
    assert "push-only: deployment not configured" in result.notes


def test_invalid_deployment_url_fails_without_health_request(sample) -> None:
    root, bare, _, request = sample
    target = FakeDeployTarget([DeploymentStatus(id="new", status="deploying", target="fake", url="file:///tmp/fake")])
    health = FakeHealthChecker([HealthResult(True, 200, 1)])
    agent, _, _ = make_agent(root, bare, target=target, health=health)
    result = run_preserving_source(agent, request, root)
    assert result.status == "failed" and result.commit_sha
    assert health.calls == []
    assert "invalid deployment URL" in " ".join(result.notes)


# Group 3: limits and result structure


@pytest.mark.parametrize(
    ("stop_at", "step_name"),
    [
        (1, "checkpoint validation"),
        (2, "staging"),
        (3, "publish file preparation"),
        (4, "secret scan"),
        (5, "repository lookup"),
        (6, "git push"),
        (7, "deployment configuration"),
        (8, "deployment"),
    ],
)
def test_should_stop_before_each_step(sample, stop_at: int, step_name: str) -> None:
    root, bare, _, request = sample
    count = 0

    def should_stop() -> bool:
        nonlocal count
        count += 1
        return count == stop_at

    class CountingTarget(FakeDeployTarget):
        def __init__(self):
            super().__init__([DeploymentStatus(id="new", status="deploying", target="fake", url="https://app.example")])
            self.configured_calls = 0

        def is_configured(self) -> bool:
            self.configured_calls += 1
            return True

    target = CountingTarget()
    agent, github, runner = make_agent(root, bare, target=target, should_stop=should_stop)
    result = run_preserving_source(agent, request, root)
    assert result.status == "stopped"
    assert step_name in " ".join(result.notes)
    assert count == stop_at
    assert target.deploy_calls == []
    if stop_at <= 2:
        assert not (root / ".agentforge" / "deploy" / "staging").exists()
    if stop_at <= 5:
        assert github.get_calls == github.create_calls == runner.calls == 0
    if stop_at == 6:
        assert github.get_calls == 1 and runner.calls == 0
    if stop_at == 7:
        assert target.configured_calls == 0 and runner.calls > 0
    if stop_at == 8:
        assert target.configured_calls == 1 and runner.calls > 0


def test_deadline_exceeded_with_injected_clock(sample) -> None:
    root, bare, _, request = sample
    ticks = iter([0.0, 0.0, 301.0])
    agent, github, runner = make_agent(root, bare, clock=lambda: next(ticks), max_seconds=300)
    result = run_preserving_source(agent, request, root)
    assert result.status == "failed" and "deadline exceeded" in " ".join(result.notes)
    assert github.get_calls == github.create_calls == runner.calls == 0
    assert not (root / ".agentforge" / "deploy" / "staging").exists()


def test_clock_failure_returns_failed_result(sample) -> None:
    root, bare, _, request = sample

    def broken_clock() -> float:
        raise RuntimeError("clock unavailable")

    agent, github, runner = make_agent(root, bare, clock=broken_clock)
    result = run_preserving_source(agent, request, root)
    assert result.status == "failed" and "clock unavailable" in " ".join(result.notes)
    assert github.get_calls == github.create_calls == runner.calls == 0


@pytest.mark.parametrize("include_ci", [False, True])
def test_include_ci_controls_workflow_file(sample, include_ci: bool) -> None:
    root, bare, _, request = sample
    agent, _, _ = make_agent(root, bare, include_ci=include_ci)
    result = run_preserving_source(agent, request, root)
    assert result.status == "done"
    workflow = root / ".agentforge" / "deploy" / "staging" / ".github" / "workflows" / "ci.yml"
    assert workflow.exists() is include_ci
    assert (".github/workflows/ci.yml" in result.files_pushed) is include_ci


def test_deploy_result_serializes_with_buildresult_fields(sample) -> None:
    root, bare, _, request = sample
    agent, _, _ = make_agent(root, bare)
    result = run_preserving_source(agent, request, root)
    data = json.loads(result.model_dump_json())
    expected = {"stage_id", "status", "files_changed", "checks", "models_used", "tokens", "cost_usd", "iterations", "notes"}
    assert expected.issubset(data)
    assert data["models_used"] == [] and data["tokens"] == 0 and data["cost_usd"] == 0.0
    assert data["iterations"] == 1


@pytest.mark.parametrize("healthy", [True, False])
def test_checks_report_each_completed_step(sample, healthy: bool) -> None:
    root, bare, _, request = sample
    target = FakeDeployTarget([DeploymentStatus(id="new", status="deploying", target="fake", url="https://app.example")])
    health = FakeHealthChecker([HealthResult(healthy, 200 if healthy else 503, 1, None if healthy else "HTTP 503")])
    agent, _, _ = make_agent(root, bare, target=target, health=health)
    result = run_preserving_source(agent, request, root)
    checks = {item.name: item.passed for item in result.checks}
    assert list(checks) == ["checkpoint_approved", "content_from_approved_tag", "secret_scan", "repo_ready", "git_push", "deploy", "health_check"]
    assert checks["content_from_approved_tag"] is False
    assert all(checks[name] for name in ("checkpoint_approved", "secret_scan", "repo_ready", "git_push", "deploy"))
    assert checks["health_check"] is healthy
    assert result.status == ("done" if healthy else "failed")


def test_checks_for_push_only_stop_after_git_push(sample) -> None:
    root, bare, _, request = sample
    agent, _, _ = make_agent(root, bare)
    result = run_preserving_source(agent, request, root)
    assert [(item.name, item.passed) for item in result.checks] == [
        ("checkpoint_approved", True),
        ("content_from_approved_tag", False),
        ("secret_scan", True),
        ("repo_ready", True),
        ("git_push", True),
    ]


@pytest.mark.parametrize("failure", ["checkpoint", "secret"])
def test_checks_fail_at_gate_before_repo_or_git(sample, failure: str) -> None:
    root, bare, _, request = sample
    if failure == "secret":
        (root / "backend" / "settings.py").write_text("key = 'nvapi-" + "s" * 24 + "'\n")
        verifier = FileCheckpointVerifier(root)
    else:
        verifier = FalseVerifier()
    agent, github, runner = make_agent(root, bare, verifier=verifier)
    result = run_preserving_source(agent, request, root)
    expected = [("checkpoint_approved", False)] if failure == "checkpoint" else [
        ("checkpoint_approved", True), ("content_from_approved_tag", False), ("secret_scan", False)
    ]
    assert [(item.name, item.passed) for item in result.checks] == expected
    assert github.get_calls == github.create_calls == runner.calls == 0


def test_generic_secret_key_warning_does_not_block_push(sample) -> None:
    root, bare, executable, request = sample
    (root / "backend" / "settings.py").write_text('SECRET_KEY = "dev-secret"\n')
    agent, _, _ = make_agent(root, bare)
    result = run_preserving_source(agent, request, root)
    assert result.status == "done" and result.commit_sha
    assert git(executable, bare, "rev-parse", "refs/heads/main") == result.commit_sha
    assert any("backend/settings.py:1" in note for note in result.notes)
    assert "dev-secret" not in result.model_dump_json()


def test_unknown_binary_type_is_allowed_with_result_note(sample) -> None:
    root, bare, executable, request = sample
    (root / "backend" / "blob.bin").write_bytes(b"\xff\x80\x01" * 20)
    agent, _, _ = make_agent(root, bare)
    result = run_preserving_source(agent, request, root)
    assert result.status == "done"
    assert git(executable, bare, "rev-parse", "refs/heads/main") == result.commit_sha
    assert "unknown binary type: backend/blob.bin" in result.notes
