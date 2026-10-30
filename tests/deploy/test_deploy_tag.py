"""Tagged source guarantees for DeployAgent; Git stays in temporary repositories."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from engine.agents.deploy import stage_tag_archive
from engine.agents.deploy_agent import DeployAgent
from engine.agents.deploy_git import GitPublisher, PublishResult
from engine.agents.deploy_github import FakeGitHubClient
from engine.agents.deploy_runner import SubprocessRunner
from engine.agents.deploy_models import DeployRequest, RepoInfo
from engine.agents.deploy_models import DeployResult
from engine.agents.deploy_support import DeploymentHistory, DisabledTarget, FakeHealthChecker, FileCheckpointVerifier
from scripts import run_deploy_agent as deploy_cli


def make_agent(workspace: Path, *, content_mode: str = "tag") -> DeployAgent:
    return DeployAgent(
        github=FakeGitHubClient(), publisher=GitPublisher(SubprocessRunner()),
        health=FakeHealthChecker(), target=DisabledTarget(),
        history=DeploymentHistory(workspace / ".agentforge" / "deploy" / "deployments.json"),
        verifier=FileCheckpointVerifier(workspace), content_mode=content_mode,
    )


def test_content_mode_defaults_to_tag(tmp_path: Path) -> None:
    assert make_agent(tmp_path).content_mode == "tag"
    assert make_agent(tmp_path, content_mode="working_tree").content_mode == "working_tree"
    with pytest.raises(ValueError, match="content_mode"):
        make_agent(tmp_path, content_mode="other")


GIT_FALLBACK = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "native" / "git" / "cmd" / "git.exe"


def git_executable() -> str:
    executable = shutil.which("git") or (str(GIT_FALLBACK) if GIT_FALLBACK.exists() else None)
    if executable is None:
        pytest.skip("git executable unavailable")
    return executable


def git(workspace: Path, *args: str) -> str:
    return subprocess.run(
        [git_executable(), *args], cwd=workspace, check=True, capture_output=True, text=True, timeout=15
    ).stdout.strip()


def workspace_with_checkpoint(tmp_path: Path, *, repository: bool = True, tag: bool = True, commit: str | None = None, publish_metadata: bool = False) -> Path:
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    (workspace / "backend").mkdir()
    (workspace / "backend" / "app.py").write_bytes(b"approved content\n")
    (workspace / "backend" / "deleted.py").write_bytes(b"approved second file\n")
    if publish_metadata:
        (workspace / "README.md").write_bytes(b"# approved arcade\n")
        (workspace / "LICENSE").write_bytes(b"synthetic license\n")
        (workspace / ".gitignore").write_bytes(b".agentforge/\n")
    if repository:
        git(workspace, "init", "-b", "main")
        to_add = ["backend/app.py", "backend/deleted.py"]
        if publish_metadata:
            to_add.extend(["README.md", "LICENSE", ".gitignore"])
        git(workspace, "add", "--", *to_add)
        git(workspace, "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", "approved")
        if tag:
            git(workspace, "tag", "cp-1")
    checkpoint = workspace / ".agentforge" / "checkpoints"
    checkpoint.mkdir(parents=True)
    data = {"id": "cp_1", "run_id": "run_1", "git_tag": "cp-1"}
    if commit is not None:
        data["commit"] = commit
    (checkpoint / "cp_1.json").write_text(json.dumps(data))
    return workspace


def request() -> DeployRequest:
    return DeployRequest(run_id="run_1", stage_id="final", checkpoint_id="cp_1", git_tag="cp-1", repo_name="arcade")


class GuardedGitHub:
    calls = 0

    def get_repo(self, *args):
        self.calls += 1
        raise AssertionError("GitHub must not be called")


class GuardedPublisher:
    def __init__(self) -> None:
        self.runner = SubprocessRunner()
        self.calls = 0

    def publish(self, *args):
        self.calls += 1
        raise AssertionError("push must not be called")


@pytest.mark.parametrize(("repository", "tag"), [(True, False), (False, False)])
def test_missing_approved_tag_stops_before_staging_or_network(tmp_path: Path, repository: bool, tag: bool) -> None:
    workspace = workspace_with_checkpoint(tmp_path, repository=repository, tag=tag)
    github, publisher = GuardedGitHub(), GuardedPublisher()
    agent = make_agent(workspace)
    agent.github, agent.publisher = github, publisher
    result = agent.run(request(), workspace)
    assert result.status == "failed"
    assert "approved tag not found in the workspace repository" in result.notes
    assert [(item.name, item.passed) for item in result.checks][-1] == ("content_from_approved_tag", False)
    assert github.calls == publisher.calls == 0
    assert not (workspace / ".agentforge" / "deploy" / "staging").exists()


def test_checkpoint_commit_mismatch_stops_before_staging_or_network(tmp_path: Path) -> None:
    workspace = workspace_with_checkpoint(tmp_path, commit="0" * 40)
    github, publisher = GuardedGitHub(), GuardedPublisher()
    agent = make_agent(workspace)
    agent.github, agent.publisher = github, publisher
    result = agent.run(request(), workspace)
    assert result.status == "failed"
    assert "approved checkpoint commit does not match the tag commit" in result.notes
    assert [(item.name, item.passed) for item in result.checks][-1] == ("content_from_approved_tag", False)
    assert github.calls == publisher.calls == 0
    assert not (workspace / ".agentforge" / "deploy" / "staging").exists()


def test_archive_exports_tagged_tree_instead_of_working_tree(tmp_path: Path) -> None:
    workspace = workspace_with_checkpoint(tmp_path)
    commit = git(workspace, "rev-parse", "refs/tags/cp-1^{commit}")
    tagged_app = subprocess.run(
        [git_executable(), "show", "cp-1:backend/app.py"], cwd=workspace, check=True, capture_output=True, timeout=15
    ).stdout
    tagged_deleted = subprocess.run(
        [git_executable(), "show", "cp-1:backend/deleted.py"], cwd=workspace, check=True, capture_output=True, timeout=15
    ).stdout
    (workspace / "backend" / "app.py").write_bytes(b"modified working content\n")
    (workspace / "backend" / "untracked.py").write_bytes(b"untracked\n")
    (workspace / "backend" / "deleted.py").unlink()
    staged, excluded, notes = stage_tag_archive(workspace, commit, SubprocessRunner())
    assert (staged / "backend" / "app.py").read_bytes() == tagged_app
    assert (staged / "backend" / "deleted.py").read_bytes() == tagged_deleted
    assert not (staged / "backend" / "untracked.py").exists()
    assert not (staged / ".git").exists()
    assert excluded == notes == []


def test_archive_skips_symlink_entry_without_writing_outside_staging(tmp_path: Path) -> None:
    workspace = workspace_with_checkpoint(tmp_path, tag=False)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside remains unchanged")
    link = workspace / "backend" / "outside-link"
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"OS cannot create symlinks here: {exc}")
    git(workspace, "add", "--", "backend/outside-link")
    git(workspace, "-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "-m", "symlink fixture")
    commit = git(workspace, "rev-parse", "HEAD")
    staged, _, notes = stage_tag_archive(workspace, commit, SubprocessRunner())
    assert not (staged / "backend" / "outside-link").exists()
    assert "skipped archive symlink: backend/outside-link" in notes
    assert outside.read_text() == "outside remains unchanged"


def test_archive_enforces_file_limit_before_extraction(tmp_path: Path) -> None:
    workspace = workspace_with_checkpoint(tmp_path)
    commit = git(workspace, "rev-parse", "refs/tags/cp-1^{commit}")
    with pytest.raises(ValueError, match="archive file exceeds"):
        stage_tag_archive(workspace, commit, SubprocessRunner(), max_file_bytes=10)
    assert not (workspace / ".agentforge" / "deploy" / "staging").exists()


def test_source_commit_is_reported_and_in_publish_message(tmp_path: Path) -> None:
    workspace = workspace_with_checkpoint(tmp_path)
    source_commit = git(workspace, "rev-parse", "refs/tags/cp-1^{commit}")

    class RecordingPublisher:
        def __init__(self) -> None:
            self.runner = SubprocessRunner()
            self.message = ""

        def publish(self, staged, repo, branch, tag, message):
            self.message = message
            (staged / ".git").mkdir()
            return PublishResult("a" * 40, tag, True, ["backend/app.py", "backend/deleted.py"])

    publisher = RecordingPublisher()
    agent = make_agent(workspace)
    agent.publisher = publisher
    result = agent.run(request(), workspace)
    assert result.status == "done"
    assert result.source_commit == source_commit
    assert result.model_dump()["source_commit"] == source_commit
    assert publisher.message == f"AgentForge: final approved (cp_1), source {source_commit}"


def test_working_tree_mode_publishes_current_folder_with_unverified_note(tmp_path: Path) -> None:
    workspace = workspace_with_checkpoint(tmp_path)
    (workspace / "backend" / "app.py").write_bytes(b"current working content\n")
    (workspace / "backend" / "untracked.py").write_bytes(b"new file\n")

    class RecordingPublisher:
        def __init__(self) -> None:
            self.seen: dict[str, bytes] = {}

        def publish(self, staged, repo, branch, tag, message):
            self.seen = {
                file.relative_to(staged).as_posix(): file.read_bytes()
                for file in staged.rglob("*") if file.is_file()
            }
            (staged / ".git").mkdir()
            return PublishResult("a" * 40, tag, True, sorted(self.seen))

    publisher = RecordingPublisher()
    agent = make_agent(workspace, content_mode="working_tree")
    agent.publisher = publisher
    result = agent.run(request(), workspace)
    assert result.status == "done"
    assert publisher.seen["backend/app.py"] == b"current working content\n"
    assert publisher.seen["backend/untracked.py"] == b"new file\n"
    assert "UNVERIFIED: published working tree, not the approved tag" in result.notes
    assert ("content_from_approved_tag", False) in [(item.name, item.passed) for item in result.checks]
    assert result.source_commit is None


def cli_args(workspace: Path) -> list[str]:
    return [
        "--workspace", str(workspace), "--run-id", "run_1", "--stage-id", "final",
        "--checkpoint-id", "cp_1", "--git-tag", "cp-1", "--repo-name", "arcade",
    ]


def test_cli_dry_run_uses_tagged_tree_when_repository_exists(tmp_path: Path, capsys) -> None:
    workspace = workspace_with_checkpoint(tmp_path)
    (workspace / "backend" / "app.py").write_bytes(b"unapproved edit\n")
    (workspace / "backend" / "untracked.py").write_bytes(b"unapproved new file\n")
    (workspace / "backend" / "deleted.py").unlink()
    assert deploy_cli.main(cli_args(workspace)) == 0
    output = capsys.readouterr().out
    assert "Dry run uses approved tag commit" in output
    assert "backend/deleted.py" in output
    assert "backend/untracked.py" not in output
    assert "DRY RUN USES WORKING TREE" not in output


def test_cli_dry_run_non_repo_fallback_is_explicit(tmp_path: Path, capsys) -> None:
    workspace = workspace_with_checkpoint(tmp_path, repository=False)
    assert deploy_cli.main(cli_args(workspace)) == 0
    assert "DRY RUN USES WORKING TREE; a real push requires the approved tag" in capsys.readouterr().out


def test_cli_dry_run_repo_with_missing_tag_does_not_fallback(tmp_path: Path, capsys) -> None:
    workspace = workspace_with_checkpoint(tmp_path, tag=False)
    assert deploy_cli.main(cli_args(workspace)) == 1
    assert "approved tag not found" in capsys.readouterr().out


@pytest.mark.parametrize("allow_working_tree", [False, True])
def test_cli_real_push_defaults_to_tag_with_explicit_override(tmp_path: Path, monkeypatch, capsys, allow_working_tree: bool) -> None:
    workspace = workspace_with_checkpoint(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-cli-token-123456789")
    modes: list[str] = []

    class RecordingAgent:
        def __init__(self, **kwargs):
            modes.append(kwargs["content_mode"])

        def run(self, request, workspace_root):
            return DeployResult(stage_id="final", status="done")

    monkeypatch.setattr(deploy_cli, "DeployAgent", RecordingAgent)
    command = [*cli_args(workspace), "--push", "--yes"]
    if allow_working_tree:
        command.append("--allow-working-tree")
    assert deploy_cli.main(command) == 0
    output = capsys.readouterr().out
    assert modes == ["working_tree" if allow_working_tree else "tag"]
    assert ("WARNING: UNVERIFIED WORKING TREE PUSH" in output) is allow_working_tree


def git_bytes(workspace: Path, *args: str) -> bytes:
    return subprocess.run(
        [git_executable(), *args], cwd=workspace, check=True, capture_output=True, timeout=15
    ).stdout


def source_files_hash(workspace: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(workspace.rglob("*")):
        if not file.is_file() or any(part in {".git", ".agentforge"} for part in file.relative_to(workspace).parts):
            continue
        digest.update(file.relative_to(workspace).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def test_tag_mode_pushes_only_approved_blob_bytes_and_leaves_source_repo_unchanged(tmp_path: Path) -> None:
    workspace = workspace_with_checkpoint(tmp_path, publish_metadata=True)
    source_commit = git(workspace, "rev-parse", "refs/tags/cp-1^{commit}")
    checkpoint = workspace / ".agentforge" / "checkpoints" / "cp_1.json"
    checkpoint.write_text(json.dumps({"id": "cp_1", "run_id": "run_1", "git_tag": "cp-1", "commit": source_commit}))
    (workspace / "backend" / "app.py").write_bytes(b"unapproved edited text\n")
    (workspace / "backend" / "untracked.py").write_bytes(b"untracked file\n")
    (workspace / "backend" / "deleted.py").unlink()
    before = (
        git(workspace, "rev-parse", "HEAD"), git(workspace, "symbolic-ref", "--short", "HEAD"),
        git(workspace, "show-ref"), git(workspace, "status", "--porcelain"), source_files_hash(workspace),
    )
    bare = tmp_path / "bare remote with spaces.git"
    subprocess.run([git_executable(), "init", "--bare", str(bare)], cwd=tmp_path, check=True, capture_output=True, timeout=15)
    repo = RepoInfo(html_url="https://github.com/fake/arcade", clone_url=str(bare), default_branch="main")
    agent = make_agent(workspace)
    agent.github = FakeGitHubClient({"arcade": repo})
    result = agent.run(request(), workspace)
    assert result.status == "done", result.notes
    assert result.source_commit == source_commit
    assert result.commit_sha == git(bare, "rev-parse", "refs/heads/main")
    committed = git(bare, "log", "-1", "--format=%B", "refs/heads/main")
    assert committed == f"AgentForge: final approved (cp_1), source {source_commit}"
    tagged_files = git(workspace, "ls-tree", "-r", "--name-only", "cp-1").splitlines()
    pushed_files = git(bare, "ls-tree", "-r", "--name-only", "refs/heads/main").splitlines()
    assert pushed_files == tagged_files == sorted(result.files_pushed)
    for name in tagged_files:
        assert git_bytes(bare, "show", f"refs/heads/main:{name}") == git_bytes(workspace, "show", f"cp-1:{name}")
    assert "backend/untracked.py" not in pushed_files
    assert b"unapproved edited text" not in git_bytes(bare, "show", "refs/heads/main:backend/app.py")
    after = (
        git(workspace, "rev-parse", "HEAD"), git(workspace, "symbolic-ref", "--short", "HEAD"),
        git(workspace, "show-ref"), git(workspace, "status", "--porcelain"), source_files_hash(workspace),
    )
    assert after == before
