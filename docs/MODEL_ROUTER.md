# Model Router ("Intelligent Model Shifter")

Owner: backend model layer. One entry point for every LLM call in AgentForge. Agents describe the
task; the router picks one of three configured models, handles failures and fallback, enforces
budgets, and explains every decision on the event stream. It never calls an LLM to decide routing.

**Status (2026-10-29):** 61 offline tests pass. A live smoke test on Nebius Token Factory routed a log
summary to C, routine code to B and planning to A, with schema-valid JSON. Cost was $0.00034.

```
engine/router/
  config.py   three slots + budgets; config/models.toml, profiles, env overrides, .env loading
  task.py     TaskProfile: deterministic task metadata (no LLM classifier)
  policy.py   decide(): pure, deterministic routing policy with reasons for every choice
  health.py   circuit breaker, rate-limit cooldown, latency EWMA (shared across runs)
  budget.py   per-run budget (FR-9, FR-17) + account ledger (credits, free allowances, persisted)
  errors.py   provider error classification, RouterStopped
  router.py   ModelRouter.call(): decide -> call -> classify -> fall back / wait / stop
```

## Profiles (which models fill the three slots)

Pick one with `AF_ROUTER_CONFIG`. Agent code never changes.

| Profile | A (premium reasoning) | B (main coding) | C (economy) | Cost |
|---|---|---|---|---|
| `config/profiles/web-light.toml` **(submission)** | Lightning 3.5, full thinking | Lightning 3.5, low-effort thinking | Nemotron 3 Nano, no thinking | $0.06 / $0.24 per 1M, from Token Factory credits |
| `config/profiles/web-nemotron.toml` **(used by `.env`)** | Nemotron 3 Super 120B, thinking | Reflection Beam | Nemotron 3 Nano | $0.30/$0.90, $0.25/$1.00, $0.06/$0.24 |
| `config/profiles/web-quality.toml` | Nemotron 3 Ultra, thinking | Reflection Beam | Nemotron 3 Nano | $1.00/$3.00, $0.25/$1.00, $0.06/$0.24 |
| `config/models.toml` | Nemotron 3 Ultra | Nemotron 3 Super | Nemotron 3 Nano | $1.00/$3.00, $0.30/$0.90, $0.06/$0.24 |
| `config/profiles/spark-lightning.toml` | Lightning, full thinking | Lightning, low effort | Lightning, no thinking | $0 (DGX Spark) |
| `config/profiles/spark-super.toml` / `spark-mixed.toml` | Super on the Spark | Super | Super / Lightning | $0 |

All model IDs and prices were read from this account's Token Factory catalog. On the web profile,
Token Factory honours the thinking switch (`chat_template_kwargs`), returns reasoning separately in
`reasoning_content`, and supports `json_object` and `json_schema`, including with thinking on (all
verified 2026-10-29). All of these are hosted on Token Factory, which covers hackathon rule H1. Slot A in web-nemotron is Super because it planned 10/10 valid at ~1/4 of Ultra's cost (`docs/PLANNER_REVIEWER.md`). Slot B in web-quality is Reflection Beam because it won a frontend benchmark (`scripts/bench_frontend_models.py`, 2026-10-30); Chinese-origin models in the catalog were excluded. The Spark profiles are for $0
local development; see [DGX_SPARK_SETUP.md](DGX_SPARK_SETUP.md).

## Billing: free vs credits vs paid

Nothing is treated as free unless configured. Each slot has `billing`:

- `paid`: every token costs `price_*`.
- `prepaid_credits`: tokens cost `price_*`, drawn from `AF_CREDIT_BALANCE_USD`. Spend is persisted
  in `data/ledger.json`, so a restart doesn't reset it. Under 30% left switches on low-budget mode;
  a call that would exceed the balance is refused.
- `free_allowance`: $0 only up to `free_allowance_tokens`, then `price_*`. Ineligible if no price is set.
- `local`: self-hosted (DGX Spark), $0 per token. Token limits act as the GPU-time budget.

A model without a confirmed price is tracked in tokens and reported as cost `null`, never $0.
`reasoning_overhead` per slot prices in billed thinking tokens (A ≈3×, B ≈1.5×, C 1×).

## Policy (`policy.decide`), in order

1. **Capability floor.** High risk or high complexity, medium+ code generation, or non-trivial
   structured output: never C.
2. **Target slot** from the task type (table in `task.py`). C→B after 1 failed attempt.
   B→A after 2 failed attempts.
3. **Premium gate (A).** Blocked by the premium token/cost cap; in low-budget mode for
   non-critical work; and when it would use the last 25% of premium budget reserved for
   critical or escalated tasks.
4. **Expected cost to success** (prices known, not all $0):
   `cost / p_success + (1 − p_success) × downstream_failure_cost[risk]`. It can move an A-task
   down to B, but moves a B-task up to A only if the task is high complexity or high risk.
   Routine work stays on B.
5. **Low-budget mode** (run budget < 20% or credits < 30%): low-complexity B work moves to C.
   Premium is used only for critical or escalated tasks.
6. **Eligibility:** enabled, healthy, context fits, run tokens, cost and credits cover the estimate.
   Slow models are tried last.
7. **Outcome:**
   - `CALL` with ordered fallbacks: A→B→C, B→A→C, C→B (never C→A).
   - `WAIT` once if the preferred model is back within 15 s and the alternative is an upgrade.
   - `STOP` (`RouterStopped`) when the budget is exhausted or no eligible model exists.

Every decision event carries `reasons`, `rejected` (why each other slot was not used), cost and
success estimates, and `inputs`: remaining run/premium budget, credits, free allowance, per-model
health, and required context.

## Failure handling inside one call

| Error | Action |
|---|---|
| 429 rate limit | cool down the slot (Retry-After), re-decide: fallback, or wait once |
| 5xx / timeout / connection | retry the same slot once with backoff, then exclude it for this call |
| 3 consecutive transient failures | circuit opens for 60 s (all runs), then one half-open probe |
| 404 model not found | disable the slot (bad model ID) |
| context length | exclude the slot; try a slot with more context |
| invalid JSON / schema | one repair turn on the same model, then the next eligible model |
| 401/403 | stop immediately (`auth_failed`) |
| 4 attempts in one call | stop (`max_attempts`) |

The OpenAI SDK runs with `max_retries=0`, so retries are never doubled.

## Integration contract for the other agents (teammates)

```python
from engine.router import ModelRouter, RouterStopped, AccountLedger, RouterConfig

cfg = RouterConfig.load()                                   # reads .env + AF_ROUTER_CONFIG
base = ModelRouter(cfg, account=AccountLedger(cfg, "data/ledger.json"))   # once per process
router = base.for_run(run_id, on_event=bus.publish)         # per run: own budget, shared health

plan = await router.call("planner", messages, schema=Plan)  # role default task type
code = await router.call("backend", messages, task_type="debug",
                         failed_attempts=stage.retries, stage_id=stage.id)
note = await router.call("reviewer", [{"role": "user", "content": log}], task_type="summarize_log")
# result: .content, .parsed (pydantic, when schema=...), .slot, .model_id,
#         .input_tokens, .output_tokens, .cost_usd, .latency_s, .attempts

try: ...
except RouterStopped as e:   # e.reason: run_budget_exhausted | budget_exhausted | no_eligible_model
    escalate(e.reason)       #           | auth_failed | max_attempts  -> stop and report, never loop
```

- **Roles:** `planner`, `backend`, `frontend`, `reviewer`, `deploy`, `orchestrator`.
- **Task types:** `plan`, `api_contract`, `review_judgment`, `complex_debug`, `code_backend`,
  `code_frontend`, `debug`, `test_repair`, `integration`, `summarize_log`, `classify_error`,
  `extract_context`, `commit_message`, `simple_edit`, `deploy_script`.
- **Optional overrides:** `complexity`, `risk` (`low|medium|high|critical`), `failed_attempts`,
  `stage_id`, `input_tokens_est`, `output_tokens_est`, `max_tokens`.

`failed_attempts` should be the stage retry count (Reviewer rejections). It drives C→B and B→A.
Provider errors are handled inside the call.

**Events** (`on_event`, for SSE/dashboard): `route_decision`, `model_call_ok` (tokens, `cost_usd`,
`price_verified`, latency, budget snapshot), `model_call_failed` (`error_kind`), `router_stop`.
`router.stats()` gives per-run totals, per-slot usage, model health and remaining credits (FR-17).

## Running

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m pytest -q                                   # 61 tests, no network
.venv/Scripts/python scripts/simulate_run.py                        # zero-token demo of every policy branch
AF_ROUTER_CONFIG=config/profiles/web-light.toml .venv/Scripts/python scripts/verify_models.py   # IDs, JSON support
AF_ROUTER_CONFIG=config/profiles/web-light.toml .venv/Scripts/python scripts/live_smoke.py      # 3 real calls, ~$0.0004
```

`.env` (never committed): `NEBIUS_API_KEY`, `AF_ROUTER_CONFIG`, `AF_CREDIT_BALANCE_USD`.

## Deviations from the project docs

- **No gpt-oss fallbacks.** The docs list gpt-oss-120b/20b; the three-slot design falls back between
  A, B and C instead. To use gpt-oss, assign it to a slot.
- **No Ultra in the submission profile.** Ultra exists on Token Factory ($1/$3) and is in
  `models.toml`, but the submission profile uses the cheaper Lightning/Nano to stretch the credits.
- **Retries are hand-rolled, not Tenacity**, because they interact with fallback and health state.
- **Capability scores are priors**, to be replaced with bake-off results (B13).
