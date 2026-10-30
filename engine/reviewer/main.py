
"""Entry point for the AI2 Reviewer."""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .checks import run_project_checks
from .config import load_policy
from .events import EventEmitter, json_line_callback
from .explanations import explain_failures
from .schemas import CheckResult, ReviewReport
from .verdict import verdict


def _public_check_result(result) -> CheckResult:
    """Convert an internal check result to the platform API schema."""
    return CheckResult(
        name=result.name,
        passed=result.passed,
        exit_code=result.exit_code,
        duration_ms=max(0, round(result.duration_s * 1000)),
        log=result.log_tail,
    )


def _required_fix_text(fix) -> str:
    """Convert a structured internal fix into a platform-compatible string."""
    if isinstance(fix, str):
        return fix

    if hasattr(fix, "model_dump"):
        fix = fix.model_dump()

    if isinstance(fix, dict):
        owner = fix.get("owner", "shared")
        file = fix.get("file")
        problem = fix.get("problem", "Required fix")
        suggestion = fix.get("suggestion", "")

        location = f" ({file})" if file else ""
        advice = f" Suggested action: {suggestion}" if suggestion else ""

        return f"[{owner}]{location} {problem}.{advice}".strip()

    return str(fix)


class Reviewer:
    """Coordinate project checks and produce a platform-compatible report."""

    def __init__(
        self,
        router: Any = None,
        sandbox: Any = None,
        emit=None,
        policy: dict[str, str] | None = None,
    ):
        self.router = router
        self.sandbox = sandbox
        self.emit = emit
        self.policy = policy if policy is not None else load_policy()

    def review_stage(
        self,
        ctx: Any,
        stage: Any,
        build: Any,
        attempt: int,
    ) -> ReviewReport:
        """Review a workspace and return the platform API report."""
        if attempt < 1:
            raise ValueError("attempt must be at least 1")

        workspace = Path(
            getattr(build, "workspace", None)
            or getattr(ctx, "workspace", ".")
        ).resolve()

        run_id = str(
            getattr(ctx, "run_id", None) or uuid.uuid4()
        )
        stage_id = str(
            getattr(stage, "id", None)
            or getattr(stage, "stage_id", None)
            or "stage"
        )

        emitter = EventEmitter(
            run_id=run_id,
            callback=self.emit,
        )

        emitter.emit(
            "review.started",
            stage_id=stage_id,
            attempt=attempt,
        )

        results = run_project_checks(
            workspace=workspace,
            policy=self.policy,
        )

        for result in results:
            emitter.emit(
                "check.result",
                stage_id=stage_id,
                name=result.name,
                passed=result.passed,
                required=result.required,
                duration_s=result.duration_s,
                log_tail=result.log_tail,
            )

        decision = verdict(results, self.policy)

        fixes = []
        if decision == "reject":
            raw_fixes = explain_failures(
                checks=[
                    result
                    for result in results
                    if not result.passed and result.required
                ],
                router=self.router,
            )
            fixes = [_required_fix_text(fix) for fix in raw_fixes]

        public_checks = [
            _public_check_result(result)
            for result in results
        ]

        report = ReviewReport(
            stage_id=stage_id,
            verdict=decision,
            checks=public_checks,
            required_fixes=fixes,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        summary = (
            "All required checks passed."
            if decision == "approve"
            else (
                "Review rejected: "
                f"{len(fixes)} required fix(es) identified."
            )
        )

        emitter.emit(
            "review.verdict",
            stage_id=stage_id,
            attempt=attempt,
            verdict=report.verdict,
            summary=summary,
            required_fixes=report.required_fixes,
        )

        return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the ForgeAI Reviewer against a project workspace."
    )
    parser.add_argument(
        "workspace",
        help="Path to the generated project workspace",
    )
    parser.add_argument(
        "--policy",
        help="Optional JSON file defining required/advisory checks",
    )
    parser.add_argument(
        "--stage",
        default="manual-review",
        help="Stage identifier",
    )
    parser.add_argument(
        "--attempt",
        type=int,
        default=1,
        help="Review attempt number",
    )
    args = parser.parse_args()

    try:
        policy = load_policy(args.policy)

        if args.attempt < 1:
            raise ValueError("--attempt must be at least 1")

        run_id = str(uuid.uuid4())
        callback = json_line_callback()
        emitter = EventEmitter(
            run_id=run_id,
            callback=callback,
        )

        reviewer = Reviewer(
            emit=callback,
            policy=policy,
        )

        class Context:
            pass

        class Stage:
            pass

        class Build:
            pass

        ctx = Context()
        ctx.run_id = run_id
        ctx.workspace = args.workspace

        stage = Stage()
        stage.id = args.stage

        build = Build()
        build.workspace = args.workspace

        report = reviewer.review_stage(
            ctx=ctx,
            stage=stage,
            build=build,
            attempt=args.attempt,
        )

        print(report.model_dump_json(indent=2))
        return 0 if report.verdict == "approve" else 1

    except (OSError, ValueError, RuntimeError) as exc:
        print(
            json.dumps(
                {
                    "error": str(exc),
                    "verdict": "reject",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
