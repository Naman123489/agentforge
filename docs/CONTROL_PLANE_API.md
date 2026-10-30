# AgentForge Control Plane API server

FastAPI implementation of [`contracts/agentforge-openapi.yaml`](../contracts/agentforge-openapi.yaml) (`/api/v1`).
It runs builds with the model router and the Backend Agent and streams everything to the dashboard
(`web/`). Responses are validated against the contract's JSON schemas in `tests/test_server.py`.

```
server/
  app.py       routes, auth, contract-shaped errors, SSE, files/zip, rollback, static dashboard
  pipeline.py  run state machine in a per-run worker thread; git checkpoints; uvicorn preview
  planner.py   minimal Planner: request -> validated per-run OpenAPI 3.1 contract
  state.py     RunRecord, ordered event log, run.json / events.jsonl persistence
```

## Run it

```bash
cd agentforge
.venv/Scripts/python -m uvicorn server.app:app --port 8000      # Linux/Spark: .venv/bin/python
cd web && npm run dev                                           # dashboard on :5173 proxies /api -> :8000
```

On first start the server prints a **platform token** and saves it in `data/platform_token.txt`. Restarts
reuse it. Paste it in the dashboard under Settings → Platform access token, or set `AF_PLATFORM_TOKEN`.
For single-process hosting, run `npm run build` in `web/` and the server also serves the dashboard at `/`.

| Env var | Default | Meaning |
|---|---|---|
| `AF_ROUTER_CONFIG` | from `.env` | model profile (`config/profiles/web-quality.toml` = Super + Ultra on Token Factory) |
| `NEBIUS_API_KEY` | from `.env` | provider key; server-side only, never sent to clients |
| `AF_PLATFORM_TOKEN` | generated | bearer token for the API |
| `AF_AUTH=off` | on | disable auth (local development only) |
| `AF_MAX_CONCURRENT_RUNS` | 2 | more runs → `429` with `Retry-After` |
| `AF_SANDBOX` | auto | `docker` when the `agentforge-backend-sandbox` image exists, else `local` (reported as `sandbox: degraded` in `/health`) |

## What a run does

1. **Planner** (`engine/planner`, see `docs/PLANNER_REVIEWER.md`). Writes a validated plan (`plan.json`: requirements,
   tasks with owners, endpoints, dependencies, acceptance checks, file scope), `contract/openapi.yaml`, `types.ts`
   and `task_assignments.json`. Invalid output is re-asked with the validator's findings. The approved plan
   becomes the annotated checkpoint tag **`stage-{planner_stage_id}-green`** (git + `checkpoints/*.json`).
2. **Build.** The **Backend Agent** writes `backend/**` and the **Frontend Agent** writes `frontend/**`, in
   parallel, each with its own checks and fix loop (see `docs/FRONTEND_AGENT.md`). Each gets its tasks from the
   plan. A retry rebuilds only the failing side.
3. **Reviewer** (`engine/reviewer`, the ForgeAI AI2 Reviewer). Re-runs every check itself: contract structure,
   OpenAPI 3.1 conformance, `types.ts` drift, compile, ruff, pytest, a contract test against the running app,
   and the frontend checks (`frontend: …`), where serving the built UI with the backend is required. A bandit
   scan is advisory. The verdict is the check results; a required check that failed or did not run rejects.
4. **Approve.** Annotated checkpoint tag **`stage-{reviewer_stage_id}-green`**, then the **preview**: the
   generated backend runs under uvicorn and is health-checked. With a built frontend the preview serves UI and
   API on one origin (URL `/`); otherwise the URL is the backend's Swagger UI.
4b. **Deploy (optional, off by default).** Only when the run sets `options.enable_deploy_agent` and the server has
   `AF_DEPLOY_ENABLED=1` and `GITHUB_TOKEN`: the Deploy agent publishes the run's **final approved checkpoint tag**
   to a private GitHub repository (`deploy` stage, `deployment_status` events, `GET …/deployments/latest`). The same
   is available afterwards through `POST /runs/{id}/deployments` with `{"target": "github"}`. A push ends as
   `pushed`, never `healthy`; live targets are not available yet (see `docs/DEPLOY_AGENT.md`).
5. **Reject.** A model explains the failures (root cause, owner, file, fix) for the next attempt; it cannot
   change the verdict. The builder retries, up to `max_retries_per_stage`. After that the workspace is
   **rolled back** to the last checkpoint (annotated `stage-*-green` tags only) and the run **fails** with the reason.
6. **Limits.** `max_tokens`, `max_cost_usd` and `max_premium_cost_usd` are enforced by the router (including
   capping each call's `max_tokens`); `max_wall_time_seconds` by the pipeline. Cancellation is cooperative.
   Every stop ends with `run_finished` and a contract `ErrorResponse` in `RunDetail.error`.

Every stage transition, routing decision, model call, check, checkpoint and rollback is a `RunEvent`. Ids are
assigned in order by the server, so `Last-Event-ID` resumes exactly. A finished run's stream sends its history
and closes. History survives restarts (`data/runs/<id>/run.json`, `events.jsonl`). A run that was in progress
during a restart is reported as `failed / server_restarted`.

## Endpoints

All contract paths are implemented. Two behave as "not available":
- `POST …/deployments` returns `409 conflict` (no deploy target configured) and `GET …/deployments/latest` returns `404`.
- `/health` reports `deploy: unavailable`.

`PUT …/contracts` is accepted only before the build starts (`409` afterwards: use a ContractChangeRequest).
`POST …/rollback` requires a finished run; `retry_after_rollback: true` returns `409` (start a follow-up instead).
Follow-ups start from the exact files of the chosen checkpoint (`git archive`), with the Planner seeing the
existing contract.

## Verified

- **`tests/test_server.py` (8 tests).** Full run through HTTP (scripted model, real sandbox checks, real git,
  real uvicorn preview); SSE plus `Last-Event-ID` resume; path traversal (`../`, absolute, `.git/`, drive,
  backslash → 400); ZIP; rollback; cancel/deploy conflicts; rejected build → rollback → `review_failed`;
  budget stop; follow-up from a checkpoint. Every response is validated against the contract schemas.
- **Live, through the dashboard.** The real chain (dashboard → API → pipeline → router → Nebius Token Factory)
  works. On 2026-10-29 Nebius answered **`402 Payment Required: You have exhausted your budget`** for every
  model (our own ledger had recorded about $0.26 of spend). The router now stops immediately on 402
  (`provider_budget_exhausted`) instead of retrying, and the dashboard shows the reason. A full live build
  needs funds on the Token Factory account, or the DGX Spark profile.

## Known limits

- The frontend's npm install needs network access to the npm registry; there is no browser (Playwright) E2E.
- Without Docker, checks and the preview run as local processes (time-limited, secrets stripped, but not isolated).
- Single process, in-memory event fan-out; a run in progress does not survive a restart.
