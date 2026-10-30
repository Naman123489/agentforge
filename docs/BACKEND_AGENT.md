# Backend Agent

The Backend agent turns the Planner's **OpenAPI 3.1 contract** into a working, tested **FastAPI + SQLite**
backend. It never edits the contract or the frontend. It writes code, runs its own checks in a sandbox,
fixes from the real failures, and returns a typed `BuildResult`. All model calls go through the
[model router](MODEL_ROUTER.md), so a task that keeps failing escalates to a stronger model.

```
engine/agents/
  backend.py          BackendAgent: prompt, write -> check -> fix loop, regression guard, ownership rules
  backend_checks.py   executed checks: required files, compile, ruff, pytest, contract test vs the real app
  contract.py         OpenAPI 3.1 loader ($ref, enums, auth, list responses) -> normalised endpoints
  runner.py           where code runs: DockerRunner (no network, limits) or LocalRunner; pluggable
  templates/backend/  known-good starter skeleton (DB helper, password hashing, Bearer auth, test fixture)
engine/schemas.py     TaskAssignment, BuildResult, CheckResult, ContractChangeRequest (FR-10)
```

## How it works

1. **Validate the contract.** If it's unusable (e.g. a POST without a body schema), it returns a
   `ContractChangeRequest` without calling a model.
2. **Start from the template** (first build only): working imports, `get_conn()`, `hash_password()` /
   `verify_password()`, `issue_token(conn, user_id)`, `Depends(current_user)`, CORS, and a test file that
   isolates the DB and makes unique usernames.
3. **Write code.** Router task `code_backend` goes to slot B. The model outputs complete files in
   `=== FILE: backend/... ===` blocks.
4. **Enforce ownership.** Only `backend/**` and only the globs in `files_allowed`. A violation is fed
   back as a failure; nothing outside is written.
5. **Run the checks** (sandbox):

   | Check | What it proves |
   |---|---|
   | required files | `backend/app.py`, `backend/test_app.py` exist |
   | compiles | no syntax errors |
   | lint (ruff) | no undefined names or fatal errors (`E9,F63,F7,F82,F811`) |
   | pytest | the agent's own tests pass |
   | contract test | calls **every** contract endpoint on the real app: status codes, response fields and types, list vs object, and that secured endpoints return 401/403 **without** a token |

6. **Fix from evidence.** Failures go back to the model, condensed to the first failure (with the
   failing source line) plus a one-line summary of the rest. The contract is the referee: fix the
   app if it disagrees with the contract, fix the test if the test is wrong. The router escalates
   B→A after 2 failed attempts.
7. **Regression guard.** Each attempt is scored by the checks. If an attempt makes things worse, the
   agent restores the best version and tells the model. A failed run still leaves the best version on disk.
8. **Stop.** At most 4 attempts (`max_iterations`). Returns `status`: `done`, `failed`,
   `contract_change_requested`, or `stopped` (router budget or auth). It never loops.

## Using it (orchestrator owner)

```python
from engine.agents.backend import BackendAgent
from engine.schemas import TaskAssignment

agent = BackendAgent(router)            # optional: runner=YourSandboxRunner(), max_iterations=4
result = await agent.run(TaskAssignment(
    stage_id="stage-2-backend", agent="backend",
    instructions="Implement the arcade backend exactly as the contract says.",
    contract_ref="contract/openapi.yaml",           # relative to the workspace, or absolute
    failed_attempts=stage.retries,                  # Reviewer rejections so far -> model escalation
    reviewer_failures=["pytest: ..."],              # optional (strings, or CheckResults): start in fix mode
), workspace_root=run_dir / "project")

result.status          # done | failed | contract_change_requested | stopped
result.files_changed   # ["backend/app.py", "backend/test_app.py"]
result.checks          # [CheckResult(name, passed, exit_code, duration_ms, log, skipped), ...]
result.contract_change # ContractChangeRequest(problem, proposed_change, reason, affected_paths, breaking_change)
result.models_used, result.tokens, result.cost_usd, result.iterations, result.notes  # notes: list[str]
result.to_contract()   # JSON in the platform contract's BuildResult shape (for Stage.result)
```

**Platform contract.** These types follow `contracts/agentforge-openapi.yaml` (TaskAssignment, BuildResult,
CheckResult, ContractChangeRequest). `tests/test_contract_conformance.py` validates real agent output
against that YAML. A `ContractChangeRequest` goes to
`POST /runs/{runId}/stages/{stageId}/contract-change-requests`, sent by the orchestrator. The agent itself
never calls the platform API.

The generated backend reads its DB path from `AF_DB` and runs with:
`python -m uvicorn backend.app:app` (from the workspace root).

**Sandbox.** `default_runner()` uses Docker if the image `agentforge-backend-sandbox` exists
(no network, 1 GB RAM, 2 CPUs, pid limit, no secrets), otherwise a local subprocess with a time limit
and the API keys removed from its environment. The Sandbox owner can pass any object with
`run(cmd, workdir, timeout) -> ExecResult`.

## Results (live, Nebius Token Factory, 29 Oct 2026, arcade contract)

| Models (profile) | Outcome |
|---|---|
| Lightning 30B, thinking on (`web-light`, first version) | thinking text filled the token budget; no files |
| Lightning 30B, thinking off (`web-light`) | 0 of 6 runs passed: compiled, but each fix attempt introduced a new bug |
| **Super (B) + Ultra (A)** (`web-quality`) | **4 of 5 runs passed** (2–4 attempts, 50–97 s, **$0.017–$0.038** each). The one failure had a correct app (contract test green) but a test that assumed an empty leaderboard; the template's per-test empty-DB fixture was added after it, and **both runs since passed** |

The passing backend was also run as a real server and exercised over HTTP: register/login, 401 on a bad
password and on a missing token, 400 for an unknown game, best-score-per-user leaderboard.

**Recommendation:** run the Backend agent with `AF_ROUTER_CONFIG=config/profiles/web-quality.toml`.
About $0.04 per backend build is small against the $29 of credits, and the light models didn't converge.

Fixes made because of those live runs (each covered by a test):
- B codes with thinking off
- the template removed traps that caused repeated bugs
- condensed failure logs (47 KB → 1.6 KB)
- every generated test starts with an empty database (autouse fixture)
- the regression guard
- the format-tolerant file parser
- slowness is judged per generated token, not per call

## Commands

```bash
.venv/Scripts/python -m pytest -q tests/test_backend_agent.py      # offline, scripted model, $0
AF_ROUTER_CONFIG=config/profiles/web-quality.toml \
  .venv/Scripts/python scripts/run_backend_agent.py                # live build of the arcade backend
.venv/Scripts/python scripts/run_backend_agent.py --contract my_api.yaml --instructions "..." --out runs/x
```

Each run writes `build_result.json` and the raw model answers (`.agentforge/backend_iter*.md`)
into the output folder for debugging.
