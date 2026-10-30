# Backend Agent: team brief

**From:** Naman · **Status:** working and tested on real models · **Updated:** 29 Oct 2026

The Backend Agent reads the Planner's **OpenAPI 3.1 contract** and writes a working, tested
**FastAPI + SQLite** backend into `backend/`. It runs its own checks, fixes its own bugs, and
returns a typed result. Details: [docs/BACKEND_AGENT.md](docs/BACKEND_AGENT.md).
Its model calls go through the model router: [TEAM_MODEL_ROUTER.md](TEAM_MODEL_ROUTER.md).

## Proof it works (live, Nebius Token Factory)

- Arcade contract (register, login, submit score with auth, leaderboard): **4 of 5 builds passed**.
  Both runs after the last fix passed.
- Each build: **50–97 seconds, $0.02–$0.04**, using Nemotron 3 Super, escalating to Ultra when stuck.
- A passing backend has 12–14 of its own pytest tests plus our **contract test** green. It was also
  started as a real server: register/login, 401 without a token, 400 for a bad game, and a correct
  best-score leaderboard.
- 93 automated tests pass offline (no credits needed).

## Follows the platform contract

All messages use the shared **AgentForge Control Plane API** (`contracts/agentforge-openapi.yaml`):
`TaskAssignment` in, **`BuildResult`** out (with `CheckResult` and `ContractChangeRequest`). The router's
events are **`RunEvent`s**, and it provides **`UsageSummary`** and **`ModelStatus`**. Conformance
tests validate the real outputs against that YAML's schemas. A live build's `build_result.json`,
`events.jsonl` and `usage_summary.json` all validated. As the guide says, the Backend Agent does **not**
implement the platform endpoints; that's the orchestrator/API server.

## What each teammate needs to know

### Planner owner: the contract I need
Write an **OpenAPI 3.1** file (YAML or JSON), e.g. `contract/openapi.yaml`. Example:
[examples/arcade_openapi.yaml](examples/arcade_openapi.yaml). Please include:
- `requestBody` → `application/json` schema for every POST/PUT/PATCH (with `required` fields)
- the success response (`200`/`201`) with a JSON schema; arrays as `type: array` + `items`
- `security: [{bearer: []}]` on protected endpoints (+ `components.securitySchemes.bearer`)
- `enum`, `minimum`, `minLength` and `example` where they matter (the contract test uses them)
- all paths under `/api/`

If the contract is broken, I return a **`ContractChangeRequest`** (`problem`, `proposed_change`, `reason`,
`affected_paths`, `breaking_change`) in `result.contract_change` instead of guessing. The orchestrator
posts it to `POST /runs/{runId}/stages/{stageId}/contract-change-requests`.

### Orchestrator owner: how to call it
```python
from engine.agents.backend import BackendAgent
from engine.schemas import TaskAssignment

result = await BackendAgent(router).run(TaskAssignment(
    stage_id="backend", agent="backend",
    instructions="Implement the backend exactly as the contract says.",
    contract_ref="contract/openapi.yaml",       # relative to the workspace
    failed_attempts=stage.retries,              # Reviewer rejections so far
    reviewer_failures=["pytest: 1 failed ..."], # optional, contract form: list of strings -> starts in fix mode
), workspace_root=run_dir)

result.status        # "done" | "failed" | "contract_change_requested" | "stopped"
stage.result = result.to_contract()   # JSON exactly in the contract's BuildResult shape
```
- `done`: hand to the Reviewer. `failed`: the best version is left on disk, and `result.checks`
  says what still fails. `stopped`: budget or API key problem; stop the run.
- Also returned (contract fields): `files_changed` (paths), `checks` (with `exit_code`, `duration_ms`),
  `models_used`, `tokens`, `cost_usd` (estimate), `iterations`, `notes` (list of strings).
- `reviewer_failures` also accepts `CheckResult` objects and converts them to strings.
- It only ever writes `backend/**` (plus its own logs in `.agentforge/`). It's safe to run in parallel
  with the Frontend agent.

### Reviewer / Sandbox owner
- My checks run through a pluggable runner. Pass yours: `BackendAgent(router, runner=YourRunner())`,
  any object with `run(cmd, workdir, timeout) -> ExecResult` (see `engine/agents/runner.py`).
  Built in: a Docker runner (no network, memory/CPU/pid limits) and a local fallback.
- You can reuse my **contract test** (`engine/agents/backend_checks.py`). It calls every endpoint of
  the real app, checks status codes, response fields and types, and checks that protected endpoints
  reject requests **without** a token. It caught seeded faults: auth hole, renamed field, wrong status code.

### Frontend owner: what the generated backend gives you
- Same paths and JSON fields as the contract. CORS is open, so your dev server can call it.
- Auth: `POST /api/login` or `/api/register` returns `{"token"}`; send `Authorization: Bearer <token>`.
- Run it from the workspace root: `python -m uvicorn backend.app:app --reload` (DB file: `backend/app.db`,
  or set `AF_DB`).

### Dashboard owner
The router events (`route_decision`, `model_call_ok`, ...) show which model the Backend agent used
and why, its tokens and cost. `stage_id` tells you which stage they belong to.

## Try it yourself

```bash
.venv/Scripts/python -m pytest -q tests/test_backend_agent.py     # offline, $0
.venv/Scripts/python scripts/run_backend_agent.py                 # live arcade build, ~$0.03
```
The output folder (`data/backend_runs/<time>/`) contains the generated `backend/`,
`build_result.json` and the raw model answers.

**Model profile:** the Backend agent needs `AF_ROUTER_CONFIG=config/profiles/web-quality.toml`
(Super + Ultra). The cheaper Lightning profile never produced a passing backend (0 of 6 runs).

Questions → Naman.
