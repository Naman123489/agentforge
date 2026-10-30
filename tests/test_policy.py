import pytest
from pydantic import ValidationError

from engine.router import AccountLedger, Action, HealthTracker, RouterConfig, RunBudget, Slot, TaskProfile, decide
from engine.router.config import SlotConfig
from engine.router.health import ErrorKind
from tests.fakes import FakeClock, load_cfg


@pytest.fixture
def env():
    cfg = load_cfg()
    clock = FakeClock()
    return cfg, HealthTracker(cfg.health, clock), RunBudget(cfg), AccountLedger(cfg), clock


def route(env, role, task_type=None, excluded=None, **kw):
    cfg, health, run, account, _ = env
    kw.setdefault("input_tokens_est", 2000)
    return decide(TaskProfile.for_task(role, task_type, **kw), cfg, health, run, account, excluded)


def test_task_type_defaults(env):
    assert route(env, "orchestrator", "summarize_log").slot is Slot.C
    assert route(env, "reviewer", "classify_error").slot is Slot.C
    assert route(env, "backend", "code_backend").slot is Slot.B
    assert route(env, "frontend").slot is Slot.B
    assert route(env, "planner").slot is Slot.A
    assert route(env, "planner", "api_contract").slot is Slot.A


def test_economy_tasks_never_fall_back_to_premium(env):
    d = route(env, "orchestrator", "summarize_log")
    assert Slot.A not in d.candidates


def test_economy_promoted_after_failure(env):
    d = route(env, "orchestrator", "summarize_log", failed_attempts=1)
    assert d.slot is Slot.B and Slot.C not in d.candidates


def test_escalate_to_premium_after_repeated_failures(env):
    assert route(env, "backend", "debug", failed_attempts=1).slot is Slot.B
    d = route(env, "backend", "debug", failed_attempts=2)
    assert d.slot is Slot.A
    assert any("escalate" in r for r in d.reasons)


def test_per_agent_escalation_threshold(env):
    env[0].policy.escalate_to_premium_after_failures_by_agent = {"frontend": 3}
    assert route(env, "frontend", "test_repair", failed_attempts=2).slot is Slot.B  # one more B repair
    assert route(env, "frontend", "test_repair", failed_attempts=3).slot is Slot.A
    assert route(env, "backend", "debug", failed_attempts=2).slot is Slot.A  # other agents keep the default


def test_quality_profile_gives_frontend_one_more_b_repair():
    cfg = RouterConfig.load("config/profiles/web-quality.toml", env={})
    assert cfg.policy.escalate_to_premium_after_failures_by_agent == {"frontend": 3}
    assert cfg.policy.escalate_to_premium_after_failures == 2
    b = cfg.slot(Slot.B)
    assert b.model_id == "reflection/beam" and b.extra_body == {}  # `extra_body = {}` cleared Nemotron's kwargs


def test_high_risk_tasks_never_use_economy(env):
    d = route(env, "reviewer", "integration")
    assert Slot.C not in d.candidates and "floor" in d.rejected[Slot.C]


def test_premium_cap_downgrades_planning_to_b(env):
    _, _, run, _, _ = env
    run.premium_tokens = run.cfg.budget.premium_token_limit  # premium spent
    d = route(env, "planner")
    assert d.slot is Slot.B
    assert any("premium unavailable" in r for r in d.reasons)


def test_premium_reserve_kept_for_critical_tasks(env):
    cfg, _, run, _, _ = env
    run.premium_tokens = int(cfg.budget.premium_token_limit * 0.78)  # 22% left, reserve is 25%
    assert route(env, "reviewer", "complex_debug").slot is Slot.B  # high, not critical
    assert route(env, "planner").slot is Slot.A                     # critical keeps premium


def test_low_budget_downgrades_low_risk_work(env):
    cfg, _, run, _, _ = env
    run.tokens_used = int(cfg.budget.run_token_limit * 0.9)
    d = route(env, "backend", "code_backend", complexity="low", risk="low")
    assert d.low_budget_mode and d.slot is Slot.C
    assert route(env, "planner").slot is Slot.A  # critical work still gets premium
    assert route(env, "backend", "code_backend").slot is Slot.B  # medium risk stays on B


def test_low_account_credits_trigger_low_budget_mode():
    cfg = load_cfg()
    cfg.account.credit_balance_usd = 10.0
    account = AccountLedger(cfg)
    account.credit_spent_usd = 8.0  # 20% left < 30%
    d = decide(TaskProfile.for_task("reviewer", "complex_debug", input_tokens_est=2000), cfg,
               HealthTracker(cfg.health), RunBudget(cfg), account)
    assert d.low_budget_mode and d.slot is Slot.B


def test_unhealthy_premium_falls_back_to_strongest_available(env):
    _, health, *_ = env
    for _ in range(3):
        health.record_failure(Slot.A, ErrorKind.SERVER_ERROR)
    d = route(env, "planner")
    assert d.slot is Slot.B and d.rejected[Slot.A] == "circuit_open"


def test_half_open_probe_after_cooldown(env):
    cfg, health, _, _, clock = env
    for _ in range(3):
        health.record_failure(Slot.A, ErrorKind.SERVER_ERROR)
    clock.t += cfg.health.cooldown_s + 1
    assert route(env, "planner").slot is Slot.A
    health.record_failure(Slot.A, ErrorKind.SERVER_ERROR)  # probe fails -> reopen immediately
    assert route(env, "planner").slot is Slot.B


def test_short_rate_limit_waits_long_outage_stops(env):
    _, health, *_ = env
    for s in Slot:
        health.record_failure(s, ErrorKind.RATE_LIMIT, retry_after_s=5)
    d = route(env, "backend")
    assert d.action is Action.WAIT and d.wait_s == pytest.approx(5)
    for s in Slot:
        health.record_failure(s, ErrorKind.RATE_LIMIT, retry_after_s=600)
    d = route(env, "backend")
    assert d.action is Action.STOP and d.stop_reason == "no_eligible_model"


def test_disabled_and_missing_models(env):
    cfg, health, *_ = env
    cfg.slot(Slot.A).enabled = False
    health.record_failure(Slot.B, ErrorKind.MODEL_NOT_FOUND)
    d = route(env, "planner")
    assert d.action is Action.STOP  # C is below the planner's capability floor
    assert d.rejected[Slot.A] == "disabled in config"


def test_run_budget_exhausted_stops(env):
    cfg, _, run, _, _ = env
    run.tokens_used = cfg.budget.run_token_limit
    d = route(env, "orchestrator", "summarize_log")
    assert d.action is Action.STOP and d.stop_reason == "run_budget_exhausted"


def test_budget_too_small_for_any_model_stops_with_budget_reason(env):
    cfg, _, run, _, _ = env
    run.tokens_used = cfg.budget.run_token_limit - 3000
    d = route(env, "backend", "code_backend")  # needs ~6000 tokens
    assert d.action is Action.STOP and d.stop_reason == "budget_exhausted"


def test_context_too_large_promotes(env):
    cfg, *_ = env
    cfg.slot(Slot.C).context_window = 8000
    d = route(env, "orchestrator", "summarize_log", input_tokens_est=20_000)
    assert d.slot is Slot.B and "context" in d.rejected[Slot.C]


def test_excluded_slot_is_skipped(env):
    d = route(env, "backend", "code_backend", excluded={Slot.B: "server_error"})
    assert d.slot is Slot.A  # strongest affordable alternative


def test_expected_cost_premium_wins_when_failure_is_costly(env):
    d = route(env, "reviewer", "complex_debug")
    assert d.slot is Slot.A
    assert d.estimates[Slot.A].expected_cost_usd < d.estimates[Slot.B].expected_cost_usd


def test_expected_cost_avoids_premium_for_easy_low_risk_work(env):
    d = route(env, "reviewer", "complex_debug", complexity="low", risk="low")
    assert d.slot is Slot.B
    assert any("expected cost" in r for r in d.reasons)


def test_unknown_premium_price_falls_back_to_rules():
    cfg = RouterConfig.load(env={})
    cfg.slot(Slot.A).price_input_per_1m = cfg.slot(Slot.A).price_output_per_1m = None
    d = decide(TaskProfile.for_task("reviewer", "complex_debug", input_tokens_est=2000), cfg,
               HealthTracker(cfg.health), RunBudget(cfg), AccountLedger(cfg))
    assert d.slot is Slot.A and d.estimates[Slot.A].est_cost_usd is None


def test_free_allowance_requires_explicit_size():
    with pytest.raises(ValidationError):
        SlotConfig(model_id="x", billing="free_allowance")


def test_free_allowance_is_zero_cost_only_within_allowance():
    cfg = load_cfg()
    c = cfg.slot(Slot.C)
    c.billing, c.free_allowance_tokens = "free_allowance", 10_000
    account = AccountLedger(cfg)
    assert account.estimate(Slot.C, 4000, 1000).cost_usd == 0.0
    account.free_used[Slot.C] = 8000
    est = account.estimate(Slot.C, 4000, 1000)  # 2000 free, 3000 paid
    assert est.free_tokens == 2000 and est.cost_usd == pytest.approx(c.list_price(4000, 1000) * 0.6)
    c.price_input_per_1m = c.price_output_per_1m = None
    account.free_used[Slot.C] = 10_000
    d = decide(TaskProfile.for_task("orchestrator", "summarize_log", input_tokens_est=2000), cfg,
               HealthTracker(cfg.health), RunBudget(cfg), account)
    assert d.slot is Slot.B and "allowance" in d.rejected[Slot.C]


def test_exactly_three_slots_required():
    cfg = load_cfg()
    raw = cfg.model_dump()
    raw["slots"].pop(Slot.C)
    with pytest.raises(ValidationError):
        RouterConfig.model_validate(raw)


def test_env_overrides_swap_models_without_code_changes():
    cfg = RouterConfig.load(env={"AF_MODEL_B": "openai/gpt-oss-120b", "AF_MODEL_A_ENABLED": "false"})
    assert cfg.slot(Slot.B).model_id == "openai/gpt-oss-120b"
    assert not cfg.slot(Slot.A).enabled


@pytest.mark.parametrize("model", ["Qwen/Qwen3-Coder-480B", "deepseek-ai/DeepSeek-V3", "zai-org/GLM-4.5",
                                   "moonshotai/Kimi-K2-Instruct"])
def test_chinese_origin_models_rejected(model):
    with pytest.raises(ValidationError):
        RouterConfig.load(env={"AF_MODEL_C": model})


def test_critical_task_stops_rather_than_using_economy_model(env):
    cfg, health, *_ = env
    for s in (Slot.A, Slot.B):
        health.record_failure(s, ErrorKind.MODEL_NOT_FOUND)
    d = route(env, "planner", "api_contract")
    assert d.action is Action.STOP and "floor" in d.rejected[Slot.C]


# ---- local (DGX Spark) profiles -------------------------------------------------------
@pytest.mark.parametrize("profile", ["spark-lightning", "spark-super", "spark-mixed"])
def test_spark_profiles_are_local_and_free(profile):
    cfg = RouterConfig.load(f"config/profiles/{profile}.toml", env={"SPARK_HOST": "10.0.0.7"})
    for s in Slot:
        sc = cfg.slot(s)
        assert sc.billing == "local" and sc.list_price(100_000, 100_000) == 0.0
        assert cfg.endpoint(s)[0].startswith("http://10.0.0.7:")
    assert not cfg.provider.requires_api_key
    assert cfg.retry.max_attempts_per_call == 4  # inherited from models.toml


def test_spark_host_defaults_to_localhost():
    cfg = RouterConfig.load("config/profiles/spark-lightning.toml", env={})
    assert cfg.endpoint(Slot.A)[0] == "http://localhost:8001/v1"


def test_reasoning_mode_differs_per_slot_on_one_model():
    cfg = RouterConfig.load("config/profiles/spark-lightning.toml", env={})
    modes = {s: cfg.slot(s).extra_body["chat_template_kwargs"] for s in Slot}
    assert modes[Slot.A] == {"enable_thinking": True}
    assert modes[Slot.B] == {"enable_thinking": True, "low_effort": True}
    assert modes[Slot.C] == {"enable_thinking": False}


def test_local_routing_uses_rules_not_cost_math():
    cfg = RouterConfig.load("config/profiles/spark-lightning.toml", env={})
    run, account = RunBudget(cfg), AccountLedger(cfg)
    health = HealthTracker(cfg.health)
    route = lambda *a, **kw: decide(TaskProfile.for_task(*a, input_tokens_est=2000, **kw), cfg, health, run, account)
    assert route("backend").slot is Slot.B          # $0 everywhere must not push all coding to A
    assert route("planner").slot is Slot.A
    assert route("orchestrator", "summarize_log").slot is Slot.C
    assert not any("expected cost" in r for r in route("backend").reasons)


def test_account_spend_persists_across_restarts(tmp_path):
    cfg = load_cfg()
    cfg.account.credit_balance_usd = 0.80
    path = tmp_path / "ledger.json"
    a = AccountLedger(cfg, path)
    a.record(Slot.B, a.estimate(Slot.B, 1_000_000, 500_000))  # $0.30 + $0.45
    b = AccountLedger(cfg, path)  # "server restarted"
    assert b.credit_spent_usd == pytest.approx(0.75) and b.credit_low
    d = decide(TaskProfile.for_task("backend", input_tokens_est=200_000), cfg, HealthTracker(cfg.health),
               RunBudget(cfg), b)
    assert d.rejected.get(Slot.B) == "prepaid credits exhausted"


def test_web_profile_is_token_factory_and_capped():
    cfg = RouterConfig.load("config/profiles/web-light.toml", env={})
    assert {cfg.endpoint(s)[0] for s in Slot} == {"https://api.tokenfactory.nebius.com/v1"}
    assert all(cfg.slot(s).billing == "prepaid_credits" and cfg.slot(s).has_price for s in Slot)
    assert cfg.budget.run_cost_limit_usd <= 0.30


def test_decision_records_budget_and_health_inputs(env):
    _, health, *_ = env
    health.record_failure(Slot.A, ErrorKind.RATE_LIMIT, retry_after_s=30)
    ev = route(env, "backend").to_event()
    i = ev["inputs"]
    assert i["health"]["A"] == "rate_limited" and i["health"]["B"] == "ok"
    assert i["run_tokens_remaining"] > 0 and 0 <= i["premium_remaining_fraction"] <= 1
    assert i["required_context_tokens"] == 2000 + 4000


def test_routine_code_stays_on_b_even_when_premium_is_same_price():
    """Live finding 2026-10-29: A and B were the same model/price, so A looked like free extra quality."""
    cfg = RouterConfig.load("config/profiles/web-light.toml", env={})
    run, account, health = RunBudget(cfg), AccountLedger(cfg), HealthTracker(cfg.health)
    d = decide(TaskProfile.for_task("backend", "code_backend", complexity="low", input_tokens_est=60), cfg,
               health, run, account)
    assert d.slot is Slot.B
    assert d.estimates[Slot.A].est_cost_usd > d.estimates[Slot.B].est_cost_usd  # thinking overhead is priced in
    hard = decide(TaskProfile.for_task("reviewer", "integration", input_tokens_est=60), cfg, health, run, account)
    assert any("expected cost" in r for r in hard.reasons) or hard.slot is Slot.B  # upgrade only considered when hard
