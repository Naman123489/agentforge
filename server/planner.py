"""LEGACY contract-only Planner, kept as the benchmark baseline (scripts/bench_planner_reviewer.py).

The pipeline now uses engine.planner (ForgeAI ai1-planner integrated). Original description:
Minimal Planner: turns the user's request into the per-run generated-app contract (OpenAPI 3.1 YAML).

The full Planner/Architect agent belongs to a teammate; this one exists so the Control Plane API can run
end to end. Output is validated by the same loader the Backend agent uses; invalid contracts are re-asked.
"""

from __future__ import annotations

import re

import yaml

from engine.agents.contract import ContractError, contract_problems, load_contract
from engine.router import ModelRouter

SYSTEM = """\
[agent:planner] You are the Planner/Architect of AgentForge. Write the API contract that a Backend agent will
implement exactly (FastAPI + SQLite) and a Frontend agent will call. Output ONE OpenAPI 3.1.0 document in YAML.

Rules:
- `openapi: 3.1.0`, an `info` block, and every path under /api/.
- Every POST/PUT/PATCH has requestBody -> content -> application/json -> schema with `required` fields.
- Every operation has a 200 or 201 response with an application/json schema; arrays use type: array + items.
- Use components/schemas with $ref for shared objects. Use enum, minimum, minLength and example where useful.
- If users must log in: POST /api/register and POST /api/login returning {token, username}; protect endpoints with
  `security: [{bearer: []}]` and define components.securitySchemes.bearer (type: http, scheme: bearer).
- Keep it small: only the endpoints the requested features need. Describe 4xx behaviour in each summary.
Reply with only the YAML in a ```yaml fenced block. No prose.
"""

FENCE = re.compile(r"```(?:ya?ml)?\s*\n(.*?)```", re.S)


def extract_yaml(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    m = FENCE.search(text)
    return (m.group(1) if m else text).strip() + "\n"


def validate(text: str) -> list[str]:
    try:
        spec = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return [f"YAML does not parse: {e}"[:400]]
    if not isinstance(spec, dict):
        return ["document is not a YAML mapping"]
    try:
        c = load_contract(spec)
    except (ContractError, KeyError, TypeError, AttributeError) as e:
        return [f"not a usable OpenAPI 3.x document: {e}"[:400]]
    errs = contract_problems(c)
    errs += [f"{e.method} {e.path}: path must start with /api/" for e in c.endpoints if not e.path.startswith("/api/")]
    errs += [f"{e.method} {e.path}: no JSON success response schema" for e in c.endpoints if not e.response and e.method != "DELETE"]
    return errs


async def plan_contract(router: ModelRouter, request: str, stage_id: str, existing: str | None = None,
                        attempts: int = 2) -> tuple[str, list[str]]:
    """Returns (yaml_text, problems). problems == [] means the contract validated."""
    problems: list[str] = []
    text = ""
    for i in range(attempts):
        user = f"User request:\n{request}"
        if existing:
            user += f"\n\nThis is a FOLLOW-UP. Current contract (keep existing endpoints unless the request changes them):\n```yaml\n{existing}```"
        if problems:
            user += "\n\nYour previous contract was rejected by the validator:\n- " + "\n- ".join(problems) + \
                    f"\n\nPrevious YAML:\n```yaml\n{text}```\nReturn the corrected full document."
        res = await router.call("planner", [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                                task_type="api_contract" if existing else "plan", failed_attempts=i, stage_id=stage_id)
        text = extract_yaml(res.content)
        problems = validate(text)
        if not problems:
            break
    return text, problems
