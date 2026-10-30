"""CLI preview and consent tests; no real network or push."""

from __future__ import annotations

import hashlib
import shutil
import socket
from pathlib import Path

import pytest

from engine.agents.deploy_git import GitPublisher
from scripts.run_deploy_agent import main


FIXTURE = Path(__file__).parent / "fixtures" / "sample_workspace"


def args(workspace: Path) -> list[str]:
    return [
        "--workspace", str(workspace), "--run-id", "run_1", "--stage-id", "deploy",
        "--checkpoint-id", "cp_1", "--git-tag", "cp-1", "--repo-name", "arcade",
    ]


def tree_hash(workspace: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(path for path in workspace.rglob("*") if path.is_file()):
        digest.update(file.relative_to(workspace).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def copy_fixture(tmp_path: Path) -> Path:
    workspace = tmp_path / "arcade workspace with spaces"
    shutil.copytree(FIXTURE, workspace)
    (workspace / ".env").write_text("FAKE_SETTING=1\n")
    return workspace


def test_dry_run_clean_is_read_only_and_never_uses_network_or_git(tmp_path, monkeypatch, capsys) -> None:
    workspace = copy_fixture(tmp_path)
    before = tree_hash(workspace)
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    monkeypatch.setattr(GitPublisher, "publish", lambda *a, **k: (_ for _ in ()).throw(AssertionError("git push used")))
    assert main(args(workspace)) == 0
    output = capsys.readouterr().out
    assert "backend/app.py" in output and "frontend/public/sprite.png" in output
    assert "node_modules/foo/index.js" in output and ".env" in output
    assert "backend/app.py:8" in output
    assert "Checkpoint approval is not required" in output
    assert "dev-secret-change-me" not in output
    assert tree_hash(workspace) == before


def test_dry_run_block_prints_filename_only_and_returns_one(tmp_path, capsys) -> None:
    workspace = copy_fixture(tmp_path)
    planted = "ghp_" + "b" * 36
    (workspace / "leaked.txt").write_text(planted)
    before = tree_hash(workspace)
    assert main(args(workspace)) == 1
    output = capsys.readouterr().out
    assert "leaked.txt" in output
    assert planted not in output and "dev-secret-change-me" not in output
    assert tree_hash(workspace) == before


def test_blocked_file_never_appears_under_would_push_heading(tmp_path, capsys) -> None:
    workspace = copy_fixture(tmp_path)
    (workspace / "leaked.txt").write_text("ghp_" + "b" * 36)
    assert main(args(workspace)) == 1
    output = capsys.readouterr().out
    assert "Files that would be pushed:" not in output
    assert "PUSH WOULD BE BLOCKED. Nothing would be pushed.\nBlocks (file name only):\n  leaked.txt" in output


def test_push_without_token_stops_before_staging(tmp_path, monkeypatch, capsys) -> None:
    workspace = copy_fixture(tmp_path)
    before = tree_hash(workspace)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert main([*args(workspace), "--push"]) == 1
    assert "GITHUB_TOKEN is not set" in capsys.readouterr().out
    assert tree_hash(workspace) == before
    assert not (workspace / ".agentforge").exists()


def test_push_answer_no_never_starts_push_or_network(tmp_path, monkeypatch, capsys) -> None:
    workspace = copy_fixture(tmp_path)
    before = tree_hash(workspace)
    token = "fake-prompt-token-123456789"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    monkeypatch.setattr("builtins.input", lambda: "no")
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network used")))
    monkeypatch.setattr(GitPublisher, "publish", lambda *a, **k: (_ for _ in ()).throw(AssertionError("git push used")))
    assert main([*args(workspace), "--push"]) == 1
    output = capsys.readouterr().out
    assert "Push cancelled" in output
    assert token not in output
    assert tree_hash(workspace) == before


@pytest.mark.parametrize(("flag", "value"), [("--repo-name", "-bad"), ("--git-tag", "bad..tag")])
def test_invalid_name_returns_one_with_clear_message(tmp_path, capsys, flag, value) -> None:
    workspace = copy_fixture(tmp_path)
    command = args(workspace)
    command[command.index(flag) + 1] = value
    assert main(command) == 1
    assert f"invalid {flag[2:]}" in capsys.readouterr().out
    assert not (workspace / ".agentforge").exists()


def test_push_requires_matching_checkpoint_file(tmp_path, monkeypatch, capsys) -> None:
    workspace = copy_fixture(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "fake-checkpoint-token-123456789")
    assert main([*args(workspace), "--push", "--yes"]) == 1
    assert "No approved checkpoint file" in capsys.readouterr().out
    assert not (workspace / ".agentforge").exists()
