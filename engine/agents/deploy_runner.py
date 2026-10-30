# Deploy-only runner. Do NOT replace with the sandbox runner: git push needs network access and environment variables.
"""Bounded subprocess runner for deployment commands."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .deploy import redact


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class Runner(Protocol):
    def run(
        self, cmd: list[str], workdir: Path, timeout: float, env: dict[str, str] | None = None
    ) -> RunResult: ...


class SubprocessRunner:
    def run(
        self, cmd: list[str], workdir: Path, timeout: float, env: dict[str, str] | None = None
    ) -> RunResult:
        if not isinstance(cmd, list):
            raise TypeError("cmd must be a list")
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        try:
            result = subprocess.run(
                cmd,
                cwd=Path(workdir),
                timeout=timeout,
                env=merged_env,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return RunResult(result.returncode, redact(result.stdout), redact(result.stderr))
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            return RunResult(124, redact(stdout), redact(stderr), timed_out=True)
        except FileNotFoundError as exc:
            return RunResult(127, "", redact(f"executable not found: {exc.filename}"))
        except OSError as exc:
            return RunResult(127, "", redact(str(exc)))
