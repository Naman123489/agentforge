"""Hardening Batch 2/3 (staging tricks, concurrency, Git robustness, input validation), written before the fixes.

Junction tests run without admin rights on Windows (unlike the skipped symlink tests): Path.is_symlink() is False
for a junction and rglob() walks through it, so the old checks did not see it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from engine.agents.deploy import stage_tag_archive, stage_workspace
from engine.agents.deploy_agent import DeployAgent
from engine.agents.deploy_git import GitPublisher
from engine.agents.deploy_models import DeployRequest
from engine.agents.deploy_runner import SubprocessRunner
from engine.agents.deploy_support import DeploymentHistory, DisabledTarget, FakeHealthChecker, FileCheckpointVerifier
from tests.deploy.test_deploy_run import LocalGitHubClient


def link_dir(target: Path, link: Path) -> None:
    """A directory link the OS lets us create: junction on Windows, symlink elsewhere."""
    try:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    except ImportError:
        link.symlink_to(target, target_is_directory=True)


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("must survive", encoding="utf-8")
    return root, outside


# ---- attack 4: staging tricks ------------------------------------------------------------------------
def test_junction_inside_workspace_is_refused_not_followed(ws):
    root, outside = ws
    link_dir(outside, root / "backend" / "vendor")
    with pytest.raises(ValueError, match="link"):
        stage_workspace(root)
    assert not (root / ".agentforge" / "deploy" / "staging" / "backend" / "vendor" / "keep.txt").exists()


def test_linked_deploy_directory_cannot_redirect_staging_deletes(ws):
    root, outside = ws
    (outside / "staging").mkdir()
    (outside / "staging" / "keep.txt").write_text("must survive", encoding="utf-8")
    (root / ".agentforge").mkdir()
    link_dir(outside, root / ".agentforge" / "deploy")  # .agentforge/deploy/staging -> outside/staging
    with pytest.raises(ValueError, match="link"):
        stage_workspace(root)
    assert (outside / "staging" / "keep.txt").read_text(encoding="utf-8") == "must survive"


def test_linked_staging_parent_is_refused_in_tag_mode(ws):
    root, outside = ws
    (outside / "staging").mkdir()
    (outside / "staging" / "keep.txt").write_text("x", encoding="utf-8")
    link_dir(outside, root / ".agentforge")
    with pytest.raises(ValueError, match="link"):
        stage_tag_archive(root, "0" * 40, SubprocessRunner())
    assert (outside / "staging" / "keep.txt").exists()


# ---- shared agent setup (local bare remote; no network) ---------------------------------------------
@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    root = tmp_path / "w"
    (root / "backend").mkdir(parents=True)
    (root / "backend" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    cps = root / ".agentforge" / "checkpoints"
    cps.mkdir(parents=True)
    (cps / "cp_1.json").write_text(json.dumps({"id": "cp_1", "run_id": "run_1", "git_tag": "cp-1"}), encoding="utf-8")
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    req = DeployRequest(run_id="run_1", stage_id="final", checkpoint_id="cp_1", git_tag="cp-1", repo_name="arcade")
    return root, bare, req


def agent_for(root: Path, bare: Path, publisher=None, verifier=None, mode="working_tree") -> DeployAgent:
    return DeployAgent(github=LocalGitHubClient(bare), publisher=publisher or GitPublisher(SubprocessRunner()),
                       health=FakeHealthChecker(), target=DisabledTarget(),
                       history=DeploymentHistory(root / ".agentforge" / "deploy" / "deployments.json"),
                       verifier=verifier or FileCheckpointVerifier(root), content_mode=mode)


# ---- attack 5: concurrency ----------------------------------------------------------------------------
class SlowPublisher(GitPublisher):
    def __init__(self, gate: threading.Event):
        super().__init__(SubprocessRunner())
        self.gate = gate

    def publish(self, *a, **k):
        self.gate.wait(10)
        return super().publish(*a, **k)


def test_second_concurrent_deployment_of_the_same_workspace_is_refused(repo):
    root, bare, req = repo
    gate = threading.Event()
    first = agent_for(root, bare, publisher=SlowPublisher(gate))
    out: dict = {}
    t = threading.Thread(target=lambda: out.setdefault("first", first.run(req, root)))
    t.start()
    deadline = time.monotonic() + 10
    while not (root / ".agentforge" / "deploy" / "deploy.lock").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    second = agent_for(root, bare).run(req, root)
    gate.set()
    t.join(30)
    assert second.status == "failed" and any("another deployment" in n for n in second.notes)
    assert out["first"].status == "done", out["first"].notes
    assert not (root / ".agentforge" / "deploy" / "deploy.lock").exists()  # released
    assert agent_for(root, bare).run(req, root).status == "done"  # and usable again


def test_stale_lock_from_a_dead_process_is_recovered(repo):
    root, bare, req = repo
    lock = root / ".agentforge" / "deploy" / "deploy.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 2**22 + 12345, "created": time.time() - 7200}), encoding="utf-8")
    assert agent_for(root, bare).run(req, root).status == "done"


# ---- attack 6: Git robustness ------------------------------------------------------------------------
def test_hooks_in_the_cached_publish_repo_never_run(repo, tmp_path):
    root, bare, req = repo
    assert agent_for(root, bare).run(req, root).status == "done"
    cache = next((root / ".agentforge" / "deploy" / "git-cache").iterdir())
    marker = tmp_path / "hook-ran.txt"
    hook = cache / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\n", encoding="utf-8")
    hook.chmod(0o755)
    (root / "backend" / "app.py").write_text("print('changed')\n", encoding="utf-8")  # forces a new commit
    agent_for(root, bare).run(req, root)
    assert not marker.exists()


def test_git_commands_enable_long_paths_and_disable_hooks():
    calls: list[list[str]] = []

    class Rec:
        def run(self, cmd, workdir, timeout, env=None):
            calls.append(cmd)
            from engine.agents.deploy_runner import RunResult
            return RunResult(1, "", "stop")

    with pytest.raises(Exception):
        GitPublisher(Rec()).publish(Path.cwd(), __import__("engine.agents.deploy_models", fromlist=["RepoInfo"]).RepoInfo(
            html_url="https://x", clone_url="https://x.git"), "main", "cp-1", "m")
    assert calls and all("core.longpaths=true" in c and any(a.startswith("core.hooksPath=") for a in c) for c in calls)


# ---- input validation ----------------------------------------------------------------------------------
@pytest.mark.parametrize("field,value", [
    ("repo_name", "x" * 101), ("repo_name", "bad\nname"), ("health_check_path", "//evil.example/x"),
    ("health_check_path", "/a/../b"), ("health_check_path", "health"), ("run_id", "run 1;rm"),
    ("checkpoint_id", "../cp"), ("git_tag", "-delete"), ("stage_id", "s" * 200),
])
def test_request_fields_are_validated(field, value):
    base = dict(run_id="run_1", stage_id="final", checkpoint_id="cp_1", git_tag="cp-1", repo_name="arcade")
    with pytest.raises(ValueError):
        DeployRequest(**{**base, field: value})


# ---- checkpoint verification: the verifier's recorded commit is mandatory when it provides one ---------
class CommitVerifier:
    def __init__(self, commit: str):
        self.commit = commit

    def is_approved(self, run_id, checkpoint_id, git_tag):
        return True

    def approved_commit(self, run_id, checkpoint_id):
        return self.commit


def _tagged_repo(root: Path) -> str:
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"], ["add", "-A"],
                 ["commit", "-qm", "c"], ["tag", "-a", "cp-1", "-m", "green"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return subprocess.run(["git", "rev-parse", "cp-1^{commit}"], cwd=root, capture_output=True, text=True).stdout.strip()


def test_tag_mode_refuses_when_the_tag_no_longer_points_at_the_approved_commit(repo):
    root, bare, req = repo
    (root / ".gitignore").write_text(".agentforge/\n", encoding="utf-8")
    real = _tagged_repo(root)
    bad = agent_for(root, bare, verifier=CommitVerifier("f" * 40), mode="tag").run(req, root)
    assert bad.status == "failed" and any("does not match" in n for n in bad.notes)
    good = agent_for(root, bare, verifier=CommitVerifier(real), mode="tag").run(req, root)
    assert good.status == "done" and good.source_commit == real
