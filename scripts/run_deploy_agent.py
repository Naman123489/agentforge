"""Preview a deploy safely, or push a verified checkpoint with explicit consent."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

# Direct invocation from any working directory must find the copied project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.agents.deploy import _excluded, redact, resolve_tag_commit, scan_for_secrets, stage_tag_archive, stage_workspace
from engine.agents.deploy_agent import DeployAgent
from engine.agents.deploy_git import GitPublisher
from engine.agents.deploy_github import HttpxGitHubClient
from engine.agents.deploy_models import DeployRequest
from engine.agents.deploy_runner import SubprocessRunner
from engine.agents.deploy_support import (
    DeploymentHistory, DisabledTarget, FileCheckpointVerifier, HttpxHealthChecker, prepare_publish_files,
)


_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        for option in ("--repo-name", "--git-tag", "--run-id", "--stage-id", "--checkpoint-id"):
            if option in message:
                raise ValueError(f"invalid {option[2:]}")
        raise ValueError("invalid arguments")


def _say(message: str) -> None:
    print(redact(message))


def _valid_name(value: str, label: str) -> None:
    if not _NAME.fullmatch(value) or ".." in value or value.endswith("."):
        raise ValueError(f"invalid {label}")


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(description="Preview or push an approved AgentForge checkpoint")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--stage-id", required=True)
    parser.add_argument("--checkpoint-id", required=True)
    parser.add_argument("--git-tag", required=True)
    parser.add_argument("--repo-name", required=True)
    parser.add_argument("--visibility", choices=("private", "public"), default="private")
    parser.add_argument("--include-ci", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--allow-working-tree", action="store_true")
    return parser.parse_args(argv)


def _dry_run(workspace: Path, request: DeployRequest, include_ci: bool) -> int:
    # Stage inside a disposable copy, never under the source workspace.
    with tempfile.TemporaryDirectory(prefix="agentforge-deploy-preview-") as temporary:
        copy = Path(temporary) / "workspace"
        runner = SubprocessRunner()
        probe = runner.run(["git", "rev-parse", "--show-toplevel"], workdir=workspace, timeout=15)
        if probe.returncode == 0:
            source_commit = resolve_tag_commit(workspace, request.git_tag or "", runner)
            if source_commit is None:
                _say("approved tag not found in the workspace repository")
                return 1
            staged, excluded, archive_notes = stage_tag_archive(
                workspace, source_commit, runner,
                staging_dir=copy / ".agentforge" / "deploy" / "staging",
            )
            preview_mode = "tag"
        else:
            def ignore(directory: str, names: list[str]) -> list[str]:
                parent = Path(directory).relative_to(workspace)
                return [name for name in names if _excluded(parent / name)]

            excluded = sorted(
                path.relative_to(workspace).as_posix()
                for path in workspace.rglob("*")
                if path.is_file() and _excluded(path.relative_to(workspace))
            )
            shutil.copytree(workspace, copy, ignore=ignore, symlinks=True)
            staged = stage_workspace(copy)
            archive_notes = []
            preview_mode = "working_tree"
        prepare_publish_files(staged, request, include_ci=include_ci)
        report = scan_for_secrets(staged)

        _say("Dry run: no network call or Git push.")
        _say("Checkpoint approval is not required for a dry run.")
        if preview_mode == "working_tree":
            _say("DRY RUN USES WORKING TREE; a real push requires the approved tag")
        else:
            _say(f"Dry run uses approved tag commit {source_commit}.")
        for note in archive_notes:
            _say(note)
        if report.blocks:
            _say("PUSH WOULD BE BLOCKED. Nothing would be pushed.")
            _say("Blocks (file name only):")
            for block in report.blocks:
                _say(f"  {block.file or '[staged files]'}")
        else:
            _say("Files that would be pushed:")
            for name in sorted(report.files_scanned):
                _say(f"  {name}")
        _say("Excluded files:")
        for name in excluded:
            _say(f"  {name}")
        _say("Warnings (file and line only):")
        for warning in report.warnings:
            _say(f"  {warning.file}:{warning.line}")
        if not report.blocks:
            _say("Blocks (file name only):")
        return 1 if report.blocks else 0


def main(argv: list[str] | None = None) -> int:
    """Run a read-only preview by default; require token, consent and approval to push."""
    try:
        args = _arguments(argv)
        for label, value in (
            ("run-id", args.run_id), ("stage-id", args.stage_id),
            ("checkpoint-id", args.checkpoint_id), ("git-tag", args.git_tag),
            ("repo-name", args.repo_name),
        ):
            _valid_name(value, label)
        workspace = args.workspace.resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError("workspace must be a directory")
        request = DeployRequest(
            run_id=args.run_id, stage_id=args.stage_id, checkpoint_id=args.checkpoint_id,
            git_tag=args.git_tag, repo_name=args.repo_name, visibility=args.visibility,
        )
        if not args.push:
            return _dry_run(workspace, request, args.include_ci)
        if not os.environ.get("GITHUB_TOKEN"):
            _say("GITHUB_TOKEN is not set; push cancelled.")
            return 1
        if args.allow_working_tree:
            _say("WARNING: UNVERIFIED WORKING TREE PUSH; content may differ from the approved tag.")
        if not args.yes:
            _say('Type "yes" to push this approved checkpoint:')
            if input().strip() != "yes":
                _say("Push cancelled.")
                return 1
        verifier = FileCheckpointVerifier(workspace)
        if not verifier.is_approved(request.run_id, request.checkpoint_id or "", request.git_tag or ""):
            _say("No approved checkpoint file matched the request; push cancelled.")
            return 1
        agent = DeployAgent(
            github=HttpxGitHubClient(), publisher=GitPublisher(SubprocessRunner()),
            health=HttpxHealthChecker(), target=DisabledTarget(),
            history=DeploymentHistory(workspace / ".agentforge" / "deploy" / "deployments.json"),
            verifier=verifier, include_ci=args.include_ci,
            content_mode="working_tree" if args.allow_working_tree else "tag",
        )
        result = agent.run(request, workspace)
        _say(f"Deploy result: {result.status}")
        for note in result.notes:
            _say(f"  {note}")
        return 0 if result.status == "done" else 1
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception as exc:
        _say(f"Deploy command failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
