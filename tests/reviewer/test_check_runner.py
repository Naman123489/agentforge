
import sys
import tempfile
from pathlib import Path

from engine.reviewer.check_runner import run_check


def test_successful_command_passes():
    result = run_check(
        name="python-version",
        command=[sys.executable, "--version"],
        cwd=".",
    )

    assert result.passed is True
    assert result.exit_code == 0
    assert result.duration_s >= 0


def test_failed_command_is_rejected():
    result = run_check(
        name="intentional-failure",
        command=[
            sys.executable,
            "-c",
            "raise SystemExit(2)",
        ],
        cwd=".",
    )

    assert result.passed is False
    assert result.exit_code == 2


def test_timeout_is_recorded_as_failure():
    result = run_check(
        name="timeout-test",
        command=[
            sys.executable,
            "-c",
            "import time; time.sleep(5)",
        ],
        cwd=".",
        timeout=1,
    )

    assert result.passed is False
    assert result.exit_code is None
    assert "TIMED OUT" in result.log_tail


def test_missing_directory_is_recorded_as_failure():
    missing_path = (
        Path(tempfile.gettempdir())
        / "reviewer-directory-that-does-not-exist"
    )

    result = run_check(
        name="missing-directory",
        command=[sys.executable, "--version"],
        cwd=missing_path,
    )

    assert result.passed is False
    assert result.exit_code is None
    assert "COULD NOT RUN" in result.log_tail


def test_log_output_is_limited_to_last_60_lines():
    result = run_check(
        name="log-tail-test",
        command=[
            sys.executable,
            "-c",
            "for i in range(100): print(f'line-{i}')",
        ],
        cwd=".",
    )

    lines = result.log_tail.splitlines()

    assert result.passed is True
    assert len(lines) <= 60
    assert "line-99" in result.log_tail
