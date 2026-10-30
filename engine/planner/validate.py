"""Deterministic validation of Planner output. Model output is untrusted until it passes here.

validate_plan / validate_openapi are kept from ForgeAI ai1-planner (they raise ValueError). plan_problems and
contract_problems_for collect every issue instead, so the model can be re-asked with the full list.
"""

from __future__ import annotations

import json
import re
from typing import Any

import yaml

from engine.agents.contract import ContractError, contract_problems, load_contract

from .schemas import ProjectPlan

ENDPOINT_RE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s?#]*)(?:[?#]\S*)?$")  # query strings ignored
OWNER_PREFIX = {"backend": "backend/", "frontend": "frontend/"}


# ---- ai1-planner checks (unchanged behaviour) -------------------------------------------------------
def validate_plan(plan: ProjectPlan) -> None:
    task_ids = [task.task_id for task in plan.tasks]
    if len(task_ids) != len(set(task_ids)):
        raise ValueError("The plan contains duplicate task IDs.")
    known_ids = set(task_ids)
    for task in plan.tasks:
        for dependency in task.dependencies:
            if dependency not in known_ids:
                raise ValueError(f"{task.task_id} references unknown dependency {dependency}.")
            if dependency == task.task_id:
                raise ValueError(f"{task.task_id} cannot depend on itself.")
    dependencies = {task.task_id: task.dependencies for task in plan.tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise ValueError(f"Dependency cycle detected at {task_id}.")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in dependencies[task_id]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in task_ids:
        visit(task_id)


def validate_openapi(spec: dict[str, Any]) -> None:
    if spec.get("openapi") != "3.1.0":
        raise ValueError("Application contract must use OpenAPI 3.1.0.")
    info = spec.get("info")
    if not isinstance(info, dict) or not info.get("title") or not info.get("version"):
        raise ValueError("Application contract needs info.title and info.version.")
    paths = spec.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise ValueError("Application contract must define at least one path.")


# ---- AgentForge additions ----------------------------------------------------------------------------
def contract_problems_for(spec: dict[str, Any]) -> list[str]:
    """Everything the Backend agent's contract loader and the OpenAPI 3.1 validator reject."""
    errs: list[str] = []
    try:
        validate_openapi(spec)
    except ValueError as e:
        errs.append(str(e))
    paths = spec.get("paths") or {}
    errs += [f"{p}: AgentForge control-plane route in the application contract" for p in paths
             if isinstance(p, str) and re.match(r"^/(api/)?(v1/)?runs\b", p)]
    try:
        c = load_contract(spec)
    except (ContractError, KeyError, TypeError, AttributeError) as e:
        return errs + [f"not a usable OpenAPI 3.x document: {e}"[:400]]
    errs += [e + " (item operations belong on '/api/<things>/{id}'; keep '/api/<things>' for the list and create)"
             if "not in path" in e else e for e in contract_problems(c)]
    errs += [f"{e.method} {e.path}: path must start with /api/" for e in c.endpoints if not e.path.startswith("/api/")]
    errs += [f"{e.method} {e.path}: register/login must not require a token (set security: [] on it)"
             for e in c.endpoints if e.auth and AUTH_BOOTSTRAP.match(e.path)]
    # Name the fix: models re-sent the same map-shaped schema when only told "no schema" (bench 2026-10-30).
    errs += [f"{e.method} {e.path}: the 200/201 application/json schema needs named `properties` (an object, or an "
             "array of objects); maps via additionalProperties are not supported, e.g. use an array of "
             "{category, total} instead of {<category>: total}"
             for e in c.endpoints if not e.response and e.method != "DELETE"]
    try:
        from openapi_spec_validator import validate as osv_validate  # same validator the Reviewer runs
    except ImportError:
        pass
    else:
        try:
            osv_validate(spec)
        except Exception as e:  # noqa: BLE001 - the validator raises several exception types
            errs.append(f"OpenAPI 3.1 validation: {str(e).splitlines()[0][:300]}")
    return errs


AUTH_BOOTSTRAP = re.compile(r"^/api/(auth/)?(register|login|signup|sign-up|token)/?$")


def normalize_contract(spec: dict[str, Any]) -> list[str]:
    """Deterministic repair before validation. A root-level `security` also applies to register/login, which would
    need a token to obtain a token (run_013eaee54836: the Backend agent rightly refused to build it). Those operations
    get an explicit `security: []` unless they set their own. Returns what was changed."""
    changed = []
    # An empty object body ({type: object} with no properties) means "no input": drop it (run_68259ab31790,
    # POST /api/sessions to start a game).
    for path, item in (spec.get("paths") or {}).items():
        for method, op in (item.items() if isinstance(item, dict) else []):
            if method.lower() not in ("post", "put", "patch") or not isinstance(op, dict):
                continue
            schema = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
            if (isinstance(schema, dict) and "$ref" not in schema and schema.get("type", "object") == "object"
                    and not schema.get("properties") and not any(k in schema for k in ("allOf", "oneOf", "anyOf"))):
                op.pop("requestBody")
                changed.append(f"{method.upper()} {path}: removed an empty request body (the action takes no input)")
    # Bearer security with nothing that issues a token can never be satisfied (run_ebc39e4b1766: POST
    # /api/leaderboard secured, no register/login, plan said "auth is omitted"; the backend failed 4x). Drop it.
    if _uses_security(spec) and not _issues_token(spec):
        spec.pop("security", None)
        for path, item in (spec.get("paths") or {}).items():
            for method, op in (item.items() if isinstance(item, dict) else []):
                if isinstance(op, dict) and op.pop("security", None):
                    changed.append(f"{method.upper()} {path}: made public (no endpoint issues a token to call it with)")
        return changed
    if not spec.get("security"):
        return changed
    for path, item in (spec.get("paths") or {}).items():
        if not (isinstance(item, dict) and isinstance(path, str) and AUTH_BOOTSTRAP.match(path)):
            continue
        for method, op in item.items():
            if method.lower() in ("get", "post", "put", "patch", "delete") and isinstance(op, dict) and "security" not in op:
                op["security"] = []
                changed.append(f"{method.upper()} {path}: made public (root security would have required a token to log in)")
    return changed


def _ops(spec: dict[str, Any]):
    for path, item in (spec.get("paths") or {}).items():
        for method, op in (item.items() if isinstance(item, dict) else []):
            if method.lower() in _METHODS and isinstance(op, dict):
                yield path, op


def _uses_security(spec: dict[str, Any]) -> bool:
    return bool(spec.get("security")) or any(op.get("security") for _, op in _ops(spec))


def _issues_token(spec: dict[str, Any]) -> bool:
    """A register/login path, or any operation whose response (one $ref level resolved) has a token field."""
    schemas = (spec.get("components") or {}).get("schemas") or {}
    for path, op in _ops(spec):
        if isinstance(path, str) and AUTH_BOOTSTRAP.match(path):
            return True
        text = json.dumps(op.get("responses") or {})
        text += "".join(json.dumps(schemas.get(ref, "")) for ref in re.findall(r"#/components/schemas/([\w.-]+)", text))
        if re.search(r'"(access_)?token"\s*:', text):
            return True
    return False


_METHODS = ("get", "post", "put", "patch", "delete")


def move_misplaced_item_operations(spec: dict[str, Any]) -> list[tuple[str, str, str]]:
    """An operation that declares exactly one `in: path` parameter missing from its path (e.g. PUT /api/notes with
    `id`) is an item operation on the collection path: move it to `/api/notes/{id}` unless that slot is taken.
    run_dddb079dbaf4: Super repeated this 3x when only told "path parameter 'id' not in path".
    Returns (METHOD, old_path, new_path) per move."""
    moves: list[tuple[str, str, str]] = []
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return moves
    for path in list(paths):
        item = paths[path]
        if not isinstance(item, dict) or not isinstance(path, str):
            continue
        for method in list(item):
            op = item[method]
            if method.lower() not in _METHODS or not isinstance(op, dict):
                continue
            names = [p.get("name") for p in op.get("parameters") or [] if isinstance(p, dict) and p.get("in") == "path"]
            missing = [n for n in names if isinstance(n, str) and "{" + n + "}" not in path]
            if len(missing) != 1:
                continue
            new = f"{path.rstrip('/')}/{{{missing[0]}}}"
            target = paths.setdefault(new, {})
            if not isinstance(target, dict) or method in target:
                continue
            target[method] = item.pop(method)
            moves.append((method.upper(), path, new))
        if not any(m in _METHODS for m in item):
            paths.pop(path)
    return moves


def normalize_output(out) -> list[str]:
    """Deterministic repairs on a parsed Planner reply (contract + matching task endpoints). Returns what changed."""
    spec = out.openapi_spec
    fixes = normalize_contract(spec)
    moves = move_misplaced_item_operations(spec)
    renamed = {f"{m} {old}": f"{m} {new}" for m, old, new in moves}
    for t in out.plan.tasks:  # "GET /api/todos?completed=false" names GET /api/todos (run_d4acd81b4ce1)
        t.endpoints = [renamed.get(e.strip().split("?")[0].split("#")[0], e.strip().split("?")[0].split("#")[0])
                       for e in t.endpoints]
    fixes += [f"{m} {old}: moved to {new} (its path parameter was missing from the path)" for m, old, new in moves]
    return fixes


def contract_yaml_problems(text: str) -> list[str]:
    try:
        spec = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return [f"YAML does not parse: {e}"[:400]]
    if not isinstance(spec, dict):
        return ["document is not a YAML mapping"]
    return contract_problems_for(spec)


def contract_operations(spec: dict[str, Any]) -> set[str]:
    ops = set()
    for path, item in (spec.get("paths") or {}).items():
        if isinstance(item, dict):
            ops |= {f"{m.upper()} {path}" for m in item if m.lower() in ("get", "post", "put", "patch", "delete")}
    return ops


def plan_problems(plan: ProjectPlan, spec: dict[str, Any]) -> list[str]:
    """Plan-level rules on top of validate_plan: ownership, file scope, endpoint coverage, frontend independence."""
    errs: list[str] = []
    try:
        validate_plan(plan)
    except ValueError as e:
        return [str(e)]
    ops = contract_operations(spec)
    by_id = {t.task_id: t for t in plan.tasks}
    covered: dict[str, set[str]] = {"backend": set(), "frontend": set()}
    for t in plan.tasks:
        for f in t.files_allowed:
            if f.startswith("/") or ".." in f.split("/") or not f.startswith(OWNER_PREFIX[t.owner]):
                errs.append(f"{t.task_id}: file {f!r} is outside {OWNER_PREFIX[t.owner]}")
        for ep in t.endpoints:
            m = ENDPOINT_RE.match(ep.strip())
            if not m:
                errs.append(f"{t.task_id}: endpoint {ep!r} must look like 'GET /api/items'")
            elif f"{m.group(1)} {m.group(2)}" not in ops:
                errs.append(f"{t.task_id}: endpoint {ep!r} is not in the contract")
            else:
                covered[t.owner].add(f"{m.group(1)} {m.group(2)}")
        if t.owner == "frontend":
            errs += [f"{t.task_id}: frontend task depends on backend task {d}; the frontend builds against the "
                     "contract, so it can run in parallel" for d in t.dependencies if by_id[d].owner == "backend"]
    for side in ("backend", "frontend"):
        if not any(t.owner == side for t in plan.tasks):
            errs.append(f"the plan has no {side} task")
        elif missing := sorted(ops - covered[side]):
            errs.append(f"contract operations no {side} task lists in endpoints: {', '.join(missing)[:400]}")
    return errs


def parallel_groups(plan: ProjectPlan) -> list[list[str]]:
    """Topological waves: every task's prerequisites are in an earlier wave (requires an acyclic plan)."""
    deps = {t.task_id: set(t.dependencies) for t in plan.tasks}
    done: set[str] = set()
    waves: list[list[str]] = []
    while len(done) < len(deps):
        wave = [t for t, d in deps.items() if t not in done and d <= done]
        if not wave:
            raise ValueError("dependency cycle")
        waves.append(wave)
        done |= set(wave)
    return waves
