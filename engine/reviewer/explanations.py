"""Failure explanations for AI2 Reviewer.

The explanation layer never decides whether a build is approved.
The verdict engine remains the sole authority for approval/rejection.
"""

from typing import Any

from .schemas import InternalCheckResult, RequiredFix


def fallback_explanations(
    checks: list[InternalCheckResult],
) -> list[RequiredFix]:
    """Generate useful fixes from check evidence without an LLM."""
    fixes: list[RequiredFix] = []

    for check in checks:
        if check.passed:
            continue

        name = check.name.lower()
        evidence = check.log_tail.strip()
        first_error = next(
            (
                line.strip()
                for line in evidence.splitlines()
                if any(
                    marker in line.lower()
                    for marker in (
                        "error",
                        "failed",
                        "exception",
                        "not found",
                        "timed out",
                        "could not run",
                    )
                )
            ),
            evidence.splitlines()[0] if evidence else "No log output available.",
        )

        if "contract" in name:
            owner = "planner"
            suggestion = (
                "Validate the OpenAPI document and align its paths, "
                "methods, schemas, and response types with the plan."
            )
        elif "frontend" in name:
            owner = "frontend"
            suggestion = (
                "Inspect the frontend build or test output, fix the "
                "reported issue, and rerun the failed check."
            )
        else:
            owner = "backend"
            suggestion = (
                "Inspect the backend check output, fix the reported "
                "issue, and rerun the failed check."
            )

        fixes.append(
            RequiredFix(
                owner=owner,
                file=None,
                problem=f"{check.name} failed: {first_error[:500]}",
                suggestion=suggestion,
            )
        )

    return fixes


def explain_failures(
    checks: list[InternalCheckResult],
    router: Any = None,
    contract_excerpt: str = "",
) -> list[RequiredFix]:
    """
    Return structured fix suggestions.

    Without a configured router, use deterministic fallback guidance.
    A future LLM integration may improve suggestions, but its output
    must be validated and must never determine the review verdict.
    """
    # Router integration is intentionally deferred until the model
    # interface and response contract are defined by the orchestrator.
    return fallback_explanations(checks)
