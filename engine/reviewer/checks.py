
"""Run contract and project checks for AgentForge."""

import json
import re
import sys
from pathlib import Path

from .backend_health import run_backend_health_check
from .check_runner import run_check
from .config import DEFAULT_POLICY
from .schemas import InternalCheckResult



EXCLUDED_DIRS = {
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".git",
    "site-packages",
    "dist",
    "build",
}


def load_policy(policy_file=None):
    """Load and validate check-policy overrides."""
    policy = DEFAULT_POLICY.copy()

    if policy_file is not None:
        path = Path(policy_file)
        try:
            overrides = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid policy JSON: {exc}") from exc

        if not isinstance(overrides, dict):
            raise ValueError("Policy must be a JSON object.")

        unknown = set(overrides) - set(policy)
        if unknown:
            raise ValueError(
                f"Unknown policy checks: {sorted(unknown)}"
            )

        policy.update(overrides)

    invalid = {
        name: level
        for name, level in policy.items()
        if level not in {"required", "advisory"}
    }
    if invalid:
        raise ValueError(f"Invalid policy entries: {invalid}")

    return policy


def _has_source_files(directory, suffixes):
    """Detect source files without traversing dependency/build folders."""
    if not directory.is_dir():
        return False

    for path in directory.rglob("*"):
        if not path.is_file():
            continue

        relative = path.relative_to(directory)

        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue

        if path.suffix.lower() in suffixes:
            return True

    return False


def _read_package(path):
    """Read and validate a package.json file."""
    try:
        package = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read {path}: {exc}") from exc

    if not isinstance(package, dict):
        raise ValueError(f"{path} must contain a JSON object.")

    scripts = package.get("scripts") or {}
    dependencies = package.get("dependencies") or {}
    dev_dependencies = package.get("devDependencies") or {}

    if not isinstance(scripts, dict):
        raise ValueError(f"{path}: scripts must be an object.")

    if not isinstance(dependencies, dict):
        raise ValueError(f"{path}: dependencies must be an object.")

    if not isinstance(dev_dependencies, dict):
        raise ValueError(
            f"{path}: devDependencies must be an object."
        )

    return package, scripts, {
        **dependencies,
        **dev_dependencies,
    }


def _is_node_backend(backend):
    """Detect a Node backend from its package configuration."""
    package_file = backend / "package.json"

    if not package_file.is_file():
        return False

    _, scripts, dependencies = _read_package(package_file)

    # A start script plus a server framework is a useful signal.
    server_frameworks = {
        "express",
        "fastify",
        "koa",
        "@nestjs/core",
        "hono",
    }

    has_framework = any(
        name in dependencies for name in server_frameworks
    )

    return has_framework and bool(scripts.get("start"))


def run_project_checks(workspace, policy=None, timeout=120):
    """Run applicable checks and report missing required capabilities."""
    workspace = Path(workspace).resolve()
    policy = dict(DEFAULT_POLICY if policy is None else policy)
    results = []

    invalid = {
        name: level
        for name, level in policy.items()
        if level not in {"required", "advisory"}
    }
    if invalid:
        raise ValueError(f"Invalid policy entries: {invalid}")

    def required(name):
        return policy.get(name, "required") == "required"

    def add_result(
        name,
        passed,
        message,
        exit_code=None,
        duration_s=0,
    ):
        results.append(
            InternalCheckResult(
                name=name,
                passed=passed,
                required=required(name),
                duration_s=duration_s,
                exit_code=exit_code,
                log_tail=message,
            )
        )

    def missing(name, message):
        add_result(name, False, message)

    def not_applicable(name, message):
        add_result(
            name,
            True,
            f"Not applicable: {message}",
            exit_code=0,
        )

    def execute(name, command, cwd):
        results.append(
            run_check(
                name=name,
                command=command,
                cwd=cwd,
                timeout=timeout,
                required=required(name),
            )
        )

    # Detect the application stacks present in this workspace.
    backend = workspace / "backend"
    frontend = workspace / "frontend"

    python_backend = _has_source_files(backend, {".py"})
    node_backend = _is_node_backend(backend)

    frontend_package = frontend / "package.json"
    frontend_present = frontend_package.is_file()

    results.extend(run_contract_checks(workspace, policy, timeout))

    # Backend checks.
    if python_backend:
        # Python takes precedence if a workspace contains both stacks.
        # The health checker currently supports Python and Express.
        health_result = run_backend_health_check(
            workspace,
            timeout=min(timeout, 60),
        )
        health_result.required = required("backend_health")
        results.append(health_result)

        execute("backend_lint", ["ruff", "check", "."], backend)
        execute("backend_types", ["mypy", "."], backend)
        execute("backend_tests", ["pytest", "-q"], backend)
        execute(
            "security",
            ["bandit", "-q", "-r", "."],
            backend,
        )

    elif node_backend:
        health_result = run_backend_health_check(
            workspace,
            timeout=min(timeout, 60),
        )
        health_result.required = required("backend_health")
        results.append(health_result)

        package_file = backend / "package.json"
        _, scripts, dependencies = _read_package(package_file)

        npm = "npm"

        if scripts.get("lint"):
            execute("backend_lint", [npm, "run", "lint"], backend)
        else:
            not_applicable(
                "backend_lint",
                "No Node backend lint script is configured.",
            )

        if scripts.get("typecheck"):
            execute(
                "backend_types",
                [npm, "run", "typecheck"],
                backend,
            )
        elif scripts.get("types"):
            execute("backend_types", [npm, "run", "types"], backend)
        elif (backend / "tsconfig.json").is_file():
            execute(
                "backend_types",
                [npm, "exec", "--", "tsc", "--noEmit"],
                backend,
            )
        else:
            not_applicable(
                "backend_types",
                "No TypeScript configuration or type-check script exists.",
            )

        if scripts.get("test"):
            execute("backend_tests", [npm, "test"], backend)
        else:
            missing(
                "backend_tests",
                "Node backend has no test script configured.",
            )

        if (backend / "package-lock.json").is_file():
            execute(
                "security",
                [
                    npm,
                    "audit",
                    "--omit=dev",
                    "--audit-level=high",
                ],
                backend,
            )
        else:
            not_applicable(
                "security",
                "No package-lock.json is available for npm audit.",
            )

    else:
        not_applicable(
            "backend_health",
            "No supported Python or Node server backend was detected.",
        )
        not_applicable(
            "backend_lint",
            "No supported backend stack was detected.",
        )
        not_applicable(
            "backend_types",
            "No supported backend stack was detected.",
        )
        not_applicable(
            "backend_tests",
            "No supported backend stack was detected.",
        )
        not_applicable(
            "security",
            "No supported backend stack was detected.",
        )

    # Frontend checks.
    if not frontend_present:
        for name in (
            "frontend_build",
            "frontend_types",
            "frontend_tests",
        ):
            not_applicable(
                name,
                "frontend/package.json was not found.",
            )
    else:
        try:
            _, scripts, _ = _read_package(frontend_package)

            if scripts.get("build"):
                execute(
                    "frontend_build",
                    ["npm", "run", "build"],
                    frontend,
                )
            else:
                missing(
                    "frontend_build",
                    "No frontend build script is configured.",
                )

            if (frontend / "tsconfig.json").is_file():
                execute(
                    "frontend_types",
                    [
                        "npm",
                        "exec",
                        "--",
                        "tsc",
                        "--noEmit",
                    ],
                    frontend,
                )
            else:
                not_applicable(
                    "frontend_types",
                    "No frontend TypeScript configuration was found.",
                )

            if scripts.get("test"):
                execute(
                    "frontend_tests",
                    ["npm", "test", "--", "--run"],
                    frontend,
                )
            else:
                missing(
                    "frontend_tests",
                    "No frontend test script is configured.",
                )

        except (OSError, ValueError) as exc:
            for name in (
                "frontend_build",
                "frontend_types",
                "frontend_tests",
            ):
                missing(
                    name,
                    f"Could not read frontend configuration: {exc}",
                )

    # Explicitly advisory until end-to-end orchestration is implemented.
    missing(
        "integration_e2e",
        "End-to-end test orchestration is not implemented.",
    )

    return results


def run_contract_checks(workspace, policy=None, timeout=120):
    """Contract structure, OpenAPI conformance and types.ts drift (no backend/frontend checks)."""
    workspace = Path(workspace).resolve()
    policy = dict(DEFAULT_POLICY if policy is None else policy)
    results = []

    def required(name):
        return policy.get(name, "required") == "required"

    def add_result(name, passed, message, exit_code=None, duration_s=0):
        results.append(
            InternalCheckResult(
                name=name,
                passed=passed,
                required=required(name),
                duration_s=duration_s,
                exit_code=exit_code,
                log_tail=message,
            )
        )

    def missing(name, message):
        add_result(name, False, message)

    def execute(name, command, cwd):
        results.append(
            run_check(
                name=name,
                command=command,
                cwd=cwd,
                timeout=timeout,
                required=required(name),
            )
        )

    # Validate the generated application's OpenAPI contract.
    contract = workspace / "contract" / "openapi.yaml"
    spec = None
    contract_valid = False

    if not contract.is_file():
        missing("contract", "contract/openapi.yaml was not found.")
        missing(
            "contract_conformance",
            "Cannot validate without contract/openapi.yaml.",
        )
        missing(
            "contract_drift",
            "Cannot compare generated types without a valid contract.",
        )
    else:
        try:
            import yaml

            spec = yaml.safe_load(
                contract.read_text(encoding="utf-8")
            )

            if not isinstance(spec, dict):
                raise ValueError(
                    "OpenAPI document must be an object."
                )

            if spec.get("openapi") != "3.1.0":
                raise ValueError("Expected OpenAPI version 3.1.0.")

            info = spec.get("info")
            if not isinstance(info, dict):
                raise ValueError("OpenAPI info section is missing.")

            if not info.get("title") or not info.get("version"):
                raise ValueError("OpenAPI info needs title and version.")

            paths = spec.get("paths")
            if not isinstance(paths, dict) or not paths:
                raise ValueError("OpenAPI paths must be non-empty.")

            components = spec.get("components", {})
            if not isinstance(components, dict):
                raise ValueError("OpenAPI components must be an object.")

            schemas = components.get("schemas", {})
            if not isinstance(schemas, dict):
                raise ValueError(
                    "OpenAPI components.schemas must be an object."
                )

            contract_valid = True

        except (OSError, ValueError, ImportError) as exc:
            missing("contract", f"Contract validation failed: {exc}")
            missing(
                "contract_conformance",
                "Contract could not be parsed or is structurally invalid.",
            )
            missing(
                "contract_drift",
                "Cannot compare types against an invalid contract.",
            )
        except Exception as exc:
            # Includes YAML parser errors from malformed documents.
            missing(
                "contract",
                f"Contract parsing failed: {type(exc).__name__}: {exc}",
            )
            missing(
                "contract_conformance",
                "Contract could not be parsed.",
            )
            missing(
                "contract_drift",
                "Cannot compare types against an invalid contract.",
            )

    if contract_valid:
        add_result(
            "contract",
            True,
            "Basic OpenAPI structure is valid.",
            exit_code=0,
        )

        execute(
            "contract_conformance",
            [
                sys.executable,
                "-m",
                "openapi_spec_validator",
                str(contract),
            ],
            workspace,
        )

        # This is a name-presence check, not full type equivalence.
        types_file = workspace / "types.ts"

        if not types_file.is_file():
            missing("contract_drift", "Generated types.ts was not found.")
        else:
            try:
                actual = types_file.read_text(encoding="utf-8")
                schemas = spec["components"].get("schemas", {})
                absent = []

                for schema_name, schema in schemas.items():
                    if not re.search(
                        rf"\b{re.escape(schema_name)}\b",
                        actual,
                    ):
                        absent.append(f"schema {schema_name}")
                        continue

                    if not isinstance(schema, dict):
                        continue

                    properties = schema.get("properties", {})
                    if not isinstance(properties, dict):
                        continue

                    for property_name in properties:
                        if not re.search(
                            rf"\b{re.escape(property_name)}\b",
                            actual,
                        ):
                            absent.append(
                                f"{schema_name}.{property_name}"
                            )

                if absent:
                    missing(
                        "contract_drift",
                        "Generated types.ts appears to omit contract "
                        "definitions: " + ", ".join(absent[:30]),
                    )
                else:
                    add_result(
                        "contract_drift",
                        True,
                        "Contract schema and property names were found "
                        "in types.ts. Full type equivalence is not "
                        "verified by this check.",
                        exit_code=0,
                    )

            except (OSError, ValueError, TypeError) as exc:
                missing(
                    "contract_drift",
                    f"Could not inspect generated types: {exc}",
                )

    return results
