"""Offline Git and mocked GitHub tests for deploy phase 2."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from engine.agents.deploy import redact, stage_workspace
from engine.agents.deploy_git import GitPublishError, GitPublisher
from engine.agents.deploy_github import FakeGitHubClient, GitHubError, HttpxGitHubClient, NotConfigured
from engine.agents.deploy_models import RepoInfo
from engine.agents.deploy_runner import RunResult, SubprocessRunner


GIT_FALLBACK = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "native" / "git" / "cmd" / "git.exe"
TOKEN = "ghp_" + "b" * 36


class RecordingRunner:
    def __init__(self) -> None:
        self.inner = SubprocessRunner()
        self.calls: list[tuple[list[str], dict[str, str] | None]] = []

    def run(self, cmd: list[str], workdir: Path, timeout: float, env: dict[str, str] | None = None) -> RunResult:
        self.calls.append((list(cmd), dict(env) if env else None))
        return self.inner.run(cmd, workdir, timeout, env)


@pytest.fixture
def git_exe(monkeypatch: pytest.MonkeyPatch) -> str:
    executable = shutil.which("git") or (str(GIT_FALLBACK) if GIT_FALLBACK.exists() else None)
    if executable is None:
        pytest.skip("git executable unavailable")
    monkeypatch.setenv("PATH", str(Path(executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    return executable


def git(executable: str, cwd: Path, *args: str) -> str:
    result = subprocess.run([executable, *args], cwd=cwd, capture_output=True, text=True, timeout=15, check=True)
    return result.stdout.strip()


@pytest.fixture
def project(tmp_path: Path, git_exe: str, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, RepoInfo, str]:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    workspace = tmp_path / "project with space"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('hello')\n")
    (workspace / "README.md").write_text("example\n")
    staged = stage_workspace(workspace)
    bare = tmp_path / "remote repo.git"
    git(git_exe, tmp_path, "init", "--bare", str(bare))
    repo = RepoInfo(html_url="https://github.com/example/project", clone_url=str(bare), default_branch="main")
    return staged, bare, repo, git_exe


def publish(project: tuple[Path, Path, RepoInfo, str], runner: RecordingRunner | None = None, tag: str = "cp-1"):
    staged, _, repo, _ = project
    return GitPublisher(runner or RecordingRunner()).publish(
        staged, repo, "main", tag, "AgentForge: final approved (checkpoint_1)"
    )


def test_publish_to_local_bare_remote(project: tuple[Path, Path, RepoInfo, str]) -> None:
    _, bare, _, executable = project
    result = publish(project)
    assert git(executable, bare, "rev-parse", "refs/heads/main") == result.commit_sha
    assert git(executable, bare, "rev-parse", "refs/tags/cp-1^{commit}") == result.commit_sha


def test_commit_message_and_identity(project: tuple[Path, Path, RepoInfo, str]) -> None:
    staged, _, _, executable = project
    publish(project)
    assert git(executable, staged, "log", "-1", "--format=%s") == "AgentForge: final approved (checkpoint_1)"
    assert git(executable, staged, "log", "-1", "--format=%an <%ae>") == "AgentForge <agentforge@users.noreply.github.com>"


def test_rerun_is_idempotent(project: tuple[Path, Path, RepoInfo, str]) -> None:
    staged, bare, _, executable = project
    first = publish(project)
    second = publish(project)
    assert second.commit_sha == first.commit_sha
    assert git(executable, staged, "rev-list", "--count", "HEAD") == "1"
    assert git(executable, bare, "tag", "--list") == "cp-1"


def test_changed_files_new_commit_same_tag_fails(project: tuple[Path, Path, RepoInfo, str]) -> None:
    staged, bare, _, executable = project
    first = publish(project)
    (staged / "app.py").write_text("print('changed')\n")
    with pytest.raises(GitPublishError, match="different commit"):
        publish(project)
    assert git(executable, bare, "rev-parse", "refs/tags/cp-1^{commit}") == first.commit_sha


def test_remote_tag_on_different_commit_fails_before_branch_push(project: tuple[Path, Path, RepoInfo, str], tmp_path: Path) -> None:
    _, bare, repo, executable = project
    first = publish(project)
    other_workspace = tmp_path / "other workspace"
    other_workspace.mkdir()
    (other_workspace / "app.py").write_text("print('different')\n")
    other_staged = stage_workspace(other_workspace)
    with pytest.raises(GitPublishError, match="remote checkpoint tag"):
        GitPublisher(RecordingRunner()).publish(other_staged, repo, "main", "cp-1", "AgentForge: final approved (checkpoint_1)")
    assert git(executable, bare, "rev-parse", "refs/heads/main") == first.commit_sha


def test_no_force_push_anywhere(project: tuple[Path, Path, RepoInfo, str]) -> None:
    runner = RecordingRunner()
    publish(project, runner)
    assert all("--force" not in arg and "--force-with-lease" not in arg and arg != "-f" and not arg.startswith("+refs") for cmd, _ in runner.calls for arg in cmd)


def test_files_pushed_lists_tracked_files(project: tuple[Path, Path, RepoInfo, str]) -> None:
    assert publish(project).files_pushed == ["README.md", "app.py"]


def test_remote_url_has_no_credentials(project: tuple[Path, Path, RepoInfo, str]) -> None:
    staged, _, repo, executable = project
    publish(project)
    assert git(executable, staged, "remote", "get-url", "origin") == repo.clone_url


def test_git_config_has_no_token(project: tuple[Path, Path, RepoInfo, str]) -> None:
    staged, _, _, _ = project
    publish(project)
    config = (staged / ".git" / "config").read_text()
    encoded = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert TOKEN not in config and encoded not in config


def test_token_not_in_argv(project: tuple[Path, Path, RepoInfo, str]) -> None:
    runner = RecordingRunner()
    publish(project, runner)
    encoded = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert all(TOKEN not in arg and encoded not in arg for cmd, _ in runner.calls for arg in cmd)


def test_token_only_in_git_config_value_env(project: tuple[Path, Path, RepoInfo, str]) -> None:
    runner = RecordingRunner()
    publish(project, runner)
    encoded = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    envs = [env for _, env in runner.calls if env and "GIT_CONFIG_VALUE_0" in env]
    assert len(envs) == 3
    assert all(env["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {encoded}" for env in envs)
    assert all(encoded not in value and TOKEN not in value for _, env in runner.calls if env for key, value in env.items() if key != "GIT_CONFIG_VALUE_0")


def test_runner_uses_list_and_no_shell(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    observed: dict = {}

    def fake_run(cmd, **kwargs):
        observed.update(cmd=cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert SubprocessRunner().run(["git", "--version"], tmp_path, 2).stdout == "ok"
    assert isinstance(observed["cmd"], list) and observed["shell"] is False


def test_runner_timeout_returns_timed_out_not_exception(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"], output=b"partial", stderr=b"late")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = SubprocessRunner().run(["git", "push"], tmp_path, 0.1)
    assert result.returncode == 124 and result.timed_out and result.stdout == "partial"


def test_runner_output_is_redacted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    result = SubprocessRunner().run(
        [sys.executable, "-c", "import os; print(os.environ['GITHUB_TOKEN'])"], tmp_path, 3
    )
    assert TOKEN not in result.stdout and "[REDACTED]" in result.stdout


def test_redact_reads_environment_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    import engine.agents.deploy as deploy_module

    first = "ghp_" + "a" * 36
    second = "ghp_" + "c" * 36
    monkeypatch.setenv("GITHUB_TOKEN", first)
    encoded = base64.b64encode(f"x-access-token:{first}".encode()).decode()
    assert first not in deploy_module.redact(first + encoded)
    monkeypatch.setenv("GITHUB_TOKEN", second)
    assert first not in deploy_module.redact(first + " " + second)
    assert second not in deploy_module.redact(first + " " + second)


def response_repo(name: str = "sample") -> dict:
    return {"html_url": f"https://github.com/demo/{name}", "clone_url": f"https://github.com/demo/{name}.git", "default_branch": "main"}


def test_create_repo_success_201(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=response_repo())

    repo = HttpxGitHubClient(httpx.MockTransport(handler)).create_repo("sample", True, "description")
    assert repo.html_url.endswith("/sample") and len(seen) == 1
    assert b'"auto_init":false' in seen[0].content


def test_create_repo_422_already_exists_uses_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.method == "POST":
            return httpx.Response(422, json={"errors": [{"message": "name already exists"}]})
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "demo"})
        return httpx.Response(200, json=response_repo())

    repo = HttpxGitHubClient(httpx.MockTransport(handler)).create_repo("sample", True, "desc")
    assert repo.clone_url.endswith("sample.git")
    assert seen == ["/user/repos", "/user", "/repos/demo/sample"]


def test_401_not_retried_and_message_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401, text=TOKEN)

    with pytest.raises(GitHubError) as caught:
        HttpxGitHubClient(httpx.MockTransport(handler), sleep=lambda _: None).create_repo("sample", True, "")
    assert len(calls) == 1 and TOKEN not in str(caught.value)
    assert "Contents write" in str(caught.value)


def test_403_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(403)

    with pytest.raises(GitHubError):
        HttpxGitHubClient(httpx.MockTransport(handler), sleep=lambda _: None).get_repo("sample")
    assert len(calls) == 1


def test_503_retried_three_times_then_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    calls, delays = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(GitHubError) as caught:
        HttpxGitHubClient(httpx.MockTransport(handler), sleep=delays.append).create_repo("sample", True, "")
    assert caught.value.status_code == 503 and len(calls) == 3 and delays == [0.5, 1.0]


def test_connection_error_retried_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    calls, delays = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("temporary failure")
        return httpx.Response(201, json=response_repo())

    assert HttpxGitHubClient(httpx.MockTransport(handler), sleep=delays.append).create_repo("sample", True, "").default_branch == "main"
    assert len(calls) == 2 and delays == [0.5]


def test_missing_token_raises_notconfigured_without_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(NotConfigured, match="not configured") as caught:
        HttpxGitHubClient(httpx.MockTransport(lambda request: pytest.fail("unexpected request"))).get_repo("x")
    assert TOKEN not in str(caught.value)


def test_token_never_in_repr_or_exception_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    client = HttpxGitHubClient(httpx.MockTransport(lambda request: httpx.Response(401, text=TOKEN)))
    assert TOKEN not in repr(client)
    with pytest.raises(GitHubError) as caught:
        client.get_repo("x")
    assert TOKEN not in repr(caught.value) and TOKEN not in str(caught.value)


def test_headers_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers)
        return httpx.Response(201, json=response_repo())

    HttpxGitHubClient(httpx.MockTransport(handler)).create_repo("sample", True, "")
    headers = seen[0]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert headers["User-Agent"] == "agentforge-deploy"


def test_fake_client_contract() -> None:
    client = FakeGitHubClient()
    assert client.get_repo("sample") is None
    first = client.create_repo("sample", True, "description")
    assert client.get_repo("sample") == first
    assert client.create_repo("sample", True, "description") == first
    assert client.create_calls == 2 and len(client.repos) == 1
