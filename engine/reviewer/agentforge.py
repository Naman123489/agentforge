"""Run the AI2 Reviewer on an AgentForge workspace.

AI2's generic run_project_checks() expects a backend virtualenv, a frontend test script and npm on PATH. AgentForge
workspaces are built and checked by the engine's own suites (backend_checks, frontend_checks), so this adapter feeds
those executed results, plus AI2's contract checks and an advisory bandit scan, into AI2's verdict(). The verdict is
deterministic: a required check that failed or did not run rejects the stage. No model is involved.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from engine.agents.backend_checks import run_backend_checks
from engine.agents.contract import Contract
from engine.agents.frontend_checks import run_frontend_checks
from engine.agents.runner import Runner
from engine.schemas import CheckResult as EngineCheck

from .check_runner import run_check
from .checks import run_contract_checks
from .events import EventEmitter
from .explanations import fallback_explanations
from .schemas import InternalCheckResult, RequiredFix
from .verdict import verdict

# AI2 policy names for the checks this adapter adds. Engine suite checks are always required.
AGENTFORGE_POLICY = {"contract": "required", "contract_conformance": "required", "contract_drift": "required",
                     "security": "advisory"}


def _from_engine(prefix: str, c: EngineCheck) -> InternalCheckResult:
    """An engine check that was skipped (tool missing, backend not importable) did not run: it does not pass."""
    log = c.log if not c.skipped else f"NOT EXECUTED: {c.log}"
    return InternalCheckResult(name=f"{prefix}: {c.name}", passed=c.passed and not c.skipped, required=True,
                               duration_s=(c.duration_ms or 0) / 1000,
                               exit_code=c.exit_code if c.exit_code is not None else (0 if c.passed and not c.skipped else 1),
                               log_tail=log[-6000:])


def collect_checks(root: Path, contract: Contract, runner: Runner, with_frontend: bool,
                   on_check: Callable[[InternalCheckResult], None] | None = None) -> list[InternalCheckResult]:
    results: list[InternalCheckResult] = []

    def add(r: InternalCheckResult) -> None:
        results.append(r)
        if on_check:
            on_check(r)

    for r in run_contract_checks(root, AGENTFORGE_POLICY):
        add(r)
    for c in run_backend_checks(root, contract, runner):
        add(_from_engine("backend", c))
    if with_frontend:  # both sides exist now, so serving the UI with the backend is required
        for c in run_frontend_checks(root, contract, runner, require_integration=True):
            add(_from_engine("frontend", c))
    if (root / "backend").is_dir():
        # Application code only: generated tests (backend/test_*.py) build SQL for fixtures and would be flagged.
        sources = [p.relative_to(root).as_posix() for p in sorted((root / "backend").rglob("*.py"))
                   if not p.name.startswith("test_") and "tests" not in p.parts and ".venv" not in p.parts]
        if not sources:
            pass
        elif shutil.which("bandit") or _has_module("bandit"):
            add(run_check("security", [sys.executable, "-m", "bandit", "-q", "-ll", *sources], root, timeout=120, required=False))
        else:
            add(InternalCheckResult(name="security", passed=False, required=False, log_tail="NOT EXECUTED: bandit is not installed."))
    return results


def _has_module(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def policy_for(results: list[InternalCheckResult]) -> dict[str, str]:
    return {r.name: "required" if r.required else "advisory" for r in results}


def public_check(r: InternalCheckResult) -> dict:
    """Platform CheckResult, plus the dashboard's `skipped` and `required` extensions."""
    return {"name": r.name, "passed": r.passed, "exit_code": r.exit_code, "duration_ms": max(0, round(r.duration_s * 1000)),
            "log": r.log_tail, "skipped": r.log_tail.startswith("NOT EXECUTED"), "required": r.required}


def fix_text(f: RequiredFix) -> str:
    where = f" ({f.file})" if f.file else ""
    return f"[{f.owner}]{where} {f.problem.rstrip('.')}. Suggested action: {f.suggestion}"


def review(root: Path, contract: Contract, runner: Runner, stage_id: str, attempt: int, with_frontend: bool,
           emitter: EventEmitter | None = None) -> dict:
    """Execute every check and decide. Returns the platform ReviewReport (plus `summary`)."""
    if emitter:
        emitter.emit("review.started", stage_id=stage_id, attempt=attempt)
    on_check = (lambda r: emitter.emit("check.result", stage_id=stage_id, name=r.name, passed=r.passed,
                                       required=r.required, duration_s=r.duration_s)) if emitter else None
    results = collect_checks(root, contract, runner, with_frontend, on_check)
    decision = verdict(results, policy_for(results))
    failing_required = [r for r in results if r.required and not r.passed]
    fixes = fallback_explanations(failing_required) if decision != "approve" else []
    advisory_failed = [r.name for r in results if not r.required and not r.passed]
    summary = ("All required checks passed." if decision == "approve"
               else f"Review rejected: {len(failing_required)} required check(s) failed.")
    if advisory_failed:
        summary += f" Advisory findings: {', '.join(advisory_failed)}."
    report = {"stage_id": stage_id, "verdict": decision, "checks": [public_check(r) for r in results],
              "required_fixes": [fix_text(f) for f in fixes], "summary": summary,
              "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    if emitter:
        emitter.emit("review.verdict", stage_id=stage_id, attempt=attempt, verdict=decision, summary=summary,
                     required_fixes=report["required_fixes"])
    return report
