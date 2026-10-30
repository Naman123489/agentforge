"""Publish a staged copy to Git without rewriting history."""

from __future__ import annotations

import base64
import os
import re
import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

from .deploy import redact
from .deploy_models import RepoInfo
from .deploy_runner import RunResult, Runner


class GitPublishError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(redact(message))


@dataclass(frozen=True)
class PublishResult:
    commit_sha: str
    tag: str
    pushed: bool
    files_pushed: list[str]
    notes: list[str] = field(default_factory=list)


class GitPublisher:
    def __init__(self, runner: Runner, timeout: float = 60.0) -> None:
        self.runner = runner
        self.timeout = timeout

    # Hooks in a reused publish repo (.agentforge/deploy/git-cache) must never run; Windows needs long paths for
    # .agentforge/deploy/staging/.git/objects/... (hardening attack 6).
    _NO_HOOKS = tempfile.mkdtemp(prefix="agentforge-nohooks-")
    _SAFE = ["-c", f"core.hooksPath={_NO_HOOKS}", "-c", "core.longpaths=true"]

    def _run(self, staged: Path, args: list[str], env: dict[str, str] | None = None) -> RunResult:
        result = self.runner.run(["git", *self._SAFE, *args], workdir=staged, timeout=self.timeout, env=env)
        return RunResult(result.returncode, redact(result.stdout), redact(result.stderr), result.timed_out)

    def _checked(self, staged: Path, args: list[str], env: dict[str, str] | None = None) -> RunResult:
        result = self._run(staged, args, env)
        if result.returncode:
            raise GitPublishError(f"git {args[0]} failed: {result.stderr or result.stdout or 'unknown error'}")
        return result

    @staticmethod
    def _plain_remote(url: str) -> str:
        parsed = urlsplit(url)
        if parsed.username or parsed.password:
            raise GitPublishError("repository URL must not contain credentials")
        if "\n" in url or "\r" in url or not url:
            raise GitPublishError("invalid repository URL")
        return url

    def _git_version(self, staged: Path) -> tuple[int, int]:
        output = self._checked(staged, ["--version"]).stdout
        match = re.search(r"git version (\d+)\.(\d+)", output)
        if not match:
            raise GitPublishError("could not determine git version")
        return int(match.group(1)), int(match.group(2))

    @staticmethod
    def _askpass(staged: Path, token: str) -> Path:
        suffix = ".cmd" if os.name == "nt" else ".sh"
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=suffix, prefix="deploy-askpass-", dir=staged.parent, delete=False
        )
        path = Path(handle.name)
        try:
            if os.name == "nt":
                handle.write(f'@echo off\nset "prompt=%~1"\nif /I "%prompt:~0,8%"=="Username" (echo x-access-token) else (echo {token})\n')
            else:
                handle.write(f"#!/bin/sh\ncase \"$1\" in *Username*) echo x-access-token;; *) echo {shlex.quote(token)};; esac\n")
        finally:
            handle.close()
        path.chmod(0o700)
        return path

    def _push_env(self, staged: Path, token_env: bool) -> tuple[dict[str, str], Path | None]:
        env = {"GIT_TERMINAL_PROMPT": "0"}
        if not token_env:
            return env, None
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return env, None
        if self._git_version(staged) >= (2, 31):
            header = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            env.update(
                {
                    "GIT_CONFIG_COUNT": "2",
                    "GIT_CONFIG_KEY_0": "http.extraheader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {header}",
                    "GIT_CONFIG_KEY_1": "credential.helper",
                    "GIT_CONFIG_VALUE_1": "",
                }
            )
            return env, None
        helper = self._askpass(staged, token)
        env.update({"GIT_ASKPASS": str(helper), "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": ""})
        return env, helper

    def publish(
        self,
        staged_dir: Path,
        repo: RepoInfo,
        branch: str,
        git_tag: str,
        commit_message: str,
        token_env: bool = True,
    ) -> PublishResult:
        staged = Path(staged_dir).resolve(strict=True)
        if not staged.is_dir():
            raise GitPublishError("staged_dir must be a directory")
        if branch != "main":
            raise GitPublishError("only main branch is supported")
        if not git_tag or ".." in git_tag or git_tag.startswith(("/", "-")):
            raise GitPublishError("invalid git tag")
        if redact(git_tag) != git_tag or redact(commit_message) != commit_message:
            raise GitPublishError("tag and commit message must not contain credentials")
        self._checked(staged, ["check-ref-format", f"refs/tags/{git_tag}"])
        remote = self._plain_remote(repo.clone_url)
        if redact(remote) != remote:
            raise GitPublishError("repository URL must not contain credentials")
        if not (staged / ".git").exists():
            initialized = self._run(staged, ["init", "-b", "main"])
            if initialized.returncode:
                if "unknown switch" not in initialized.stderr.lower() and "unknown option" not in initialized.stderr.lower():
                    raise GitPublishError(f"git init failed: {initialized.stderr}")
                self._checked(staged, ["init"])
                self._checked(staged, ["symbolic-ref", "HEAD", "refs/heads/main"])
        self._checked(staged, ["-c", "core.autocrlf=false", "add", "-A", "--"])
        changes = self._checked(staged, ["status", "--porcelain"]).stdout.strip()
        head = self._run(staged, ["rev-parse", "--verify", "HEAD"])
        if changes or head.returncode:
            self._checked(
                staged,
                [
                    "-c", "user.name=AgentForge",
                    "-c", "user.email=agentforge@users.noreply.github.com",
                    "-c", "core.autocrlf=false",
                    "commit", "-m", commit_message,
                ],
            )
        commit_sha = self._checked(staged, ["rev-parse", "HEAD"]).stdout.strip()
        existing_tag = self._run(staged, ["rev-parse", "-q", "--verify", f"refs/tags/{git_tag}^{{commit}}"])
        if existing_tag.returncode == 0:
            if existing_tag.stdout.strip() != commit_sha:
                raise GitPublishError("checkpoint tag already points to a different commit; refusing to move it")
        else:
            self._checked(
                staged,
                ["-c", "user.name=AgentForge", "-c", "user.email=agentforge@users.noreply.github.com", "tag", "-a", git_tag, "-m", f"AgentForge checkpoint {git_tag}"],
            )
        current_remote = self._run(staged, ["remote", "get-url", "origin"])
        if current_remote.returncode:
            self._checked(staged, ["remote", "add", "origin", remote])
        elif current_remote.stdout.strip() != remote:
            self._checked(staged, ["remote", "set-url", "origin", remote])
        files = self._checked(staged, ["ls-tree", "-r", "--name-only", "HEAD"]).stdout.splitlines()
        is_local = (urlsplit(remote).scheme == "" or bool(PureWindowsPath(remote).drive)) and not remote.startswith("git@")
        if not is_local and token_env and not os.environ.get("GITHUB_TOKEN"):
            raise GitPublishError("GitHub token is not configured")
        env, helper = self._push_env(staged, token_env)
        try:
            remote_tag = self._checked(staged, ["ls-remote", "--tags", "origin", f"refs/tags/{git_tag}^{{}}"], env).stdout.strip()
            if remote_tag and remote_tag.split()[0] != commit_sha:
                raise GitPublishError("remote checkpoint tag already points to a different commit; refusing to push")
            self._checked(staged, ["push", "--set-upstream", "origin", "main"], env)
            try:
                self._checked(staged, ["push", "origin", f"refs/tags/{git_tag}"], env)
            except GitPublishError as exc:
                raise GitPublishError(f"tag push failed; remote tag may point to a different commit: {exc}") from None
        finally:
            if helper is not None:
                helper.unlink(missing_ok=True)
        return PublishResult(commit_sha, redact(git_tag), True, sorted(redact(files)))
