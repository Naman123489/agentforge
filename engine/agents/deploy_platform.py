"""AgentForge side of the Deploy agent: capability, checkpoint verification against the run's own checkpoint service,
agent construction, and the platform DeploymentStatus.

Nothing here duplicates the orchestrator, the checkpoint service or the event system: the verifier reads the run's
checkpoints (created by server/pipeline.py via the Reviewer's annotated `stage-{id}-green` tags), events go through
the run's emit(), and cancellation is the run's cancel flag.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .deploy import redact, sanitize_repo_name
from .deploy_agent import DeployAgent
from .deploy_git import GitPublisher
from .deploy_github import HttpxGitHubClient
from .deploy_models import DeployResult
from .deploy_runner import SubprocessRunner
from .deploy_support import DeploymentHistory, DisabledTarget, HttpxHealthChecker, NebiusServerlessTarget

# Platform DeploymentStatus.status (contract): queued -> publishing -> pushed (GitHub only, nothing live) or
# deploying -> healthy | failed | rolled_back.
TARGETS = ("github", "nebius_serverless", "configured_target")


def capability(env: dict[str, str] | None = None) -> dict[str, Any]:
    """What this server can do. Deployment is OFF unless the operator sets AF_DEPLOY_ENABLED=1 AND a server-side
    GITHUB_TOKEN exists; a token that merely happens to be in the environment is not enough."""
    env = dict(os.environ) if env is None else env
    enabled = env.get("AF_DEPLOY_ENABLED", "").strip() == "1"
    token = bool(env.get("GITHUB_TOKEN"))
    missing = [m for m, ok in (("AF_DEPLOY_ENABLED=1 is not set on the server", enabled),
                               ("GITHUB_TOKEN is not set on the server", token)) if not ok]
    return {"github_ready": not missing, "missing": missing, "targets": {
        "github": not missing,
        "nebius_serverless": False,
        "configured_target": False,
    }, "nebius_missing": NebiusServerlessTarget().missing_requirements(env)}


class RunCheckpointVerifier:
    """Approves only the run's FINAL checkpoint, created after the Reviewer approved the reviewer stage, whose tag is
    an existing annotated `stage-{stage_id}-green` tag. approved_commit() returns the commit recorded when the
    checkpoint was made, so DeployAgent refuses a tag that was moved since."""

    def __init__(self, rec: Any) -> None:
        self.rec = rec

    def _checkpoint(self, run_id: str, checkpoint_id: str) -> dict | None:
        if run_id != self.rec.id or not self.rec.checkpoints:
            return None
        final = self.rec.checkpoints[-1]
        return final if final.get("id") == checkpoint_id else None

    def why_not(self, run_id: str, checkpoint_id: str | None, git_tag: str | None) -> str | None:
        cp = self._checkpoint(run_id, checkpoint_id or "")
        if cp is None:
            return "not the run's final checkpoint"
        if cp.get("git_tag") != git_tag:
            return "git tag does not match the checkpoint"
        stage = next((s for s in self.rec.stages if s["id"] == cp.get("stage_id")), None)
        if not stage or stage.get("agent") != "reviewer":
            return "the final checkpoint is not the Reviewer's"
        if stage.get("status") != "passed" or (stage.get("review") or {}).get("verdict") != "approve":
            return "the Reviewer did not approve the final stage"
        if not re.fullmatch(rf"stage-{re.escape(stage['id'])}-green(-\d+)?", git_tag or ""):
            return "tag is not the Reviewer's stage-{id}-green tag"
        r = subprocess.run(["git", "cat-file", "-t", f"refs/tags/{git_tag}"], cwd=self.rec.workspace,
                           capture_output=True, text=True, timeout=30)
        if r.returncode or r.stdout.strip() != "tag":
            return "tag is not an existing annotated tag"
        if not cp.get("commit"):
            return "checkpoint has no recorded commit"
        return None

    def is_approved(self, run_id: str, checkpoint_id: str, git_tag: str) -> bool:
        try:
            return self.why_not(run_id, checkpoint_id, git_tag) is None
        except (OSError, subprocess.SubprocessError, KeyError, TypeError):
            return False

    def approved_commit(self, run_id: str, checkpoint_id: str) -> str | None:
        cp = self._checkpoint(run_id, checkpoint_id)
        return cp.get("commit") if cp else None


def repo_name_for(rec: Any, requested: str | None = None) -> str:
    """A repo per run unless the caller names one: two runs never push unrelated histories into one repo."""
    if requested:
        return sanitize_repo_name(requested)
    base = sanitize_repo_name(rec.project_name or "agentforge-app")[:60]
    return sanitize_repo_name(f"{base}-{rec.id.removeprefix('run_')}")


def build_agent(rec: Any, target_name: str, on_event: Callable[[dict], None], should_stop: Callable[[], bool],
                github=None, publisher=None, health=None, target=None) -> DeployAgent:
    if target is None:
        target = NebiusServerlessTarget() if target_name == "nebius_serverless" else DisabledTarget()
    return DeployAgent(
        github=github or HttpxGitHubClient(), publisher=publisher or GitPublisher(SubprocessRunner()),
        health=health or HttpxHealthChecker(), target=target,
        history=DeploymentHistory(rec.workspace / ".agentforge" / "deploy" / "deployments.json"),
        verifier=RunCheckpointVerifier(rec), on_event=on_event, should_stop=should_stop, content_mode="tag")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_status(rec: Any, target: str, checkpoint: dict, status: str = "queued") -> dict:
    return {"id": f"dep_{rec.id}_{len(rec.deployments)}", "status": status, "target": target, "url": None,
            "health_check_passed": None, "error": None, "created_at": now_iso(), "checkpoint_id": checkpoint["id"],
            "git_tag": checkpoint["git_tag"], "repo_url": None, "commit_sha": None, "source_commit": None,
            "notes": [], "checks": []}


def apply_result(dep: dict, result: DeployResult) -> dict:
    """Fold the agent's result into the platform status. A push without a live deployment is `pushed`, never
    `healthy`."""
    dep.update(repo_url=result.repo_url, commit_sha=result.commit_sha, source_commit=result.source_commit,
               notes=list(result.notes), checks=[c.model_dump(mode="json") for c in result.checks])
    if result.deployment is not None:
        d = result.deployment
        dep.update(status=d.status if d.status in ("healthy", "failed", "rolled_back") else "failed", url=d.url,
                   health_check_passed=d.health_check_passed, error=d.error)
    elif result.status == "done":
        dep.update(status="pushed", health_check_passed=None, url=None)
    else:
        dep.update(status="failed", error=("stopped: " if result.status == "stopped" else "") +
                   (result.notes[-1] if result.notes else "deployment failed"))
    return redact(dep)
