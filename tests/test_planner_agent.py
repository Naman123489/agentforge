"""Planner (engine/planner): generation, validation, cycles, invalid OpenAPI, malformed replies, router integration,
fallback and timeout. Offline: the model is a scripted FakeClient behind the real ModelRouter."""

import asyncio
import json
from pathlib import Path

import pytest
import yaml

from engine.agents.contract import load_contract
from engine.planner import PlannerAgent, PlannerFailed, tasks_brief, write_artifacts
from engine.planner.schemas import ProjectPlan
from engine.planner.validate import contract_problems_for, parallel_groups, plan_problems, validate_plan
from engine.router import ModelRouter, Slot
from tests.fakes import FakeAPIError, FakeClient, FakeClock, load_cfg, ok, planner_reply

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_YAML = (ROOT / "examples" / "arcade_openapi.yaml").read_text(encoding="utf-8")
GOOD = planner_reply(CONTRACT_YAML)


def make(client: FakeClient):
    cfg = load_cfg()
    clock = FakeClock()
    events: list[dict] = []
    return ModelRouter(cfg, client, run_id="t", on_event=events.append, sleep=clock.sleep, clock=clock), cfg, events


def run(coro):
    return asyncio.run(coro)


def reply(**changes) -> str:
    d = json.loads(GOOD)
    for path, value in changes.items():
        obj = d
        *head, last = path.split("__")
        for k in head:
            obj = obj[int(k)] if k.isdigit() else obj[k]
        obj[int(last) if last.isdigit() else last] = value
    return json.dumps(d)


def test_generates_validated_artifacts_on_the_premium_slot(tmp_path):
    r, cfg, events = make(FakeClient(default=ok(GOOD)))
    a = run(PlannerAgent(r).plan("Build an arcade", "s1"))
    assert a.problems == [] and a.attempts == 1
    assert a.models_used == [cfg.slot(Slot.A).model_id]  # planning is routed to slot A by policy
    assert [e for e in events if e["type"] == "route_decision"][0]["data"]["task"]["agent"] == "planner"
    assert a.parallel_groups == [["T1", "T2"]]  # backend and frontend build in parallel
    assert a.task_assignments[0] == {**a.task_assignments[0], "stage_id": "T1", "agent": "backend",
                                     "contract_ref": "contract/openapi.yaml"}
    assert "export interface" in a.types_ts
    write_artifacts(tmp_path, a)
    assert load_contract(tmp_path / "contract/openapi.yaml").endpoints  # the Backend agent's loader accepts it
    assert json.loads((tmp_path / "plan.json").read_text())["parallel_groups"] == [["T1", "T2"]]
    assert {"plan.json", "types.ts", "task_assignments.json"} <= {p.name for p in tmp_path.iterdir()}
    assert "POST /api/register" in tasks_brief(a, "backend") and "T2" not in tasks_brief(a, "backend")


def test_dependency_cycles_and_unknown_dependencies_are_rejected():
    plan = ProjectPlan.model_validate(json.loads(reply(plan__tasks__0__dependencies=["T2"],
                                                       plan__tasks__1__dependencies=["T1"]))["plan"])
    with pytest.raises(ValueError, match="cycle"):
        validate_plan(plan)
    with pytest.raises(ValueError):
        parallel_groups(plan)
    bad = ProjectPlan.model_validate(json.loads(reply(plan__tasks__0__dependencies=["T9"]))["plan"])
    assert plan_problems(bad, yaml.safe_load(CONTRACT_YAML)) == ["T1 references unknown dependency T9."]


def test_plan_rules_ownership_scope_coverage_and_parallelism():
    spec = yaml.safe_load(CONTRACT_YAML)
    d = json.loads(GOOD)["plan"]
    d["tasks"][0]["files_allowed"] = ["frontend/src/x.ts", "../etc/passwd"]
    d["tasks"][1]["dependencies"] = ["T1"]          # frontend waiting on backend
    d["tasks"][1]["endpoints"] = ["GET /api/nope"]   # not in the contract, and coverage lost
    errs = plan_problems(ProjectPlan.model_validate(d), spec)
    assert any("outside backend/" in e for e in errs)
    assert any("frontend task depends on backend task T1" in e for e in errs)
    assert any("'GET /api/nope' is not in the contract" in e for e in errs)
    assert any(e.startswith("contract operations no frontend task") for e in errs)


def test_invalid_openapi_is_reported():
    spec = yaml.safe_load(CONTRACT_YAML)
    assert contract_problems_for(spec) == []
    assert any("3.1.0" in e for e in contract_problems_for({**spec, "openapi": "3.0.0"}))
    assert any("control-plane" in e for e in contract_problems_for({**spec, "paths": {**spec["paths"], "/api/v1/runs": spec["paths"][next(iter(spec["paths"]))]}}))
    assert contract_problems_for({"openapi": "3.1.0", "info": {"title": "x", "version": "1"}, "paths": {}})


def test_malformed_then_invalid_replies_are_re_asked_with_validator_findings():
    client = FakeClient(default=ok(GOOD))
    r, cfg, _ = make(client)
    client.script(cfg.slot(Slot.A).model_id, ok("Sure! Here is the plan in prose."),
                  ok(reply(plan__tasks__1__dependencies=["T1"])))
    a = run(PlannerAgent(r).plan("Build an arcade", "s1"))
    # Each problem is re-asked with the exact finding: first the unparseable prose, then the plan-rule violation.
    assert a.problems == [] and a.attempts == 3
    assert not any("response_format" in c for c in client.calls)  # plain mode (JSON mode hurt Super, see agent.py)
    second, third = client.calls[1]["messages"][-1]["content"], client.calls[2]["messages"][-1]["content"]
    assert "no JSON object" in second and "frontend task depends on backend task T1" in third


def test_gives_up_after_max_attempts():
    r, _, _ = make(FakeClient(default=ok("not json")))
    with pytest.raises(PlannerFailed, match="3 attempts"):
        run(PlannerAgent(r).plan("x", "s1"))
    r, _, _ = make(FakeClient(default=ok(reply(openapi_spec__openapi="3.0.0"))))
    a = run(PlannerAgent(r).plan("x", "s1"))  # parseable but invalid: returned with problems, never as valid
    assert a.problems and a.attempts == 3


def test_router_fallback_and_timeout_reach_the_planner():
    client = FakeClient(default=ok(GOOD))
    r, cfg, _ = make(client)
    client.script(cfg.slot(Slot.A).model_id, *[FakeAPIError(500)] * 3)
    a = run(PlannerAgent(r).plan("Build an arcade", "s1"))
    assert a.problems == [] and a.models_used == [cfg.slot(Slot.B).model_id]  # A failed: the router fell back

    r, cfg, _ = make(FakeClient(default=ok(GOOD)))
    cfg.slot(Slot.A).timeout_s = 0.01

    async def slow(**req):
        r.client.calls.append(req)
        if req["model"] == cfg.slot(Slot.A).model_id:
            await asyncio.sleep(1)
        return ok(GOOD)

    r.client.chat.completions.create = slow
    assert run(PlannerAgent(r).plan("Build an arcade", "s1")).models_used == [cfg.slot(Slot.B).model_id]


def test_trailing_text_after_the_json_object_is_ignored():
    from engine.planner.agent import parse_output
    out, errs = parse_output(GOOD + '\n{"note": "second object"} and some prose')
    assert errs == [] and out is not None
    assert parse_output('{"plan": ')[1][0].startswith("invalid JSON")


def test_map_shaped_response_schema_gets_an_actionable_message():
    spec = yaml.safe_load(CONTRACT_YAML)
    path, item = next((p, i) for p, i in spec["paths"].items() if "get" in i)
    item["get"]["responses"] = {"200": {"description": "ok", "content": {"application/json": {
        "schema": {"type": "object", "additionalProperties": {"type": "number"}}}}}}
    assert any("additionalProperties" in e and "array of" in e for e in contract_problems_for(spec))


def test_root_security_never_locks_register_and_login():
    from engine.planner.validate import normalize_contract
    spec = yaml.safe_load(CONTRACT_YAML)
    spec["security"] = [{"bearer": []}]
    for item in spec["paths"].values():
        for op in item.values():
            if isinstance(op, dict):
                op.pop("security", None)
    assert any("/api/register" in e and "must not require a token" in e for e in contract_problems_for(spec))
    fixed = normalize_contract(spec)
    assert any("POST /api/register" in f for f in fixed) and any("POST /api/login" in f for f in fixed)
    assert spec["paths"]["/api/register"]["post"]["security"] == []
    assert contract_problems_for(spec) == []
    assert load_contract(spec).endpoints[0].auth is False or not any(
        e.auth for e in load_contract(spec).endpoints if e.path in ("/api/register", "/api/login"))


def test_planner_repairs_root_security_without_another_model_call():
    d = json.loads(GOOD)
    d["openapi_spec"]["security"] = [{"bearer": []}]
    for item in d["openapi_spec"]["paths"].values():
        for op in item.values():
            if isinstance(op, dict):
                op.pop("security", None)
    client = FakeClient(default=ok(json.dumps(d)))
    r, _, _ = make(client)
    agent = PlannerAgent(r)
    a = run(agent.plan("Build an arcade", "s1"))
    assert a.problems == [] and a.attempts == 1 and len(client.calls) == 1 and agent.fixes
    assert "security: []" in a.contract_yaml or "security: []\n" in a.contract_yaml


def test_item_operations_on_the_collection_path_are_moved_with_their_task_endpoints():
    """run_dddb079dbaf4: PUT/DELETE/GET-one declared on /api/scores with an `id` path parameter."""
    from engine.planner.schemas import PlannerOutput
    from engine.planner.validate import normalize_output
    d = json.loads(GOOD)
    paths = d["openapi_spec"]["paths"]
    coll = next(p for p, item in paths.items() if "post" in item and "{" not in p and "register" not in p and "login" not in p)
    item_op = {"summary": "Delete one. 404 if missing.", "parameters": [{"name": "id", "in": "path", "required": True,
               "schema": {"type": "integer"}}], "responses": {"200": {"description": "ok", "content": {
               "application/json": {"schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}}}}}}}
    paths[coll]["delete"] = item_op
    for t in d["plan"]["tasks"]:
        t["endpoints"].append(f"DELETE {coll}")
    out = PlannerOutput.model_validate(d)
    assert any("not in path" in e and "/{id}" in e for e in contract_problems_for(out.openapi_spec))
    fixes = normalize_output(out)
    assert fixes and f"{coll}/{{id}}" in out.openapi_spec["paths"] and "delete" not in out.openapi_spec["paths"][coll]
    assert all(f"DELETE {coll}/{{id}}" in t.endpoints for t in out.plan.tasks)
    assert contract_problems_for(out.openapi_spec) == [] and plan_problems(out.plan, out.openapi_spec) == []


def test_body_less_action_post_is_valid_but_a_declared_empty_body_is_not():
    """run_68259ab31790: POST /api/sessions (start a game) has no input; it was rejected 3x."""
    spec = yaml.safe_load(CONTRACT_YAML)
    ok_resp = {"200": {"description": "ok", "content": {"application/json": {"schema": {
        "type": "object", "properties": {"session_id": {"type": "integer"}}}}}}}
    spec["paths"]["/api/sessions"] = {"post": {"summary": "Start a session. 401 without token.", "responses": ok_resp}}
    assert contract_problems_for(spec) == []
    spec["paths"]["/api/sessions"]["post"]["requestBody"] = {"content": {"application/json": {"schema": {"type": "object"}}}}
    assert any("requestBody must be an application/json object schema" in e for e in contract_problems_for(spec))


def test_task_endpoints_with_query_strings_match_their_operation():
    """run_d4acd81b4ce1: 'GET /api/todos?completed=false' was reported as not in the contract."""
    from engine.planner.schemas import PlannerOutput
    from engine.planner.validate import normalize_output
    d = json.loads(GOOD)
    get_op = next(e for e in d["plan"]["tasks"][1]["endpoints"] if e.startswith("GET "))
    d["plan"]["tasks"][1]["endpoints"].append(get_op + "?completed=false&limit=10")
    out = PlannerOutput.model_validate(d)
    assert plan_problems(out.plan, out.openapi_spec) == []
    normalize_output(out)
    assert all("?" not in e for t in out.plan.tasks for e in t.endpoints)


def test_an_empty_declared_body_is_removed_as_no_input():
    from engine.planner.validate import normalize_contract
    spec = yaml.safe_load(CONTRACT_YAML)
    ok_resp = {"200": {"description": "ok", "content": {"application/json": {"schema": {
        "type": "object", "properties": {"session_id": {"type": "integer"}}}}}}}
    spec["paths"]["/api/sessions"] = {"post": {"summary": "Start. 401 without token.", "responses": ok_resp,
        "requestBody": {"required": False, "content": {"application/json": {"schema": {"type": "object", "additionalProperties": False}}}}}}
    assert contract_problems_for(spec)
    assert any("removed an empty request body" in f for f in normalize_contract(spec))
    assert contract_problems_for(spec) == []


def test_trailing_commas_and_comments_are_repaired_and_strings_are_untouched():
    """run_aee2414d7dc9: Super returned JSON with a trailing comma 3x; the plan was otherwise valid."""
    from engine.planner.agent import lenient_json, parse_output
    d = json.loads(GOOD)
    d["plan"]["summary"] = 'Keeps "// not a comment", and ",}" inside strings'
    text = json.dumps(d, indent=1)
    broken = text.replace('"summary"', '// the summary\n  "summary"', 1).replace("]\n }", "],\n }", 1)
    assert json.loads(lenient_json(broken))["plan"]["summary"] == d["plan"]["summary"]
    out, errs = parse_output(broken)
    assert errs == [] and out is not None
    out, errs = parse_output('{"plan": {"a": 1 "b": 2}}')
    assert out is None and "Near:" in errs[0] and "line 1" in errs[0]


def test_security_without_a_token_source_is_dropped():
    from engine.planner.validate import normalize_contract
    spec = {"paths": {"/api/leaderboard": {"get": {"responses": {"200": {}}},
                                           "post": {"security": [{"bearer": []}], "responses": {"201": {}}}}},
            "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}}}
    fixes = normalize_contract(spec)
    assert "security" not in spec["paths"]["/api/leaderboard"]["post"]
    assert any("no endpoint issues a token" in f for f in fixes)


def test_security_kept_when_an_endpoint_returns_a_token():
    from engine.planner.validate import normalize_contract
    spec = {"security": [{"bearer": []}],
            "paths": {"/api/sessions": {"post": {"security": [], "responses": {"200": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/Session"}}}}}}},
                      "/api/notes": {"get": {"responses": {"200": {}}}}},
            "components": {"schemas": {"Session": {"type": "object", "properties": {"token": {"type": "string"}}}}}}
    normalize_contract(spec)
    assert spec["security"] == [{"bearer": []}]
