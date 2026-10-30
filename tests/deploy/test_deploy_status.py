"""Contract conversion tests for deployment status output."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from engine.agents.deploy_agent import to_deployment_status
from engine.agents.deploy_models import DeployResult, DeploymentStatus


@pytest.mark.parametrize("state", ["healthy", "failed", "rolled_back"])
def test_to_deployment_status_matches_contract(state: str) -> None:
    created_at = datetime(2026, 10, 30, 12, 15, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    deployment = DeploymentStatus(
        id="deployment-1", status=state, target="fake", url="https://example.test/app",
        health_check_passed=state == "healthy", error="health check failed" if state == "failed" else None,
        created_at=created_at,
    )
    output = to_deployment_status(DeployResult(stage_id="deploy", status="done", deployment=deployment))
    assert output is not None
    assert set(output) == {"id", "status", "target", "url", "health_check_passed", "error", "created_at"}
    assert output["id"] == "deployment-1"
    assert output["status"] == state
    assert output["target"] == "fake"
    assert output["url"] == "https://example.test/app"
    assert output["health_check_passed"] is (state == "healthy")
    assert output["error"] == ("health check failed" if state == "failed" else None)
    assert output["created_at"] == "2026-10-30T06:45:00Z"


def test_to_deployment_status_push_only_returns_none() -> None:
    result = DeployResult(
        stage_id="deploy", status="done", repo_url="https://example.test/repo", commit_sha="a" * 40
    )
    assert to_deployment_status(result) is None


def test_to_deployment_status_redacts_token_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "fake-status-token-123456789"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    result = DeployResult(
        stage_id="deploy", status="failed",
        deployment=DeploymentStatus(
            id="deployment-2", status="failed", target="fake", error=f"request failed: {token}"
        ),
    )
    output = to_deployment_status(result)
    assert output is not None
    assert token not in json.dumps(output)
    assert output["error"] == "request failed: [REDACTED]"
    assert output["created_at"].endswith("Z")
