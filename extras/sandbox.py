"""Run untrusted generated code (FR-5, sandbox rules 7.4).

Docker backend (preferred): one throwaway container per command, no network,
CPU/memory/pid limits, only the run workspace mounted, no secrets.
Process backend (fallback when Docker is unavailable): time limit and a scrubbed
environment only. Weaker isolation; the UI shows which backend is active.

AF_SANDBOX=docker|process|auto (default auto).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

IMAGE = os.environ.get("AF_SANDBOX_IMAGE", "agentforge-sandbox:latest")
SAFE_ENV_KEYS = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "LANG", "PYTHONIOENCODING")


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def log(self, limit: int = 4000) -> str:
        text = (self.stdout + ("\n" + self.stderr if self.stderr else "")).strip()
        if self.timed_out:
            text += "\n[timed out]"
        return text if len(text) <= limit else "...\n" + text[-limit:]


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, timeout=20)
    return r.returncode == 0


def backend_name() -> str:
    choice = os.environ.get("AF_SANDBOX", "auto")
    if choice == "auto":
        return "docker" if _docker_ready() else "process"
    return choice


def _scrubbed_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in SAFE_ENV_KEYS}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env  # NEBIUS_API_KEY and every other secret are dropped


def run(cmd: list[str], workdir: Path, timeout: int = 120) -> ExecResult:
    """Run cmd with workdir as the current directory. `python`/`node` resolve inside the sandbox."""
    if backend_name() == "docker":
        name = f"af-sbx-{uuid.uuid4().hex[:10]}"
        user = [] if os.name == "nt" else ["--user", f"{os.getuid()}:{os.getgid()}"]
        full = ["docker", "run", "--rm", "--name", name, "--network", "none", "--memory", "2g", "--cpus", "2",
                "--pids-limit", "256", *user, "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
                "-v", f"{workdir.resolve()}:/work", "-w", "/work", IMAGE, *cmd]
        try:
            r = subprocess.run(full, capture_output=True, text=True, timeout=timeout + 15)
            return ExecResult(r.returncode, r.stdout, r.stderr)
        except subprocess.TimeoutExpired as e:
            subprocess.run(["docker", "kill", name], capture_output=True)
            return ExecResult(124, _s(e.stdout), _s(e.stderr), timed_out=True)
    if cmd and cmd[0] == "python":
        cmd = [sys.executable, *cmd[1:]]
    try:
        r = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout, env=_scrubbed_env())
        return ExecResult(r.returncode, r.stdout, r.stderr)
    except subprocess.TimeoutExpired as e:
        return ExecResult(124, _s(e.stdout), _s(e.stderr), timed_out=True)
    except FileNotFoundError as e:
        return ExecResult(127, "", str(e))


def _s(b) -> str:
    return b.decode(errors="replace") if isinstance(b, bytes) else (b or "")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Preview:
    """Serves a generated app (backend + static frontend) for the live preview (FR-15)."""

    def __init__(self, workdir: Path, run_id: str):
        self.workdir, self.run_id = workdir, run_id
        self.port: int | None = None
        self._proc: subprocess.Popen | None = None
        self._container: str | None = None

    def start(self) -> int:
        self.stop()
        self.port = free_port()
        args = ["-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
        if backend_name() == "docker":
            self._container = f"af-preview-{self.run_id}"
            user = [] if os.name == "nt" else ["--user", f"{os.getuid()}:{os.getgid()}"]
            subprocess.run(["docker", "rm", "-f", self._container], capture_output=True)
            subprocess.run(["docker", "run", "-d", "--name", self._container, "--memory", "1g", "--cpus", "1",
                            "--pids-limit", "128", *user, "-e", "HOME=/tmp",
                            "-p", f"127.0.0.1:{self.port}:8000", "-v", f"{self.workdir.resolve()}:/work",
                            "-w", "/work", IMAGE, "python", *args], check=True, capture_output=True)
        else:
            args[-1] = str(self.port)
            args[args.index("0.0.0.0")] = "127.0.0.1"
            self._proc = subprocess.Popen([sys.executable, *args], cwd=self.workdir, env=_scrubbed_env(),
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return self.port

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        if self._container:
            subprocess.run(["docker", "rm", "-f", self._container], capture_output=True)
        self._proc = self._container = None
