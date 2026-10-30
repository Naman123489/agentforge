"""Adversarial security checks for the standalone deploy agent."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from engine.agents.deploy import redact, scan_for_secrets
from engine.agents.deploy_agent import DeployAgent
from engine.agents.deploy_models import DeployRequest
from engine.agents.deploy_support import DeploymentHistory, DisabledTarget, FakeHealthChecker, FileCheckpointVerifier


@pytest.fixture(autouse=True)
def no_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)


def test_attack1_decodes_bare_base64_without_environment() -> None:
    encoded = base64.b64encode(b"x-access-token:ghp_" + b"a" * 36).decode()
    assert redact(encoded) == "[REDACTED]"


def test_attack1_decodes_base64_in_exception_and_nested_containers() -> None:
    encoded = base64.b64encode(b"Bearer hidden-credential-with-many-bytes").decode()
    error = RuntimeError(f"failed: {encoded}")
    assert encoded not in redact(error)
    assert encoded not in redact(str(error))
    assert encoded not in redact(repr(error))
    assert encoded not in repr(redact(error.args))
    nested = {encoded: [f"prefix {encoded} suffix", (encoded, {"key": encoded})]}
    assert encoded not in repr(redact(nested))


def test_attack1_bad_padding_and_harmless_base64() -> None:
    encoded = base64.b64encode(b"github_pat_" + b"b" * 32).decode().rstrip("=")
    harmless = base64.b64encode(b"a perfectly ordinary piece of text").decode()
    assert encoded not in redact(f"before {encoded} after")
    assert redact(harmless) == harmless
    assert redact("A" * 25 + "!")  # malformed input never raises


def test_attack1_split_urlencoded_key_and_bytes() -> None:
    split = "ghp_\n" + "c" * 36
    encoded_url = "ghp%5F" + "d" * 36
    token_key = "github_pat_" + "e" * 32
    assert "ghp_" not in redact(split)
    assert encoded_url not in redact(f"query={encoded_url}")
    assert token_key not in repr(redact({token_key: "value"}))
    assert b"nvapi-" not in redact(b"nvapi-" + b"f" * 32)


def _run_with_suspect_file(tmp_path: Path, name: str, contents: bytes):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file = workspace / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(contents)
    checkpoints = workspace / ".agentforge" / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / "cp_1.json").write_text(json.dumps({"id": "cp_1", "run_id": "run_1", "git_tag": "cp-1"}))
    calls = {"github": 0, "git": 0}

    class GuardedGitHub:
        def get_repo(self, repo_name):
            calls["github"] += 1
            raise AssertionError("repository lookup should be blocked")

    class GuardedPublisher:
        def publish(self, *args, **kwargs):
            calls["git"] += 1
            raise AssertionError("git push should be blocked")

    agent = DeployAgent(
        github=GuardedGitHub(),
        publisher=GuardedPublisher(),
        health=FakeHealthChecker(),
        target=DisabledTarget(),
        history=DeploymentHistory(workspace / ".agentforge" / "deploy" / "deployments.json"),
        verifier=FileCheckpointVerifier(workspace),
        content_mode="working_tree",
    )
    request = DeployRequest(run_id="run_1", stage_id="deploy", checkpoint_id="cp_1", git_tag="cp-1", repo_name="safe")
    return agent.run(request, workspace), calls


@pytest.mark.parametrize(
    "contents",
    [
        b"AKIA" + b"A" * 16,
        b"-----BEGIN RSA PRIVATE KEY-----",
        b"//registry.npmjs.org/:_authToken=" + b"a" * 24,
        b"xoxb-" + b"1" * 24,
        b"sk_live_" + b"2" * 24,
    ],
)
def test_attack2_secret_content_blocks_push(tmp_path: Path, contents: bytes) -> None:
    result, calls = _run_with_suspect_file(tmp_path, "backend/settings.txt", contents)
    assert result.status == "failed"
    assert any(check.name == "secret_scan" and not check.passed for check in result.checks)
    assert "backend/settings.txt" in " ".join(result.notes)
    assert calls == {"github": 0, "git": 0}


@pytest.mark.parametrize("name", ["id_rsa", "key.pem", "cert.p12", ".npmrc", ".pypirc", "credentials.json", "secrets.toml"])
def test_attack2_secret_filename_blocks_push(tmp_path: Path, name: str) -> None:
    result, calls = _run_with_suspect_file(tmp_path, name, b"harmless-looking file")
    assert result.status == "failed"
    assert any(check.name == "secret_scan" and not check.passed for check in result.checks)
    assert name in " ".join(result.notes)
    assert calls == {"github": 0, "git": 0}


def test_attack2_comment_with_word_secret_is_not_flagged(tmp_path: Path) -> None:
    (tmp_path / "safe.py").write_text("# This comment mentions a secret but contains no credential.\n")
    assert scan_for_secrets(tmp_path) == []


def test_attack3_unknown_binary_is_allowed_with_note(tmp_path: Path) -> None:
    (tmp_path / "image.bin").write_bytes(b"\x00\x01\xff" * 100)
    report = scan_for_secrets(tmp_path)
    assert report.blocks == []
    assert any("image.bin" in note and "unknown binary" in note for note in report.notes)


def test_attack3_file_size_limit_uses_small_fake_threshold(tmp_path: Path) -> None:
    (tmp_path / "oversize.txt").write_bytes(b"x" * 21)
    issues = scan_for_secrets(tmp_path, max_file_bytes=20, max_total_bytes=100)
    assert len(issues) == 1 and "oversize.txt" in issues[0] and "exceeds" in issues[0]


def test_attack3_total_size_limit_uses_small_fake_threshold(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"part{index}.txt").write_bytes(b"x" * 9)
    issues = scan_for_secrets(tmp_path, max_file_bytes=20, max_total_bytes=25)
    assert any("total staged size" in issue and "exceeds" in issue for issue in issues)


@pytest.mark.parametrize("extension", ["png", "woff2", "mp3"])
def test_policy_allowed_binary_types_pass_raw_byte_scan(tmp_path: Path, extension: str) -> None:
    (tmp_path / f"asset.{extension}").write_bytes(b"\x00\xff\x01" * 20)
    report = scan_for_secrets(tmp_path)
    assert report.blocks == [] and report.warnings == []
    assert report.files_scanned == [f"asset.{extension}"]


def test_policy_png_with_embedded_token_is_blocked(tmp_path: Path) -> None:
    (tmp_path / "asset.png").write_bytes(b"\x89PNG\x00" + b"ghp_" + b"a" * 36)
    report = scan_for_secrets(tmp_path)
    assert any(item.file == "asset.png" and item.severity == "block" for item in report.blocks)


def test_policy_png_with_embedded_token_blocks_repository_creation(tmp_path: Path) -> None:
    result, calls = _run_with_suspect_file(tmp_path, "assets/image.png", b"\x89PNG\x00" + b"ghp_" + b"a" * 36)
    assert result.status == "failed"
    assert any(item.name == "secret_scan" and not item.passed for item in result.checks)
    assert calls == {"github": 0, "git": 0}


@pytest.mark.parametrize("extension", ["exe", "dll", "so", "dylib", "pyc", "db", "sqlite", "zip", "tar", "gz", "7z"])
def test_policy_refused_file_types_block_regardless_of_content(tmp_path: Path, extension: str) -> None:
    (tmp_path / f"asset.{extension}").write_bytes(b"harmless bytes")
    report = scan_for_secrets(tmp_path)
    assert any(item.file == f"asset.{extension}" and item.severity == "block" for item in report.blocks)


def test_policy_executable_blocks_repository_creation(tmp_path: Path) -> None:
    result, calls = _run_with_suspect_file(tmp_path, "tool.exe", b"harmless bytes")
    assert result.status == "failed" and calls == {"github": 0, "git": 0}


def test_policy_generic_api_key_is_warning_with_line_number_only(tmp_path: Path) -> None:
    (tmp_path / "settings.py").write_text("# first line\napi_key = '" + "a" * 32 + "'\n")
    report = scan_for_secrets(tmp_path)
    assert report.blocks == []
    assert len(report.warnings) == 1
    warning = report.warnings[0]
    assert warning.file == "settings.py" and warning.line == 2 and warning.severity == "warn"
    assert "a" * 32 not in repr(report)
