"""Conformance with the shared platform contract (contracts/agentforge-openapi.yaml).

Validates what the Backend agent and the model router actually emit against the contract's own
JSON schemas (OpenAPI 3.1 = JSON Schema 2020-12). If the contract or the code drifts, this fails.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from engine.agents.backend import BackendAgent, parse_output
from engine.agents.runner import LocalRunner
from engine.router import ModelRouter, RouterConfig, RouterStopped, Slot
from engine.schemas import CheckResult, ContractChangeRequest, TaskAssignment
from tests.fakes import FakeAPIError, FakeClient, ok
from tests.test_backend_agent import AUTH_HOLE, CONTRACT, GOOD, LIGHTNING

ROOT = Path(__file__).resolve().parents[1]
DOC = yaml.safe_load((ROOT / "contracts" / "agentforge-openapi.yaml").read_text(encoding="utf-8"))
EVENT_TYPES = set(DOC["components"]["schemas"]["RunEvent"]["properties"]["type"]["enum"])


def validate(name: str, instance) -> None:
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "components": DOC["components"],
              "$ref": f"#/components/schemas/{name}"}
    errors = sorted(Draft202012Validator(schema).iter_errors(json.loads(json.dumps(instance, default=str))),
                    key=lambda e: list(e.path))
    assert not errors, f"{name} violates the contract: " + "; ".join(
        f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:5])


def make_agent(*responses, events=None, **router_kw):
    cfg = RouterConfig.load("config/profiles/web-light.toml", env={})
    client = FakeClient()
    client.script(LIGHTNING, *responses)
    router = ModelRouter(cfg, client, run_id="run_abc123", on_event=(events.append if events is not None else None),
                         **router_kw)
    return BackendAgent(router, LocalRunner(), max_iterations=2), router


def task(**kw):
    kw.setdefault("contract_ref", str(CONTRACT))
    return TaskAssignment(stage_id="backend", agent="backend", instructions="Build the arcade backend.", **kw)


# ---- Backend agent outputs ----------------------------------------------------------------
@pytest.mark.parametrize("responses,status", [
    ((ok(GOOD),), "done"),
    ((ok(AUTH_HOLE), ok(AUTH_HOLE)), "failed"),
    ((ok("=== CONTRACT_CHANGE_REQUEST ===\nproblem: no game list\nproposed_change: add enum\n"
         "reason: cannot validate games\naffected_paths: /api/scores\n=== END ==="),), "contract_change_requested"),
    ((FakeAPIError(401, "bad key"),), "stopped"),
])
def test_build_result_matches_contract(tmp_path, responses, status):
    agent, _ = make_agent(*responses)
    r = asyncio.run(agent.run(task(), tmp_path))
    assert r.status == status
    validate("BuildResult", r.to_contract())
    for c in r.checks:
        validate("CheckResult", c.model_dump(mode="json"))
    if r.contract_change:
        validate("ContractChangeRequest", r.contract_change.model_dump(mode="json"))
        assert r.contract_change.affected_paths == ["/api/scores"] and r.contract_change.reason


def test_task_assignment_matches_contract():
    t = task(failed_attempts=1, reviewer_failures=[CheckResult(name="pytest", passed=False, log="1 failed")],
             budget_context={"remaining_usd": 0.4})
    validate("TaskAssignment", t.model_dump(mode="json"))
    assert t.reviewer_failures == ["pytest: 1 failed"]
    # and a TaskAssignment produced by the orchestrator from the contract parses as-is
    TaskAssignment.model_validate({"stage_id": "backend", "agent": "backend", "instructions": "x",
                                   "contract_ref": None, "reviewer_failures": ["integration: field 'score'"]})


def test_contract_change_request_defaults_reason():
    _, _, ccr = parse_output("=== CONTRACT_CHANGE_REQUEST ===\nproblem: p\nproposed_change: c\n=== END ===")
    validate("ContractChangeRequest", ccr.model_dump(mode="json"))
    assert isinstance(ccr, ContractChangeRequest) and ccr.reason


# ---- Model router events, usage, model status ---------------------------------------------
def test_every_router_event_is_a_contract_run_event(tmp_path):
    events: list[dict] = []
    agent, router = make_agent(ok(AUTH_HOLE), FakeAPIError(503), FakeAPIError(503), ok(GOOD), events=events)
    asyncio.run(agent.run(task(), tmp_path))
    assert {"route_decision", "model_call_started", "model_call_ok", "model_call_failed"} <= {e["type"] for e in events}
    for e in events:
        validate("RunEvent", e)
        assert e["type"] in EVENT_TYPES and e["run_id"] == "run_abc123" and e["stage_id"] == "backend"
        assert e["timestamp"].endswith("Z") and datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
    assert len({e["id"] for e in events}) == len(events)
    rd = next(e for e in events if e["type"] == "route_decision")["data"]
    assert {"model_id", "reason", "estimated_cost_usd"} <= set(rd)  # fields from the guide's example


def test_router_stop_events_use_contract_types():
    cfg = RouterConfig.load("config/profiles/web-light.toml", env={})
    for budget, client, expected in (({"max_tokens": 100}, FakeClient(), "budget_exhausted"),
                                     (None, FakeClient(default=FakeAPIError(401)), "error")):
        events: list[dict] = []
        r = ModelRouter(cfg, client).for_run("run_x", on_event=events.append, budget=budget)
        with pytest.raises(RouterStopped):
            asyncio.run(r.call("backend", [{"role": "user", "content": "x" * 4000}]))
        assert events[-1]["type"] == expected
        for e in events:
            validate("RunEvent", e)


def test_usage_summary_matches_contract_and_never_claims_confirmed_cost():
    cfg = RouterConfig.load("config/profiles/web-quality.toml", env={})
    r = ModelRouter(cfg, FakeClient(default=ok("x", 2000, 1000)), run_id="run_u")
    for role in ("planner", "backend", "orchestrator"):
        asyncio.run(r.call(role, [{"role": "user", "content": "hi"}],
                           task_type="summarize_log" if role == "orchestrator" else None))
    u = r.usage_summary()
    validate("UsageSummary", u)
    assert u["confirmed_cost_usd"] is None and u["total_tokens"] == 9000 and u["estimated_cost_usd"] > 0
    assert {m["model_id"] for m in u["by_model"]} == {cfg.slot(s).model_id for s in Slot}
    assert u["premium_cost_usd"] > 0  # planner ran on slot A


def test_usage_summary_unpriced_and_local():
    cfg = RouterConfig.load("config/profiles/spark-lightning.toml", env={})
    r = ModelRouter(cfg, FakeClient(), run_id="run_l")
    asyncio.run(r.call("backend", [{"role": "user", "content": "hi"}]))
    u = r.usage_summary()
    validate("UsageSummary", u)
    assert u["estimated_cost_usd"] == 0 and u["by_model"][0]["calls"] == 1


def test_model_statuses_match_contract_and_hide_secrets():
    cfg = RouterConfig.load("config/profiles/web-quality.toml", env={})
    r = ModelRouter(cfg, FakeClient())
    r.health.record_failure(Slot.A, __import__("engine.router").router.ErrorKind.MODEL_NOT_FOUND)
    asyncio.run(r.call("backend", [{"role": "user", "content": "hi"}]))
    statuses = r.model_statuses()
    for m in statuses:
        validate("ModelStatus", m)
    by = {m["slot"]: m for m in statuses}
    assert by["premium"]["health"] == "unavailable" and by["coding"]["health"] == "healthy"
    assert by["economy"]["health"] == "unknown" and by["coding"]["price_status"] == "verified"
    assert "key" not in json.dumps(statuses).lower() and "http" not in json.dumps(statuses)


def test_run_budget_from_contract_is_enforced_server_side():
    cfg = RouterConfig.load("config/profiles/web-light.toml", env={})
    base = ModelRouter(cfg, FakeClient(default=ok("x", 1000, 1000)))
    run = base.for_run("run_b", budget={"max_tokens": 5000, "max_cost_usd": 0.5, "max_premium_cost_usd": 0.1,
                                        "max_wall_time_seconds": 600, "max_retries_per_stage": 3})
    assert run.cfg.budget.run_token_limit == 5000 and run.cfg.budget.run_cost_limit_usd == 0.5
    assert base.cfg.budget.run_token_limit == 1_500_000  # other runs unaffected
    calls = 0
    with pytest.raises(RouterStopped):
        for _ in range(10):
            remaining = run.budget.tokens_remaining
            asyncio.run(run.call("orchestrator", [{"role": "user", "content": "hi"}], task_type="summarize_log"))
            calls += 1
            # the provider is told it may not write past the run's remaining tokens
            assert base.client.calls[-1]["max_tokens"] <= remaining
    assert calls < 10
