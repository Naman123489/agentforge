"""Where the Backend agent executes generated code. Pluggable: the Sandbox owner can pass their own runner.

Any object with  run(cmd: list[str], workdir: Path, timeout: int) -> ExecResult  works.

DockerRunner  one throwaway container per command: no network, CPU/memory/pid limits, only the
              workspace mounted, no secrets (requirements 7.4). Build the image once:
              docker build -t agentforge-backend-sandbox deploy/backend-sandbox
LocalRunner   subprocess with a time limit and a scrubbed environment (API keys removed).
              Weaker isolation; for development when Docker is unavailable.
default_runner() picks Docker when the image exists, else Local (AF_SANDBOX=docker|local overrides).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

IMAGE = os.environ.get("AF_BACKEND_SANDBOX_IMAGE", "agentforge-backend-sandbox:latest")
SAFE_ENV = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "LANG", "PYTHONIOENCODING", "USERPROFILE",
            # npm/node on Windows need these for the cache and executable lookup; none are secrets
            "APPDATA", "LOCALAPPDATA", "PATHEXT", "COMSPEC", "PROGRAMFILES", "SYSTEMDRIVE")


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


class Runner(Protocol):
    name: str

    def run(self, cmd: list[str], workdir: Path, timeout: int = 120) -> ExecResult: ...


def _text(b) -> str:
    return b.decode(errors="replace") if isinstance(b, bytes) else (b or "")


class LocalRunner:
    name = "local"

    def run(self, cmd: list[str], workdir: Path, timeout: int = 120) -> ExecResult:
        if cmd and cmd[0] == "python":
            cmd = [sys.executable, *cmd[1:]]
        elif cmd and cmd[0] in ("node", "npm", "npx"):
            exe = shutil.which(cmd[0])  # Windows: npm.cmd / npx.cmd are not found without PATHEXT lookup
            if not exe:
                return ExecResult(127, "", f"{cmd[0]} not found on PATH")
            cmd = [exe, *cmd[1:]]
        env = {k: v for k, v in os.environ.items() if k.upper() in SAFE_ENV}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            r = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=timeout, env=env)
            return ExecResult(r.returncode, r.stdout, r.stderr)
        except subprocess.TimeoutExpired as e:
            return ExecResult(124, _text(e.stdout), _text(e.stderr), timed_out=True)
        except FileNotFoundError as e:
            return ExecResult(127, "", str(e))


class DockerRunner:
    name = "docker"

    def __init__(self, image: str = IMAGE, memory: str = "1g", cpus: str = "2"):
        self.image, self.memory, self.cpus = image, memory, cpus

    def run(self, cmd: list[str], workdir: Path, timeout: int = 120) -> ExecResult:
        name = f"af-be-{uuid.uuid4().hex[:10]}"
        user = [] if os.name == "nt" else ["--user", f"{os.getuid()}:{os.getgid()}"]
        full = ["docker", "run", "--rm", "--name", name, "--network", "none", "--memory", self.memory,
                "--cpus", self.cpus, "--pids-limit", "256", *user, "-e", "HOME=/tmp",
                "-e", "PYTHONDONTWRITEBYTECODE=1", "-v", f"{workdir.resolve()}:/work", "-w", "/work",
                self.image, *cmd]
        try:
            r = subprocess.run(full, capture_output=True, text=True, timeout=timeout + 20)
            return ExecResult(r.returncode, r.stdout, r.stderr)
        except subprocess.TimeoutExpired as e:
            subprocess.run(["docker", "kill", name], capture_output=True)
            return ExecResult(124, _text(e.stdout), _text(e.stderr), timed_out=True)


def default_runner() -> Runner:
    choice = os.environ.get("AF_SANDBOX", "auto")
    if choice == "local":
        return LocalRunner()
    if choice == "docker":
        return DockerRunner()
    if shutil.which("docker") and subprocess.run(["docker", "image", "inspect", IMAGE],
                                                 capture_output=True).returncode == 0:
        return DockerRunner()
    return LocalRunner()
