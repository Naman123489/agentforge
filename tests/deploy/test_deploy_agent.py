"""Offline Phase 1 tests for contract models and safe staging."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from pydantic import ValidationError

from engine.agents.deploy import redact, sanitize_repo_name, scan_for_secrets, stage_workspace
from engine.agents.deploy_models import CheckResult, DeploymentStatus, DeployRequest, DeployResult


def test_contract_shaped_models() -> None:
    request = DeployRequest(
        run_id="run_1", stage_id="deploy", checkpoint_id="cp_1", git_tag="cp-1", repo_name="Example"
    )
    assert request.visibility == "private"
    assert request.health_check_path == "/health"
    result = DeployResult(stage_id="deploy", status="done", checks=[CheckResult(name="scan", passed=True)])
    for field in (
        "stage_id", "status", "files_changed", "checks", "models_used", "tokens", "cost_usd", "iterations", "notes"
    ):
        assert field in result.model_dump()
    assert result.models_used == [] and result.tokens == 0 and result.cost_usd == 0.0
    assert DeploymentStatus(id="d1", status="healthy", target="test").created_at.tzinfo is not None
    with pytest.raises(ValidationError):
        DeployRequest(run_id="r", stage_id="s", repo_name="x", visibility="team")


def test_redact_never_exposes_environment_token(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "token-with-unique-secret-123"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    output = redact(f"raw {token}; header {basic}; ghp_ABC123; NEBIUS_API_KEY=abc123")
    assert token not in output
    assert basic not in output
    assert "ghp_ABC123" not in output
    assert "abc123" not in output


def test_redact_base64_git_header_token(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "unique-environment-token"
    monkeypatch.setenv("GITHUB_TOKEN", token)
    encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    assert encoded not in redact(f"git config value: {encoded}")


@pytest.mark.parametrize("scheme", ["basic", "Bearer"])
def test_redact_authorization_header_value(scheme: str) -> None:
    output = redact(f"before\nAUTHORIZATION: {scheme} arbitrary-secret-value\nafter")
    assert output == f"before\nAUTHORIZATION: {scheme} [REDACTED]\nafter"


@pytest.mark.parametrize("token", ["ghp_" + "a" * 20, "github_pat_" + "b_" * 10, "nvapi-" + "c-" * 10])
def test_redact_token_patterns_without_environment(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert redact(f"prefix {token} suffix") == "prefix [REDACTED] suffix"


def test_redact_exception_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    error = RuntimeError("request failed: ghp_" + "x" * 20)
    assert redact(error) == "request failed: [REDACTED]"


def test_redact_nested_dicts_lists_and_multiline_text() -> None:
    nested = {"events": ["first line\nAuthorization: Bearer hidden-value\nlast line", {"error": "nvapi-" + "z" * 20}]}
    assert redact(nested) == {
        "events": ["first line\nAuthorization: Bearer [REDACTED]\nlast line", {"error": "[REDACTED]"}]
    }


def test_redact_leaves_normal_text_untouched() -> None:
    normal = "Build passed.\nRepository: example-project\nStatus: done"
    assert redact(normal) == normal


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("My Cool Repo!", "my-cool-repo"),
        ("  API__v2  ", "api-v2"),
        ("A" * 100, "a" * 80),
        ("Répô!!!", "r-p"),
    ],
)
def test_sanitize_repo_name(raw: str, expected: str) -> None:
    assert sanitize_repo_name(raw) == expected


def test_sanitize_repo_name_rejects_empty() -> None:
    with pytest.raises(ValueError):
        sanitize_repo_name("!!!")


def test_stage_excludes_private_and_generated_files(tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "app.py").write_text("ok")
    (tmp_path / ".env").write_text("GITHUB_TOKEN=secret")
    (tmp_path / ".env.local").write_text("secret")
    (tmp_path / "cache.db").write_text("x")
    (tmp_path / "data.sqlite").write_text("x")
    for dirname in ("node_modules", "__pycache__", ".venv", ".agentforge", "dist", ".pytest_cache"):
        directory = tmp_path / dirname
        directory.mkdir()
        (directory / "excluded.txt").write_text("x")
    staged = stage_workspace(tmp_path)
    assert staged == tmp_path / ".agentforge" / "deploy" / "staging"
    assert [p.relative_to(staged).as_posix() for p in staged.rglob("*") if p.is_file()] == ["backend/app.py"]
    assert (tmp_path / "backend" / "app.py").read_text() == "ok"


@pytest.mark.parametrize("bad", ["../outside", "backend/../other", "/absolute", "C:\\absolute"])
def test_stage_rejects_unsafe_paths(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError, match="relative"):
        stage_workspace(tmp_path, [bad])


def test_stage_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_text("content")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this system")
    with pytest.raises(ValueError, match="symlink"):
        stage_workspace(tmp_path)


@pytest.mark.parametrize(
    "contents",
    [b"nvapi-abcdef", b"ghp_abcdef", b"github_pat_abcdef", b"BEGIN PRIVATE KEY", b"NEBIUS_API_KEY=abc"],
)
def test_scan_flags_secret_content(tmp_path: Path, contents: bytes) -> None:
    (tmp_path / "source.txt").write_bytes(contents)
    assert scan_for_secrets(tmp_path) == ["source.txt"]


def test_scan_flags_env_name_and_cross_chunk_secret(tmp_path: Path) -> None:
    (tmp_path / ".env.production").write_text("empty")
    (tmp_path / "large.txt").write_bytes(b"x" * 65532 + b"github_pat_abc")
    assert scan_for_secrets(tmp_path) == [".env.production", "large.txt"]


def test_secret_in_staged_copy_is_reported_without_content(tmp_path: Path) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "settings.py").write_text("key = 'nvapi-secretvalue'")
    staged = stage_workspace(tmp_path, ["backend"])
    assert scan_for_secrets(staged) == ["backend/settings.py"]
    assert "secretvalue" not in str(scan_for_secrets(staged))
