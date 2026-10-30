"""Checkpoint-gated publishing and optional deployment orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import stat
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

from .deploy import (
    redact,
    resolve_tag_commit,
    safe_deploy_dir,
    sanitize_repo_name,
    scan_for_secrets,
    stage_tag_archive,
    stage_workspace,
)
from .deploy_git import GitPublishError, GitPublisher
from .deploy_github import GitHubClient, GitHubError, NotConfigured
from .deploy_models import CheckResult, DeployRequest, DeployResult, DeploymentStatus
from .deploy_runner import SubprocessRunner
from .deploy_support import (
    CheckpointVerifier,
    DeployTarget,
    DeploymentHistory,
    HealthChecker,
    HealthResult,
    prepare_publish_files,
)


class _Stopped(Exception):
    pass


# One deployment per workspace at a time (hardening attack 5): an in-process lock for threads of this server and a
# lock file for other processes (the CLI). A lock file older than STALE_LOCK_S whose process is gone is recovered.
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
STALE_LOCK_S = 3600.0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, AttributeError):
        return False


class _WorkspaceLock:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.path = safe_deploy_dir(self.root, self.root / ".agentforge" / "deploy") / "deploy.lock"
        with _LOCKS_GUARD:
            self.thread_lock = _LOCKS.setdefault(str(self.root).lower(), threading.Lock())
        self.held = False

    def acquire(self) -> bool:
        if not self.thread_lock.acquire(blocking=False):
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            for _ in range(2):
                try:
                    fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    if not self._stale():
                        break
                    self.path.unlink(missing_ok=True)
                    continue
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump({"pid": os.getpid(), "created": time.time()}, handle)
                self.held = True
                return True
        except OSError:
            pass
        self.thread_lock.release()
        return False

    def _stale(self) -> bool:
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
            return not _pid_alive(int(info.get("pid", 0))) or time.time() - float(info.get("created", 0)) > STALE_LOCK_S
        except (OSError, ValueError, TypeError):
            return True

    def release(self) -> None:
        if self.held:
            self.path.unlink(missing_ok=True)
            self.held = False
            self.thread_lock.release()


class _DeadlineExceeded(Exception):
    pass


def to_deployment_status(result: DeployResult) -> dict[str, Any] | None:
    """Return the contract's deployment fields, or None if no deployment occurred.

    Only runs that reached the deploy step can produce a status dict. Push-only
    success and failures before deployment have no deployment health status.
    """
    deployment = result.deployment
    if deployment is None:
        return None
    created_at = deployment.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    payload = deployment.model_dump(mode="json")
    payload["created_at"] = created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return redact(payload)


class DeployAgent:
    def __init__(
        self,
        github: GitHubClient,
        publisher: GitPublisher,
        health: HealthChecker,
        target: DeployTarget,
        history: DeploymentHistory,
        verifier: CheckpointVerifier | None,
        on_event: Callable[[dict], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        clock: Callable[[], float] | None = None,
        include_ci: bool = False,
        max_seconds: float = 300.0,
        content_mode: Literal["tag", "working_tree"] = "tag",
    ) -> None:
        if content_mode not in {"tag", "working_tree"}:
            raise ValueError("content_mode must be 'tag' or 'working_tree'")
        self.github = github
        self.publisher = publisher
        self.health = health
        self.target = target
        self.history = history
        self.verifier = verifier
        self.on_event = on_event
        self.should_stop = should_stop or (lambda: False)
        self.clock = clock or time.monotonic
        self.include_ci = include_ci
        self.max_seconds = max_seconds
        self.content_mode = content_mode
        self._async_cancel_state = threading.local()

    async def run_async(self, request: DeployRequest, workspace_root: Path) -> DeployResult:
        """Run off the event loop and honor cancellation at the next step boundary.

        A step already in progress finishes; it is not interrupted mid-step.
        """
        cancelled = threading.Event()

        def worker() -> DeployResult:
            self._async_cancel_state.event = cancelled
            try:
                return self.run(request, workspace_root)
            finally:
                del self._async_cancel_state.event

        try:
            return await asyncio.to_thread(worker)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        except Exception as exc:
            return DeployResult(
                stage_id=redact(str(getattr(request, "stage_id", "deploy"))),
                status="failed",
                iterations=1,
                notes=[f"unexpected deployment error: {redact(str(exc))}"],
            )

    def run(self, request: DeployRequest, workspace_root: Path) -> DeployResult:
        """Publish only a verified checkpoint; return a typed failure for every error. One run per workspace."""
        try:
            lock = _WorkspaceLock(Path(workspace_root))
        except (OSError, ValueError) as exc:
            return DeployResult(stage_id=redact(str(getattr(request, "stage_id", "deploy"))), status="failed", iterations=1,
                                checks=[CheckResult(name="deploy_lock", passed=False)],
                                notes=[f"deploy directory is unsafe: {redact(str(exc))}"])
        if not lock.acquire():
            return DeployResult(stage_id=redact(str(getattr(request, "stage_id", "deploy"))), status="failed", iterations=1,
                                checks=[CheckResult(name="deploy_lock", passed=False)],
                                notes=["another deployment is in progress for this workspace"])
        try:
            return self._run_locked(request, workspace_root)
        finally:
            lock.release()

    def _run_locked(self, request: DeployRequest, workspace_root: Path) -> DeployResult:
        stage_id = redact(str(getattr(request, "stage_id", "deploy")))
        result = DeployResult(stage_id=stage_id, status="failed", iterations=1)
        started = 0.0

        def emit(kind: str, data: dict) -> None:
            if self.on_event is None:
                return
            event = redact(
                {
                    "id": f"evt_{uuid.uuid4().hex}",
                    "run_id": str(getattr(request, "run_id", "")),
                    "stage_id": stage_id,
                    "type": kind,
                    "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "data": data,
                }
            )
            try:
                self.on_event(event)
            except Exception:
                note = "event callback failed"
                if note not in result.notes:
                    result.notes.append(note)

        def guard(step: str) -> None:
            cancelled = getattr(self._async_cancel_state, "event", None)
            if (cancelled is not None and cancelled.is_set()) or self.should_stop():
                raise _Stopped(f"deployment stopped before {step}")
            if self.clock() - started >= self.max_seconds:
                raise _DeadlineExceeded(f"deployment deadline exceeded before {step}")

        def check(name: str, passed: bool, log: str = "") -> None:
            result.checks.append(CheckResult(name=name, passed=passed, log=redact(log)))

        def progress(step: str) -> None:
            emit("stage_progress", {"step": step})

        def finish() -> DeployResult:
            passed = sum(item.passed for item in result.checks)
            failed = len(result.checks) - passed
            emit(
                "stage_finished",
                {
                    "status": "passed" if result.status == "done" else result.status,
                    "checks_passed": passed,
                    "checks_failed": failed,
                },
            )
            return DeployResult.model_validate(redact(result.model_dump(mode="json")))

        emit("stage_started", {"agent": "deploy"})
        try:
            started = self.clock()
            guard("checkpoint validation")
            if (
                not isinstance(request, DeployRequest)
                or not request.run_id.strip()
                or not request.stage_id.strip()
                or not request.checkpoint_id
                or not request.git_tag
                or not request.repo_name.strip()
                or ".." in request.git_tag
                or request.git_tag.startswith(("/", "-"))
                or self.verifier is None
            ):
                check("checkpoint_approved", False)
                result.notes.append("no approved checkpoint: missing or invalid request fields or verifier")
                return finish()
            if any(redact(value) != value for value in (request.run_id, request.stage_id, request.checkpoint_id, request.git_tag, request.repo_name)):
                check("checkpoint_approved", False)
                result.notes.append("no approved checkpoint: request fields contain credentials")
                return finish()
            if not self.verifier.is_approved(request.run_id, request.checkpoint_id, request.git_tag):
                check("checkpoint_approved", False)
                result.notes.append("no approved checkpoint")
                return finish()
            check("checkpoint_approved", True)
            progress("checkpoint_verified")

            guard("staging")
            root = Path(workspace_root)
            source_commit: str | None = None
            if self.content_mode == "tag":
                source_runner = getattr(self.publisher, "runner", None) or SubprocessRunner()
                source_commit = resolve_tag_commit(root, request.git_tag, source_runner)
                if source_commit is None:
                    check("content_from_approved_tag", False)
                    result.notes.append("approved tag not found in the workspace repository")
                    return finish()
                checkpoint_file = root / ".agentforge" / "checkpoints" / f"{request.checkpoint_id}.json"
                if checkpoint_file.is_symlink():
                    check("content_from_approved_tag", False)
                    result.notes.append("approved checkpoint commit could not be verified")
                    return finish()
                if checkpoint_file.is_file():
                    checkpoint = json.loads(checkpoint_file.read_text(encoding="utf-8"))
                    if "commit" in checkpoint and checkpoint["commit"] != source_commit:
                        check("content_from_approved_tag", False)
                        result.notes.append("approved checkpoint commit does not match the tag commit")
                        return finish()
                # A checkpoint service that records the approved commit (AgentForge's) makes the match mandatory: the
                # tag must still point where it pointed when the Reviewer approved.
                approved_commit = getattr(self.verifier, "approved_commit", None)
                if approved_commit is not None:
                    expected = approved_commit(request.run_id, request.checkpoint_id)
                    if not expected or str(expected).lower() != source_commit:
                        check("content_from_approved_tag", False)
                        result.notes.append("approved checkpoint commit does not match the tag commit")
                        return finish()
                result.source_commit = source_commit
                check("content_from_approved_tag", True)
                progress("approved_tag_verified")
            else:
                check("content_from_approved_tag", False)
                result.notes.append("UNVERIFIED: published working tree, not the approved tag")
            safe_deploy_dir(root, root / ".agentforge" / "deploy")
            old_git = root / ".agentforge" / "deploy" / "staging" / ".git"
            if old_git.is_dir() and not old_git.is_symlink():
                def make_writable(function, path, error) -> None:
                    os.chmod(path, stat.S_IWRITE)
                    function(path)

                shutil.rmtree(old_git, onexc=make_writable)
            if self.content_mode == "tag":
                staged, _, archive_notes = stage_tag_archive(root, source_commit, source_runner)
                result.notes.extend(redact(archive_notes))
            else:
                staged = stage_workspace(root)
            progress("staged")

            guard("publish file preparation")
            added = prepare_publish_files(staged, request, include_ci=self.include_ci)
            result.files_changed = redact(added)

            guard("secret scan")
            scan = scan_for_secrets(staged)
            for warning in scan.warnings:
                result.notes.append(redact(f"warning: {warning.file}:{warning.line}"))
            for note in scan.notes:
                result.notes.append(redact(note))
            if scan.blocks:
                check("secret_scan", False)
                result.notes.append("secret scan blocked files: " + ", ".join(redact(list(scan))))
                progress("secret_scan")
                return finish()
            check("secret_scan", True)
            progress("secret_scan")

            guard("repository lookup")
            repo_name = sanitize_repo_name(request.repo_name)
            repo = self.github.get_repo(repo_name)
            if repo is None:
                repo = self.github.create_repo(
                    repo_name,
                    private=request.visibility != "public",
                    description=redact(f"AgentForge run {request.run_id} checkpoint {request.checkpoint_id}"),
                )
            result.repo_url = redact(repo.html_url)
            check("repo_ready", True)
            progress("repo_ready")

            guard("git push")
            cache_key = hashlib.sha256(
                json.dumps([request.run_id, request.checkpoint_id, request.git_tag, repo_name]).encode("utf-8")
            ).hexdigest()
            cache = safe_deploy_dir(root, root / ".agentforge" / "deploy" / "git-cache") / cache_key
            if cache.is_dir() and not cache.is_symlink():
                shutil.copytree(cache, staged / ".git")
            published = self.publisher.publish(
                staged,
                repo,
                "main",
                request.git_tag,
                (
                    f"AgentForge: {request.stage_id} approved ({request.checkpoint_id}), source {source_commit}"
                    if source_commit else f"AgentForge: {request.stage_id} approved ({request.checkpoint_id})"
                ),
            )
            result.commit_sha = redact(published.commit_sha)
            result.git_tag = redact(published.tag)
            result.files_pushed = redact(published.files_pushed)
            check("git_push", True)
            progress("pushed")
            if not cache.exists():
                cache.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(staged / ".git", cache)

            guard("deployment configuration")
            if not self.target.is_configured():
                result.notes.append("push-only: deployment not configured")
                result.status = "done"
                return finish()

            previous_same = self.history.for_checkpoint(request.run_id, request.checkpoint_id)
            if previous_same is not None and previous_same.status == "healthy":
                result.deployment = previous_same
                result.notes.append("healthy deployment already exists for this checkpoint; redeploy skipped")
                check("deploy", True)
                check("health_check", True)
                result.status = "done"
                return finish()

            guard("deployment")
            progress("deploying")
            try:
                deployment = self.target.deploy(staged, request, repo)
            except NotConfigured:
                result.notes.append("push-only: deployment not configured")
                result.status = "done"
                return finish()
            result.deployment = deployment
            emit("deployment_status", {"status": deployment.status, "url": deployment.url, "error": deployment.error})
            check("deploy", deployment.status != "failed", deployment.error or "")

            parsed_url = urlsplit(deployment.url or "")
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                deployment.status = "failed"
                deployment.health_check_passed = False
                deployment.error = "invalid deployment URL: must use http or https"
                check("health_check", False, deployment.error)
                self.history.add(request.run_id, request.checkpoint_id, deployment)
                emit("deployment_status", {"status": deployment.status, "url": deployment.url, "error": deployment.error})
                result.notes.append(deployment.error)
                return finish()

            guard("health check")
            health = self.health.check(deployment.url or "", request.health_check_path)
            check("health_check", health.passed, health.error or "")
            progress("health_check")
            if health.passed and deployment.status != "failed":
                deployment.status = "healthy"
                deployment.health_check_passed = True
                self.history.add(request.run_id, request.checkpoint_id, deployment)
                emit("deployment_status", {"status": deployment.status, "url": deployment.url, "error": deployment.error})
                result.status = "done"
                return finish()

            deployment.status = "failed"
            deployment.health_check_passed = False
            deployment.error = redact(health.error or deployment.error or "health check failed")
            self.history.add(request.run_id, request.checkpoint_id, deployment)
            emit("deployment_status", {"status": deployment.status, "url": deployment.url, "error": deployment.error})
            result.notes.append("deployment health check failed")
            if not request.rollback_on_failure:
                result.notes.append("rollback disabled")
                return finish()
            guard("rollback selection")
            previous = self.history.latest_healthy(request.run_id, exclude_id=deployment.id)
            if previous is None:
                result.notes.append("no previous deployment to roll back to")
                return finish()
            rollback = self.target.rollback(previous)
            guard("rollback health check")
            rollback_health: HealthResult = self.health.check(rollback.url or "", request.health_check_path)
            rollback.health_check_passed = rollback_health.passed
            rollback.status = "rolled_back" if rollback_health.passed else "failed"
            self.history.add(request.run_id, request.checkpoint_id, rollback)
            if rollback_health.passed:
                deployment.status = "rolled_back"
                self.history.add(request.run_id, request.checkpoint_id, deployment)
                result.notes.append("rolled back to the previous healthy deployment")
            else:
                result.notes.append("rollback attempted but its health check failed")
            emit("deployment_status", {"status": rollback.status, "url": rollback.url, "error": rollback.error})
            return finish()
        except _Stopped as exc:
            result.status = "stopped"
            result.notes.append(redact(str(exc)))
        except _DeadlineExceeded as exc:
            result.notes.append(redact(str(exc)))
        except (GitHubError, GitPublishError, NotConfigured) as exc:
            result.notes.append(redact(str(exc)))
            if result.repo_url and not any(item.name == "git_push" for item in result.checks):
                check("git_push", False, str(exc))
        except Exception as exc:
            message = redact(str(exc))
            result.notes.append(f"unexpected deployment error: {message}")
            emit("error", {"message": message})
        return finish()
