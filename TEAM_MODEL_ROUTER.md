# AgentForge Model Router: team brief

**From:** Naman (backend model layer) · **Status:** ready to integrate · **Updated:** 29 Oct 2026

All LLM calls in AgentForge go through one function, `router.call(...)`. You say *what kind of
task* it is; the router picks the model, handles errors and fallbacks, and enforces the budget.
**Don't call the OpenAI SDK or Token Factory directly from agent code.**

Full technical reference: [docs/MODEL_ROUTER.md](docs/MODEL_ROUTER.md)

---

## 1. What's done

- Three model slots, set in config rather than in code:

  | Slot | Purpose | Model (Token Factory) |
  |---|---|---|
  | **A** | premium reasoning: plan, API contract, critical review, hard debugging | Nemotron 3.5 Lightning, full thinking |
  | **B** | main coding: backend/frontend code, debugging, test repair | Nemotron 3.5 Lightning, light thinking |
  | **C** | economy: log summaries, error classification, commit messages | Nemotron 3 Nano, no thinking |

- **Automatic routing.** Easy work goes to C, normal coding to B, planning and critical work to A.
  A task that keeps failing moves up (C→B after 1 failure, B→A after 2).
- **Error handling.** Rate limits, timeouts and outages switch to another slot automatically.
  Broken JSON gets one repair attempt. A bad API key stops immediately.
- **Budget protection.** Each build is capped at $0.30. When credits run low, easy work moves to
  cheaper settings. The router **stops** instead of looping or overspending.
- **Verified live on Token Factory** (29 Oct): routing, thinking switch, JSON-schema output.
  A 3-call test cost $0.00034. 61 automated tests pass.
- **Hackathon fit:** NVIDIA Nemotron models on Nebius Token Factory (rule H1). No Chinese-origin models
  (enforced in code).

---

## 2. Setup (5 minutes)

```bash
cd agentforge
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # Linux/Mac: .venv/bin/pip
.venv/Scripts/python -m pytest -q         # expect: 61 passed
```

Create `agentforge/.env`. Get the API key from Naman **privately**: never paste it in group chats,
commit it, or put it in `.env.example`.

```
NEBIUS_API_KEY=<ask Naman>
AF_ROUTER_CONFIG=config/profiles/web-light.toml
AF_CREDIT_BALANCE_USD=5          # YOUR share of the credits (see section 5)
```

Check it works (3 real calls, about $0.0004):

```bash
.venv/Scripts/python scripts/live_smoke.py
```

Expected: `[C economy] -> slot C`, `[B coding] -> slot B`, `[A planning] -> slot A`.

---

## 3. How to use it in your agent

```python
from engine.router import ModelRouter, RouterConfig, AccountLedger, RouterStopped

# once, when the server starts
cfg = RouterConfig.load()
base_router = ModelRouter(cfg, account=AccountLedger(cfg, "data/ledger.json"))

# once per run (own budget; shares model health with other runs).
# budget = the platform contract's RunBudget from POST /runs: max_tokens, max_cost_usd and
# max_premium_cost_usd are enforced HERE, server-side (wall time / retries are the orchestrator's).
router = base_router.for_run(run_id, on_event=event_bus.publish, budget=request.budget)

# Planner: structured output with a Pydantic schema
res = await router.call("planner", messages, schema=Plan)
plan = res.parsed                       # validated Pydantic object

# Backend / Frontend: pass the stage retry count so it can escalate
res = await router.call("backend", messages, task_type="code_backend",
                        failed_attempts=stage.retries, stage_id=stage.id)
code = res.content

# Reviewer: cheap log summary
res = await router.call("reviewer", [{"role": "user", "content": pytest_log}],
                        task_type="summarize_log")
```

**Always handle `RouterStopped`.** It means stop, not retry:

```python
try:
    res = await router.call(...)
except RouterStopped as e:
    # e.reason: run_budget_exhausted | budget_exhausted | no_eligible_model | auth_failed | max_attempts
    mark_run_failed(reason=e.reason, detail=e.detail)   # show it in the UI; do NOT loop
```

### Which `task_type` to use

| Agent | Normal work | When fixing | Cheap helpers |
|---|---|---|---|
| Planner | `plan` | `api_contract` | |
| Backend | `code_backend` | `debug`, `test_repair` | |
| Frontend | `code_frontend` | `debug`, `test_repair` | |
| Reviewer | `review_judgment` | `complex_debug` | `summarize_log`, `classify_error` |
| Deploy | `deploy_script` | | `commit_message` |
| Orchestrator | | | `extract_context`, `summarize_log` |

Optional arguments: `complexity` / `risk` (`low|medium|high|critical`), `failed_attempts`,
`stage_id`, `max_tokens`. If you don't know a value, leave it out; the defaults are sensible.

### What you get back (`res`)

`content` (text), `parsed` (Pydantic object if you passed `schema`), `slot` (A/B/C), `model_id`,
`input_tokens`, `output_tokens`, `cost_usd`, `latency_s`, `attempts`.

---

## 4. For the dashboard team

Pass `on_event=...` and forward the events over SSE. Every event is already a **contract `RunEvent`**
(`contracts/agentforge-openapi.yaml`): `{id, run_id, stage_id, type, timestamp, data}`, with the details under `data`:

| `type` | Show it as | Useful `data` fields |
|---|---|---|
| `route_decision` | "Backend → coding model (why: …)" | `model_id`, `reason`, `estimated_cost_usd`, `slot`, `reasons`, `rejected`, `inputs` (budget, health) |
| `model_call_started` | spinner on the agent | `slot`, `model_id`, `attempt` |
| `model_call_ok` | tokens / cost / latency per agent | `model_id`, `input_tokens`, `output_tokens`, `reasoning_tokens`, `cost_usd`, `latency_s` |
| `model_call_failed` | warning line | `slot`, `error_kind` (rate_limit, timeout, …) |
| `budget_warning` | yellow banner: low budget mode | remaining run/premium budget, credits |
| `budget_exhausted` / `error` | red banner: run stopped | `source: "model_router"`, `reason`, `detail`, `usage` |

For the usage panel and the platform endpoints:
- `router.usage_summary()` is the contract's **`UsageSummary`** for `GET /runs/{runId}/usage`. Its
  `confirmed_cost_usd` is always `null`, because costs are estimates (guide rule 7).
- `router.model_statuses()` is the **`ModelStatus`** list for `GET /models` (slots `premium`, `coding`,
  `economy`; never any keys).
- `router.stats()` gives the same numbers in the router's own, more detailed format.

---

## 5. Rules for the shared credits ($29.50)

- **The spend tracker is per machine** (`data/ledger.json`). If we all use one key, nobody's tracker
  sees the others' spend. So everyone sets `AF_CREDIT_BALANCE_USD` to **their own share** (e.g. $5),
  and Naman watches the real balance on the Token Factory billing page.
- A full app build costs roughly cents on these models, but a bug that loops agents can burn money.
  Keep `failed_attempts` correct and always stop on `RouterStopped`.
- For heavy testing or prompt experiments, use the **free local mode** (DGX Spark, below) or the
  **zero-cost simulation**: `.venv/Scripts/python scripts/simulate_run.py`.

---

## 6. Free local mode (DGX Spark, optional)

The same router can use Nemotron running on our DGX Spark at $0 per token:
`AF_ROUTER_CONFIG=config/profiles/spark-lightning.toml`. Setup is in
[docs/DGX_SPARK_SETUP.md](docs/DGX_SPARK_SETUP.md). It's for development and testing; the submission
uses Token Factory (rule H1).

---

## 7. Changing models (no code changes)

- **Switch the whole set:** change `AF_ROUTER_CONFIG` to another file in `config/profiles/`.
- **Swap one slot:** set `AF_MODEL_A` / `AF_MODEL_B` / `AF_MODEL_C` in `.env`.
- **Check a new model exists and supports JSON:**
  `.venv/Scripts/python scripts/verify_models.py`

---

## 8. Things in the repo you can reuse (not part of the router)

`extras/` holds an early prototype Naman started before scopes were split: orchestrator state
machine, Planner/Builder/Reviewer agents, executed Reviewer checks (pytest, contract test, JS
syntax, frontend↔contract match), a Docker sandbox, git checkpoints/rollback, and a reference arcade
app. It's untested beyond a basic check, and the router doesn't depend on it. Take what's useful.

---

## 9. FAQ

- **"It says `set NEBIUS_API_KEY`."** Your `.env` is missing or in the wrong folder; it must be
  in `agentforge/`.
- **"`RouterStopped: budget_exhausted`."** Your `AF_CREDIT_BALANCE_USD` share is used up, or a run
  hit its $0.30 cap. Tell Naman.
- **"Why did my backend call use slot A?"** It failed twice (`failed_attempts >= 2`) or is high-risk.
  The `route_decision` event's `reasons` explain every choice.
- **"Can I use Ultra / Super?"** Yes: point `AF_ROUTER_CONFIG` at `config/models.toml` (A=Ultra,
  B=Super, C=Nano). It costs more, so check with Naman first.

Questions → Naman.
