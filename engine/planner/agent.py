"""Planner / Architect: request -> validated ProjectPlan + OpenAPI 3.1 contract + types.ts + task assignments.

Merges ForgeAI ai1-planner (plan schema, validation, TypeScript, assignments) with AgentForge's contract rules
(server/planner.py), and calls models only through the shared ModelRouter (role "planner", slot A by policy).
Invalid output is re-asked with the exact validator findings; nothing unvalidated reaches the orchestrator.
"""

from __future__ import annotations

import json
import re

import yaml
from pydantic import ValidationError

from engine.router import ModelRouter

from .artifacts import create_task_assignments, schema_to_typescript
from .schemas import PlannerArtifacts, PlannerOutput
from .validate import contract_problems_for, normalize_output, parallel_groups, plan_problems

STACK = ["FastAPI", "SQLite", "React", "TypeScript", "Vite"]

SYSTEM = """\
[agent:planner] You are the Planner/Architect of AgentForge. A Backend agent implements your API contract exactly
(FastAPI + SQLite) and a Frontend agent (React + TypeScript) calls it through a client generated from it.
Reply with ONE JSON object and nothing else: {"plan": {...}, "openapi_spec": {...}}.

plan:
  project_name   short kebab-case name
  summary        one sentence
  requirements   every concrete feature the user asked for or clearly implied, one per item
  assumptions    choices you made where the request is unspecific
  open_questions real ambiguities a human should confirm (may be empty)
  technology_stack  ["FastAPI","SQLite","React","TypeScript","Vite"]
  tasks          list of {task_id, title, owner, description, endpoints, dependencies, acceptance_checks, files_allowed}
  risks          concrete technical risks
Task rules:
- owner is "backend" or "frontend". 2-5 tasks per owner, ids T1, T2, ...
- description says exactly what to build: tables/fields, endpoints, pages, components, states.
- endpoints: "METHOD /api/path" exactly as in the contract. Every contract operation appears in at least one
  backend task (implements it) and one frontend task (calls it).
- dependencies only on earlier tasks. Frontend tasks never depend on backend tasks: both sides build from the
  contract in parallel.
- acceptance_checks are testable: an HTTP call with the expected status, or a visible UI behaviour.
- files_allowed: backend tasks under backend/, frontend tasks under frontend/src/.
- Nothing is implemented or tested yet; never say otherwise.

openapi_spec (the APPLICATION's API, not AgentForge's /runs API):
- "openapi": "3.1.0", an info block with title and version, every path under /api/.
- Every POST/PUT/PATCH that takes input has requestBody -> content -> application/json -> schema with "required"
  fields. An action that takes no input (e.g. start a session) omits requestBody.
- Every operation has a 200 or 201 response with an application/json schema; arrays use type array + items.
- Use components.schemas with $ref for shared objects. Use enum, minimum, minLength and example where useful.
- Response objects have named properties: no additionalProperties maps (return an array of {key, value} objects).
- If users must log in: POST /api/register and POST /api/login returning {token, username}; protect endpoints with
  "security": [{"bearer": []}] and define components.securitySchemes.bearer (type http, scheme bearer).
- Keep it small: only the endpoints the features need. Describe 4xx behaviour in each summary.
"""

_THINK = re.compile(r"<think>.*?</think>", re.S)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


def parse_output(content: str) -> tuple[PlannerOutput | None, list[str]]:
    text = _FENCE.sub("", _THINK.sub("", content).strip()).strip()
    start = text.find("{")
    if start < 0:
        return None, ["reply contained no JSON object"]
    try:  # decode the first complete object; text after it (a second object, a note) is ignored
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as e:
        try:  # the two mistakes models make most: trailing commas and // or /* */ comments (run_aee2414d7dc9)
            data, _ = json.JSONDecoder().raw_decode(lenient_json(text[start:]))
        except json.JSONDecodeError:
            near = text[start:][max(0, e.pos - 80):e.pos + 40].replace("\n", " ")
            return None, [f"invalid JSON at line {e.lineno} col {e.colno}: {e.msg}. Near: ...{near}... "
                          "(no trailing commas, no comments, double-quoted keys)"[:400]]
    try:
        return PlannerOutput.model_validate(data), []
    except ValidationError as e:
        return None, [f"{'.'.join(map(str, er['loc']))}: {er['msg']}" for er in e.errors()[:12]]


def lenient_json(text: str) -> str:
    """Remove // and /* */ comments and trailing commas outside strings. Nothing else is changed."""
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        elif ch in "}]":
            j = len(out) - 1
            while j >= 0 and out[j] in " \t\r\n":
                j -= 1
            if j >= 0 and out[j] == ",":
                del out[j]
            out.append(ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def check_output(out: PlannerOutput) -> list[str]:
    return contract_problems_for(out.openapi_spec) + plan_problems(out.plan, out.openapi_spec)


class PlannerAgent:
    def __init__(self, router: ModelRouter, max_attempts: int = 3):
        self.router = router
        self.max_attempts = max_attempts
        self.fixes: list[str] = []  # deterministic contract repairs applied to the last reply

    async def plan(self, request: str, stage_id: str, existing_contract: str | None = None) -> PlannerArtifacts:
        user = f"User request:\n{request}"
        if existing_contract:
            user += ("\n\nThis is a FOLLOW-UP. Current contract (keep its endpoints unless the request changes them):\n"
                     f"{existing_contract}")
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
        problems: list[str] = []
        out: PlannerOutput | None = None
        models: list[str] = []
        tokens, cost = 0, 0.0
        attempt = 0
        for attempt in range(1, self.max_attempts + 1):
            # No schema, so no provider JSON mode: measured 2026-10-30 (5 requests x 2), JSON mode + the injected
            # schema dropped Nemotron 3 Super from 10/10 to 4/10 valid plans (empty openapi_spec paths); Ultra went
            # 9/10 -> 10/10. Plain mode + parse_output + validator re-asks is the better default for Super.
            res = await self.router.call("planner", messages, task_type="api_contract" if existing_contract else "plan",
                                         failed_attempts=attempt - 1, stage_id=stage_id)
            models.append(res.model_id)
            tokens += res.input_tokens + res.output_tokens
            cost += res.cost_usd or 0.0
            candidate, problems = parse_output(res.content)
            if candidate is not None:
                out = candidate
                self.fixes = normalize_output(candidate)
                problems = check_output(candidate)
            if not problems:
                break
            # Re-ask with the validator's findings only; the previous reply is kept as context (it is short JSON).
            messages = [*messages[:2], {"role": "assistant", "content": _THINK.sub("", res.content)[-12000:]},
                        {"role": "user", "content": "The validator rejected your reply:\n- " + "\n- ".join(problems[:15])
                         + "\nReturn the corrected, complete JSON object."}]
        if out is None:
            raise PlannerFailed(problems, attempt)
        out.plan.technology_stack = out.plan.technology_stack or STACK
        try:
            groups = parallel_groups(out.plan)
        except ValueError:
            groups = []
        return PlannerArtifacts(
            plan=out.plan, contract_yaml=yaml.safe_dump(out.openapi_spec, sort_keys=False, allow_unicode=True),
            types_ts=schema_to_typescript(out.openapi_spec), task_assignments=create_task_assignments(out.plan),
            parallel_groups=groups, problems=problems, attempts=attempt, models_used=sorted(set(models)),
            tokens=tokens, cost_usd=round(cost, 6))


class PlannerFailed(Exception):
    def __init__(self, problems: list[str], attempts: int):
        super().__init__(f"Planner produced no parseable plan after {attempts} attempts: {'; '.join(problems)[:300]}")
        self.problems = problems


def write_artifacts(root, a: PlannerArtifacts, contract_path: str = "contract/openapi.yaml") -> None:
    """plan.json, types.ts and task_assignments.json at the workspace root (ai1-planner layout); the contract as YAML."""
    (root / contract_path).parent.mkdir(parents=True, exist_ok=True)
    (root / contract_path).write_text(a.contract_yaml, encoding="utf-8")
    (root / "plan.json").write_text(json.dumps({**a.plan.model_dump(), "parallel_groups": a.parallel_groups},
                                               indent=2, ensure_ascii=False), encoding="utf-8")
    (root / "types.ts").write_text(a.types_ts, encoding="utf-8")
    (root / "task_assignments.json").write_text(json.dumps(a.task_assignments, indent=2, ensure_ascii=False),
                                                encoding="utf-8")


def tasks_brief(a: PlannerArtifacts, owner: str) -> str:
    """The plan's tasks for one builder, as instructions (no model call)."""
    lines = []
    for t in a.plan.tasks:
        if t.owner != owner:
            continue
        lines.append(f"- {t.task_id} {t.title}: {t.description}")
        if t.endpoints:
            lines.append(f"  endpoints: {', '.join(t.endpoints)}")
        lines.append("  acceptance: " + "; ".join(t.acceptance_checks))
    return "\n".join(lines)
