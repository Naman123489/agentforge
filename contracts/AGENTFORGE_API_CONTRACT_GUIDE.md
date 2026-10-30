# AgentForge API Contract — Team Integration Guide

## What this contract is

`agentforge-openapi.yaml` is the **platform/control-plane API** shared by the dashboard and engine. It covers runs, stage status, SSE events, generated files, checkpoints/rollback, review results, model status, usage, preview, and optional deployments.

It is **not** the OpenAPI contract for the application AgentForge generates. The Planner must create a separate `contract/openapi.yaml` inside each run (for the current arcade benchmark, that contract defines register/login, authenticated score submission, and leaderboard endpoints).

## Source of truth

- OpenAPI version: 3.1.0
- API prefix: `/api/v1`
- JSON: request and response bodies use `application/json` unless stated otherwise.
- Authentication: bearer token for all endpoints except `GET /health`. If the current prototype has no auth implementation yet, keep the contract as the target and track auth as an integration task; do not silently remove the security requirement.
- Errors: use the shared `ErrorResponse` or `ValidationErrorResponse` schemas.
- Run creation/follow-up: return `202 Accepted`; the operation continues asynchronously.
- Event stream: `GET /runs/{runId}/events` returns Server-Sent Events. The `data` field is JSON for `RunEvent`.
- IDs are opaque strings. Clients must not parse them.
- Times are ISO-8601 date-time strings in UTC.
- File paths are relative to the run workspace. Reject path traversal and never expose host filesystem paths.

## Ownership boundaries

| Team member / component | Owns | Must implement against |
|---|---|---|
| Dashboard / frontend | Prompt form, stage board, event feed, file viewer, preview, checkpoints, usage UI | This platform contract; generate TypeScript types from OpenAPI |
| Orchestrator / API server | HTTP endpoints, run lifecycle, state machine, persistence, SSE, cancellation | This contract and `RunStatus`, `StageStatus`, `RunEvent` schemas |
| Planner | Plan and per-run generated-app contract | `TaskAssignment` and the per-run `ContractArtifact` flow |
| Backend Agent | Writes generated app into `backend/**`; does not own platform API | Per-run `contract/openapi.yaml`; returns `BuildResult` shape |
| Frontend Agent | Writes generated app frontend in its allowed workspace paths | Same per-run contract and generated TypeScript types |
| Reviewer / sandbox | Executes tests and checks; approves only on passing required checks | `ReviewReport`, `CheckResult`; approval must be based on executed evidence |
| Model Router | Chooses model, retries/fallback, token/cost usage, health | `RunEvent` types such as `route_decision`, `model_call_ok`, `model_call_failed`; `UsageSummary` |
| Checkpoint service | Git tag + machine-readable checkpoint and rollback | `Checkpoint`, rollback endpoint |
| Deploy agent (optional) | Deploy approved output and run health check | `DeploymentRequest`, `DeploymentStatus` |

## Required integration behavior

1. `POST /runs` validates the prompt and budget, creates a run, emits `run_created`, and returns `202` without waiting for the whole build.
2. The orchestrator emits a `RunEvent` for every stage transition, model routing decision, model call outcome, review, checkpoint, rollback, and terminal run state.
3. Backend and frontend agents may run in parallel only after the Planner's per-run contract is valid and file ownership boundaries are set.
4. Builders cannot merge/advance a stage until the Reviewer approves it based on executed checks.
5. A passed stage creates a checkpoint. Failed stages get bounded retries; after the configured policy, roll back to the latest approved checkpoint or stop with a reason.
6. Hard token, cost, premium spend, retry, and wall-time limits are enforced server-side. A dashboard display is not enforcement.
7. `UsageSummary.confirmed_cost_usd` must be `null` if the provider does not confirm actual billing. Do not report an estimate as a confirmed charge.
8. The API must not return API keys, provider credentials, raw secrets, or unrestricted host paths.
9. `GET /runs/{runId}/files/content` must allow only safe paths within the run workspace and should enforce a maximum response size.
10. Deployment endpoints should return a controlled error or remain disabled if deployment credentials/capability are not configured.

## Event examples

```json
{
  "id": "evt_00042",
  "run_id": "run_abc123",
  "stage_id": "backend",
  "type": "route_decision",
  "timestamp": "2026-10-29T08:30:00Z",
  "data": {
    "model_id": "configured-model-id",
    "reason": "routine coding task; premium budget preserved",
    "estimated_cost_usd": 0.02
  }
}
```

```json
{
  "id": "evt_00043",
  "run_id": "run_abc123",
  "stage_id": "backend",
  "type": "stage_finished",
  "timestamp": "2026-10-29T08:31:12Z",
  "data": {
    "status": "passed",
    "checks_passed": 13,
    "checks_failed": 0
  }
}
```

## Compatibility notes

- The current Backend Agent brief describes its result status as `done | failed | contract_change_requested | stopped`; the contract preserves those values in `BuildResult.status`.
- Existing Backend Agent integration uses `contract_ref="contract/openapi.yaml"` relative to the run workspace. Keep that convention.
- Existing local generated backend command is `python -m uvicorn backend.app:app --reload`; this is for the generated app, not the AgentForge control-plane server.
- The current backend brief says the generated app's `/api/` paths include register/login/scores/leaderboard. Do not mix those paths into the AgentForge platform endpoints.

## How to validate and generate types

Recommended CI checks:

```bash
# Validate the OpenAPI document (install once in your dev environment)
openapi-spec-validator agentforge-openapi.yaml

# Generate TypeScript types for the dashboard (example)
npx openapi-typescript agentforge-openapi.yaml -o frontend/src/api/agentforge-api.ts
```

Treat changes to this contract as team-wide changes: propose them in a pull request, update the YAML, regenerate client types, update server implementation and tests, and tell all owners about breaking changes.
