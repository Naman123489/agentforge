
"""Determine the Reviewer verdict from required checks."""


def verdict(checks, policy=None):
    """Approve only when every expected required check passes."""
    policy = policy or {}

    for name, level in policy.items():
        if level not in {"required", "advisory"}:
            raise ValueError(f"Invalid policy for {name}: {level}")

    names = [check.name for check in checks]

    # Duplicate results make the report ambiguous.
    if len(names) != len(set(names)):
        return "reject"

    # Every required policy check must be present exactly once.
    for name, level in policy.items():
        if level != "required":
            continue

        matching = [check for check in checks if check.name == name]

        if len(matching) != 1:
            return "reject"

        check = matching[0]

        if not check.passed:
            return "reject"

        # A successful subprocess check must have exit code zero.
        # Structural checks can pass without running a subprocess.
        if check.exit_code is not None and check.exit_code != 0:
            return "reject"

        if check.exit_code is None and check.duration_s > 0:
            return "reject"

    # Also reject failed checks explicitly marked as required.
    for check in checks:
        if check.required and not check.passed:
            return "reject"

    return "approve"

