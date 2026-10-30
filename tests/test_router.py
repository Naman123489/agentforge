import asyncio
import json
import random

import pytest
from pydantic import BaseModel

from engine.router import ErrorKind, ModelRouter, RouterStopped, Slot
from tests.fakes import FakeAPIError, FakeClient, FakeClock, load_cfg, ok

MSGS = [{"role": "user", "content": "build the leaderboard endpoint " * 50}]


class Verdict(BaseModel):
    verdict: str
    required_fixes: list[str]


def make(client=None, **budget):
    cfg = load_cfg(**budget)
    clock = FakeClock()
    events: list[dict] = []
    r = ModelRouter(cfg, client or FakeClient(), run_id="t", on_event=events.append, sleep=clock.sleep, clock=clock)
    r.fake_clock = clock
    return r, cfg, events


def ids(cfg):
    return {s: cfg.slot(s).model_id for s in Slot}


def run(coro):
    return asyncio.run(coro)


def test_happy_path_records_usage_cost_and_events():
    r, cfg, events = make()
    res = run(r.call("backend", MSGS))
    assert res.slot is Slot.B and res.model_id == ids(cfg)[Slot.B] and res.attempts == 1
    assert res.cost_usd == pytest.approx(cfg.slot(Slot.B).list_price(1000, 500))
    s = r.stats()
    assert s["tokens_used"] == 1500 and s["per_slot"]["B"]["calls"] == 1
    assert [e["type"] for e in events] == ["route_decision", "model_call_started", "model_call_ok"]
    d = events[0]["data"]
    assert d["reasons"] and d["reason"] and d["model_id"] == ids(cfg)[Slot.B]  # every decision is explained
    assert d["estimated_cost_usd"] > 0


def test_structured_output_parsed_and_schema_sent():
    client = FakeClient(default=ok('<think>hmm</think>```json\n{"verdict":"approve","required_fixes":[]}\n```'))
    r, cfg, _ = make(client)
    res = run(r.call("reviewer", MSGS, schema=Verdict))
    assert res.parsed == Verdict(verdict="approve", required_fixes=[])
    req = client.calls[0]
    assert req["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in req["messages"][0]["content"]


def test_invalid_output_repaired_on_same_model():
    r, cfg, _ = make()
    r.client.script(ids(cfg)[Slot.A], ok("not json"), ok('{"verdict":"reject","required_fixes":["x"]}'))
    res = run(r.call("reviewer", MSGS, schema=Verdict))
    assert res.slot is Slot.A and res.attempts == 2
    assert "rejected" in r.client.calls[1]["messages"][-1]["content"]


def test_economy_model_that_cannot_produce_valid_output_is_promoted():
    r, cfg, _ = make()
    r.client.script(ids(cfg)[Slot.C], ok("nope"), ok("still nope"))
    r.client.script(ids(cfg)[Slot.B], ok('{"verdict":"approve","required_fixes":[]}'))
    res = run(r.call("orchestrator", MSGS, schema=Verdict, task_type="classify_error"))
    assert res.slot is Slot.B
    assert r.client.models_called == [ids(cfg)[Slot.C]] * 2 + [ids(cfg)[Slot.B]]


def test_rate_limited_model_switches_without_waiting():
    r, cfg, _ = make()
    r.client.script(ids(cfg)[Slot.B], FakeAPIError(429, retry_after=30))
    res = run(r.call("backend", MSGS))
    assert res.slot is Slot.A
    assert r.health.availability(Slot.B).reason == "rate_limited"


def test_transient_error_retries_same_model_once_then_switches():
    r, cfg, events = make()
    r.client.script(ids(cfg)[Slot.B], FakeAPIError(503), FakeAPIError(503))
    res = run(r.call("backend", MSGS))
    assert r.client.models_called == [ids(cfg)[Slot.B]] * 2 + [ids(cfg)[Slot.A]]
    assert res.slot is Slot.A


def test_wrong_model_id_disables_slot_for_later_calls():
    r, cfg, _ = make()
    r.client.script(ids(cfg)[Slot.A], FakeAPIError(404, "model not found"))
    assert run(r.call("planner", MSGS)).slot is Slot.B
    assert run(r.call("planner", MSGS)).slot is Slot.B
    assert r.client.models_called.count(ids(cfg)[Slot.A]) == 1


def test_context_length_error_moves_to_next_model():
    r, cfg, _ = make()
    r.client.script(ids(cfg)[Slot.C], FakeAPIError(400, "maximum context length exceeded"))
    assert run(r.call("orchestrator", MSGS, task_type="summarize_log")).slot is Slot.B


def test_auth_error_stops_immediately():
    r, cfg, events = make(FakeClient(default=FakeAPIError(401, "bad key")))
    with pytest.raises(RouterStopped) as e:
        run(r.call("backend", MSGS))
    assert e.value.reason == "auth_failed" and len(r.client.calls) == 1


def test_timeout_is_transient():
    r, cfg, _ = make()
    cfg.slot(Slot.B).timeout_s = 0.01

    async def slow(**req):
        r.client.calls.append(req)
        if req["model"] == ids(cfg)[Slot.B]:
            await asyncio.sleep(1)
        return ok()

    r.client.chat.completions.create = slow
    assert run(r.call("backend", MSGS)).slot is Slot.A
    assert r.client.models_called == [ids(cfg)[Slot.B]] * 2 + [ids(cfg)[Slot.A]]


def test_all_models_down_stops_with_bounded_calls():
    r, cfg, events = make(FakeClient(default=FakeAPIError(500)))
    with pytest.raises(RouterStopped) as e:
        run(r.call("backend", MSGS))
    assert len(r.client.calls) <= cfg.retry.max_attempts_per_call
    assert e.value.reason in ("max_attempts", "no_eligible_model")
    assert events[-1]["type"] == "error" and events[-1]["data"]["source"] == "model_router"


def test_circuit_breaker_skips_failing_model_across_calls():
    r, cfg, _ = make()
    r.client.script(ids(cfg)[Slot.B], *[FakeAPIError(500)] * 3)
    run(r.call("backend", MSGS))  # B, B (retry), then A
    run(r.call("backend", MSGS))  # B fails a 3rd time -> circuit opens
    before = len(r.client.calls)
    assert run(r.call("backend", MSGS)).slot is Slot.A
    assert r.client.models_called[before:] == [ids(cfg)[Slot.A]]


def test_all_rate_limited_waits_once_then_succeeds():
    r, cfg, _ = make()
    for s in Slot:
        r.health.record_failure(s, ErrorKind.RATE_LIMIT, retry_after_s=3)
    res = run(r.call("backend", MSGS))
    assert res.slot is Slot.B and r.fake_clock.slept[0] == pytest.approx(3)


def test_run_budget_stops_further_spending():
    r, cfg, events = make(run_token_limit=10_000)
    with pytest.raises(RouterStopped) as e:
        for _ in range(20):
            run(r.call("orchestrator", MSGS, task_type="summarize_log"))
    assert e.value.reason in ("run_budget_exhausted", "budget_exhausted")
    assert r.budget.tokens_used <= 10_000


def test_escalation_flow_mirrors_stage_retries():
    r, cfg, _ = make()
    slots = [run(r.call("backend", MSGS, task_type="debug", failed_attempts=n)).slot for n in range(3)]
    assert slots == [Slot.B, Slot.B, Slot.A]


def test_for_run_shares_health_but_not_budget():
    r, cfg, _ = make()
    r.client.script(ids(cfg)[Slot.A], FakeAPIError(404))
    run(r.call("planner", MSGS))
    r2 = r.for_run("other")
    assert r2.budget.tokens_used == 0
    assert run(r2.call("planner", MSGS)).slot is Slot.B  # A disabled is provider-wide


def test_unpriced_model_reports_unknown_cost_not_zero():
    from engine.router import RouterConfig
    cfg = RouterConfig.load(env={})
    cfg.slot(Slot.A).price_input_per_1m = cfg.slot(Slot.A).price_output_per_1m = None
    r = ModelRouter(cfg, FakeClient(), run_id="t")
    res = run(r.call("planner", MSGS))
    assert res.slot is Slot.A and res.cost_usd is None
    snap = r.stats()
    assert snap["unpriced_tokens"] == 1500 and snap["cost_is_complete"] is False


def test_s1_3_hundred_simulated_failures_no_crash():
    """Benchmark S1-3: router survives 100 simulated provider failures without crashing or looping."""
    rng = random.Random(7)
    errors = [lambda: FakeAPIError(500), lambda: FakeAPIError(429, retry_after=1), lambda: FakeAPIError(502),
              lambda: FakeAPIError(400, "context length exceeded"), lambda: ok("{bad json"), lambda: ok("")]
    r, cfg, events = make(run_token_limit=10_000_000)
    for model in ids(cfg).values():
        r.client.script(model, *[rng.choice(errors)() for _ in range(60)])
    outcomes = {"ok": 0, "stopped": 0}
    for i in range(120):
        try:
            schema = Verdict if i % 3 == 0 else None
            r.client.default = ok('{"verdict":"approve","required_fixes":[]}')
            run(r.call(rng.choice(["planner", "backend", "frontend", "reviewer", "orchestrator"]), MSGS,
                       schema=schema))
            outcomes["ok"] += 1
        except RouterStopped:
            outcomes["stopped"] += 1
        r.fake_clock.t += 30  # let cooldowns expire between calls
    failures_injected = sum(1 for e in events if e["type"] == "model_call_failed")
    assert failures_injected >= 100
    assert outcomes["ok"] + outcomes["stopped"] == 120
    assert set(c["model"] for c in r.client.calls) <= set(ids(cfg).values())  # never a 4th model
    json.dumps(events, default=str)  # events are serialisable for SSE


def test_short_rate_limit_waits_instead_of_upgrading_to_premium():
    r, cfg, events = make()
    r.client.script(ids(cfg)[Slot.B], FakeAPIError(429, retry_after=5))
    res = run(r.call("backend", MSGS))
    assert res.slot is Slot.B and r.fake_clock.slept == [pytest.approx(5)]
    assert ids(cfg)[Slot.A] not in r.client.models_called


def test_second_rate_limit_after_waiting_falls_back_instead_of_stopping():
    r, cfg, _ = make()
    r.client.script(ids(cfg)[Slot.B], FakeAPIError(429, retry_after=5), FakeAPIError(429, retry_after=5))
    res = run(r.call("backend", MSGS))
    assert res.slot is Slot.A and len(r.fake_clock.slept) == 1


def test_local_profile_sends_thinking_mode_and_costs_nothing():
    from engine.router import RouterConfig
    cfg = RouterConfig.load("config/profiles/spark-lightning.toml", env={})
    client = FakeClient()
    r = ModelRouter(cfg, client, run_id="spark")
    res = run(r.call("orchestrator", MSGS, task_type="summarize_log"))
    assert res.slot is Slot.C and res.cost_usd == 0.0
    assert client.calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert client.calls[0]["top_p"] == 0.95
    assert r.stats()["cost_is_complete"] is True


def test_local_profile_needs_no_api_key(monkeypatch):
    from engine.router import RouterConfig
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    cfg = RouterConfig.load("config/profiles/spark-mixed.toml", env={})
    r = ModelRouter(cfg)  # builds real AsyncOpenAI clients lazily, one per endpoint
    assert str(r._client_for(Slot.A).base_url).startswith("http://localhost:8000")
    assert str(r._client_for(Slot.C).base_url).startswith("http://localhost:8001")
    assert r._client_for(Slot.A) is r._client_for(Slot.B)


def test_max_tokens_is_capped_by_remaining_budget():
    r, cfg, _ = make(run_token_limit=6_000)
    r.budget.tokens_used = 4_000  # 2,000 left
    run(r.call("orchestrator", [{"role": "user", "content": "hi"}], task_type="summarize_log"))
    assert r.client.calls[-1]["max_tokens"] <= 2_000 - 1  # minus the prompt estimate


def test_max_tokens_is_capped_by_remaining_cost():
    r, cfg, _ = make(run_cost_limit_usd=0.001)
    run(r.call("orchestrator", [{"role": "user", "content": "hi"}], task_type="summarize_log"))
    c = cfg.slot(Slot.C)
    assert r.client.calls[-1]["max_tokens"] * c.price_output_per_1m / 1e6 <= 0.001


def test_provider_402_stops_immediately_without_fallback():
    """Live finding 2026-10-29: Token Factory answered 402 'You have exhausted your budget' to every model."""
    r, cfg, events = make(FakeClient(default=FakeAPIError(402, "Payment Required: You have exhausted your budget.")))
    with pytest.raises(RouterStopped) as e:
        run(r.call("planner", MSGS))
    assert e.value.reason == "provider_budget_exhausted"
    assert len(r.client.calls) == 1  # no retry, no fallback to other models on the same account
    assert events[-1]["type"] == "budget_exhausted"
