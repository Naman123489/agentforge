
"""Detect and health-check a generated backend."""

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from .schemas import InternalCheckResult


STARTUP_TIMEOUT_SECONDS = 20
DEFAULT_HEALTH_TIMEOUT_SECONDS = 30
REQUEST_TIMEOUT_SECONDS = 1
LOG_TAIL_LINES = 60


def _result(passed, message, started, exit_code=None):
    return InternalCheckResult(
        name="backend_health",
        passed=passed,
        required=True,
        duration_s=round(time.monotonic() - started, 3),
        exit_code=exit_code,
        log_tail=message[-12000:],
    )


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _python_executable(backend):
    """Require a backend-specific virtual environment."""
    candidates = [
        backend / ".venv" / "Scripts" / "python.exe",
        backend / "venv" / "Scripts" / "python.exe",
        backend / ".venv" / "bin" / "python",
        backend / "venv" / "bin" / "python",
    ]

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    return None


def _python_entrypoint(backend):
    """Find a conventional FastAPI or Flask app entrypoint."""
    excluded = {
        ".venv", "venv", "node_modules", "__pycache__",
        ".git", "site-packages",
    }

    candidates = [
        path
        for path in backend.rglob("*.py")
        if not any(part in excluded for part in path.parts)
    ]

    candidates.sort(
        key=lambda path: (
            path.name.lower() not in {
                "main.py", "app.py", "server.py", "application.py"
            },
            len(path.parts),
            str(path).lower(),
        )
    )

    for path in candidates:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue

        if not re.search(r"\bapp\s*=", source):
            continue

        if re.search(r"\bFastAPI\s*\(", source):
            framework = "fastapi"
        elif re.search(r"\bFlask\s*\(", source):
            framework = "flask"
        else:
            continue

        module = ".".join(
            path.relative_to(backend).with_suffix("").parts
        )

        return framework, module

    return None


def _detect_backend(backend):
    """Return a launch command or a clear explanation of why unavailable."""
    python_entry = _python_entrypoint(backend)

    if python_entry:
        framework, module = python_entry
        python = _python_executable(backend)

        if python is None:
            return None, (
                f"Detected {framework}, but backend/.venv or backend/venv "
                "is missing. Provision the backend environment before "
                "running its health check."
            )

        if framework == "fastapi":
            command = [
                python, "-m", "uvicorn", f"{module}:app",
                "--host", "127.0.0.1",
            ]
        else:
            command = [
                python, "-m", "flask",
                "--app", f"{module}:app",
                "run", "--host", "127.0.0.1",
            ]

        return command, framework

    package_file = backend / "package.json"

    if package_file.is_file():
        try:
            package = json.loads(
                package_file.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"Could not read backend/package.json: {exc}"

        dependencies = {}
        dependencies.update(package.get("dependencies") or {})
        dependencies.update(package.get("devDependencies") or {})
        scripts = package.get("scripts") or {}

        if "express" in dependencies and scripts.get("start"):
            if not (backend / "node_modules").is_dir():
                return None, (
                    "Express was detected, but backend/node_modules is "
                    "missing. Install the backend's dependencies first."
                )

            npm = shutil.which("npm")
            if npm is None:
                return None, "npm is not installed or is not on PATH."

            return [npm, "start"], "node-express"

        return None, (
            "package.json exists, but no supported Express start "
            "configuration was found."
        )

    return None, (
        "No supported FastAPI, Flask, or Express entrypoint was detected."
    )


def _read_logs(log_file):
    try:
        log_file.flush()
        log_file.seek(0)
        content = log_file.read().decode("utf-8", errors="replace")
        return "\n".join(content.splitlines()[-LOG_TAIL_LINES:])
    except (OSError, ValueError):
        return "Could not read backend logs."


def _stop_process(process):
    """Stop the server process and its process group where possible."""
    if process.poll() is not None:
        return

    try:
        if os.name == "nt":
            subprocess.run(
                [
                    "taskkill", "/PID", str(process.pid), "/T", "/F"
                ],
                capture_output=True,
                timeout=5,
                check=False,
            )
        else:
            os.killpg(process.pid, signal.SIGTERM)

        process.wait(timeout=5)

    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass

        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass


def run_backend_health_check(
    workspace,
    timeout=DEFAULT_HEALTH_TIMEOUT_SECONDS,
):
    """Launch a detected backend and probe GET /health."""
    started = time.monotonic()
    workspace = Path(workspace).resolve()
    backend = workspace / "backend"

    if not backend.is_dir():
        return _result(
            False,
            "Backend health check failed: backend/ directory is missing.",
            started,
        )

    command, framework = _detect_backend(backend)

    if command is None:
        return _result(
            False,
            f"Backend health check unavailable: {framework}",
            started,
        )

    port = _free_port()
    url = f"http://127.0.0.1:{port}/health"

    env = os.environ.copy()
    env.update({
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "FLASK_RUN_HOST": "127.0.0.1",
        "FLASK_RUN_PORT": str(port),
        "PYTHONUNBUFFERED": "1",
    })

    if framework in {"fastapi", "flask"}:
        command.extend(["--port", str(port)])

    process = None

    try:
        with tempfile.TemporaryFile(mode="w+b") as log_file:
            creation_flags = (
                subprocess.CREATE_NEW_PROCESS_GROUP
                if os.name == "nt"
                else 0
            )

            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(backend),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    close_fds=True,
                    start_new_session=(os.name != "nt"),
                    creationflags=creation_flags,
                )
            except (OSError, ValueError) as exc:
                return _result(
                    False,
                    f"Could not start {framework} backend: {exc}",
                    started,
                )

            startup_deadline = (
                time.monotonic() + STARTUP_TIMEOUT_SECONDS
            )
            health_deadline = (
                startup_deadline + max(1, timeout)
            )
            last_error = "No successful health response received."

            while time.monotonic() < health_deadline:
                if process.poll() is not None:
                    logs = _read_logs(log_file)
                    return _result(
                        False,
                        f"{framework} exited with code "
                        f"{process.returncode}.\n{logs}",
                        started,
                        process.returncode,
                    )

                try:
                    request = urllib.request.Request(
                        url,
                        method="GET",
                        headers={
                            "User-Agent": "ForgeAI-Reviewer/1.0"
                        },
                    )

                    with urllib.request.urlopen(
                        request,
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    ) as response:
                        status = response.status
                        response.read(4096)

                    if 200 <= status < 300:
                        return _result(
                            True,
                            f"{framework} responded to GET /health "
                            f"with HTTP {status}.",
                            started,
                            0,
                        )

                    last_error = f"GET /health returned HTTP {status}."

                except urllib.error.HTTPError as exc:
                    last_error = f"GET /health returned HTTP {exc.code}."
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    last_error = f"Health probe failed: {exc}"

                time.sleep(0.25)

            logs = _read_logs(log_file)
            return _result(
                False,
                f"{framework} health check timed out. "
                f"{last_error}\n{logs}",
                started,
                process.poll(),
            )

    finally:
        if process is not None:
            _stop_process(process)

