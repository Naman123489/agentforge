
"""Configuration and check policies for AI2 Reviewer."""

from copy import deepcopy
from pathlib import Path
import json


DEFAULT_POLICY: dict[str, str] = {
    "contract": "required",
    "backend_lint": "advisory",
    "backend_types": "advisory",
    "backend_tests": "required",
    "backend_health": "required",
    "contract_conformance": "required",
    "frontend_build": "required",
    "frontend_types": "required",
    "frontend_tests": "required",
    "contract_drift": "required",
    "integration_e2e": "advisory",
    "security": "advisory",
}


DEFAULT_SETTINGS = {
    "check_timeout_seconds": 120,
    "max_attempts_per_stage": 3,
    "log_tail_lines": 60,
    "stop_on_repeated_failure": True,
}


def load_policy(path: str | Path | None = None) -> dict[str, str]:
    """Load policy overrides while preserving all default checks."""
    policy = deepcopy(DEFAULT_POLICY)

    if path is None:
        return policy

    with Path(path).open("r", encoding="utf-8") as file:
        overrides = json.load(file)

    if not isinstance(overrides, dict):
        raise ValueError("Policy must be a JSON object.")

    unknown = set(overrides) - set(DEFAULT_POLICY)
    if unknown:
        raise ValueError(f"Unknown policy checks: {sorted(unknown)}")

    for name, level in overrides.items():
        if not isinstance(level, str) or level not in {
            "required",
            "advisory",
        }:
            raise ValueError(
                f"Invalid policy level for {name!r}: {level!r}"
            )

        policy[name] = level

    return policy


def load_settings(path: str | Path | None = None) -> dict:
    """Load settings from JSON and validate their basic types."""
    settings = deepcopy(DEFAULT_SETTINGS)

    if path is not None:
        with Path(path).open("r", encoding="utf-8") as file:
            overrides = json.load(file)

        if not isinstance(overrides, dict):
            raise ValueError("Settings must be a JSON object.")

        unknown = set(overrides) - set(DEFAULT_SETTINGS)
        if unknown:
            raise ValueError(f"Unknown settings: {sorted(unknown)}")

        settings.update(overrides)

    timeout = settings["check_timeout_seconds"]
    attempts = settings["max_attempts_per_stage"]
    log_lines = settings["log_tail_lines"]

    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or timeout <= 0
    ):
        raise ValueError(
            "check_timeout_seconds must be a positive integer."
        )

    if (
        not isinstance(attempts, int)
        or isinstance(attempts, bool)
        or attempts < 1
    ):
        raise ValueError(
            "max_attempts_per_stage must be at least 1."
        )

    if (
        not isinstance(log_lines, int)
        or isinstance(log_lines, bool)
        or log_lines < 1
    ):
        raise ValueError("log_tail_lines must be at least 1.")

    if not isinstance(settings["stop_on_repeated_failure"], bool):
        raise ValueError(
            "stop_on_repeated_failure must be true or false."
        )

    return settings

