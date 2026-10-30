
"""Run a check safely and capture its result for the Reviewer."""

import shutil
import subprocess
import time
from pathlib import Path

from .schemas import InternalCheckResult


def run_check(
    name: str,
    command: list[str],
    cwd: str | Path,
    timeout: int = 120,
    required: bool = True,
) -> InternalCheckResult:
    """Execute a command and capture a bounded log tail.

    Commands are executed without a shell. A failed or unavailable command
    is reported as a failed check rather than being treated as a pass.
    """
    start = time.monotonic()
    exit_code = None
    passed = False

    # Resolve the executable (npm is npm.cmd on Windows) without using a shell.
    resolved = shutil.which(command[0]) if command else None
    if resolved:
        command = [resolved, *command[1:]]

    try:
        result = subprocess.run(
            command,
            cwd=Path(cwd).resolve(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
        exit_code = result.returncode
        output = "\n".join(
            part
            for part in (result.stdout, result.stderr)
            if part
        )
        passed = exit_code == 0

        if not output.strip():
            output = "Command completed with no output."

    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""

        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")

        output = (
            f"CHECK TIMED OUT after {timeout} seconds.\n"
            f"{stdout}\n{stderr}"
        )

    except (OSError, ValueError) as exc:
        output = (
            f"CHECK COULD NOT RUN: {type(exc).__name__}: {exc}"
        )

    duration = time.monotonic() - start
    log_tail = "\n".join(output.splitlines()[-60:])

    return InternalCheckResult(
        name=name,
        passed=passed,
        required=required,
        duration_s=round(duration, 3),
        exit_code=exit_code,
        log_tail=log_tail,
    )

