# Deploy agent in AgentForge

The Deploy agent (from the `inter forge` package, `engine/agents/deploy*.py`) publishes a run's **final,
Reviewer-approved checkpoint** to GitHub. It is an optional final operation, **off by default**. Live deployment
targets are not available yet. Status, 30 Oct 2026: GitHub publishing is integrated and tested offline against local
bare repositories. It has **not** been run against real GitHub, because this server has no `GITHUB_TOKEN`.

## Where it runs

```
Planner -> Backend || Frontend -> Reviewer (required checks) -> final green checkpoint (annotated stage-{id}-green tag)
  -> preview -> [only if requested AND configured] Deploy agent -> GitHub push -> (live target: not available) -> done
```

- **Pipeline:** `server/pipeline.py` runs it after the final checkpoint, when the run has `options.enable_deploy_agent`.
  Otherwise it never runs. A rejected build never reaches it.
- **API:** `POST /runs/{id}/deployments` with `{"target": "github"}` publishes a finished, verified run later.
- **Shared code:** both paths use `deploy_preconditions()`, `start_deployment()` and `deploy_final()`. There is one
  checkpoint service, one event system and one orchestrator. The run's `deploy` stage depends on the reviewer stage.
- **Run status:** a failed deployment never undoes the verified build. The run stays `done`; the `deploy` stage and
  the deployment report the failure.

## Gates (every one must pass; each refusal says which)

1. Server opt-in: `AF_DEPLOY_ENABLED=1` **and** a server-side `GITHUB_TOKEN`. A token that just happens to be in the
   environment is not enough. `/health` reports `dependencies.deploy: ok | unavailable`.
2. The run finished (`done`). The target is supported: `github` only, while `nebius_serverless` and
   `configured_target` are refused with the reason.
3. `RunCheckpointVerifier` (`engine/agents/deploy_platform.py`) approves only:
   - the run's **final** checkpoint;
   - which belongs to the **reviewer** stage, with status `passed` and verdict `approve`;
   - whose tag is `stage-{stage_id}-green`, exists as an **annotated** tag, and still points to the commit recorded
     when the checkpoint was made. `server/pipeline.py checkpoint()` now records `commit`.
4. No other deployment of the run is in progress. The agent also holds a workspace lock (thread and process level).
5. Then the agent's own gates, unchanged:
   - request validation;
   - approved-tag content mode (publishes the tagged commit's tree, not the working tree);
   - secret scan before any repository call;
   - private repository by default;
   - no force push;
   - remote-tag conflict check;
   - redaction of results, events and history.

## States (contract `DeploymentStatus.status`, dashboard Deploy tab)

| Server state | Dashboard label |
|---|---|
| `deploy` dependency not `ok`, nothing recorded | Not configured |
| configured, run done, nothing requested | Disabled |
| run not finished | Waiting |
| `queued` → `publishing` | Queued, Publishing |
| `pushed` | **Pushed to GitHub** (no live URL, health check "not run"; never "healthy") |
| `deploying` → `healthy` / `failed` / `rolled_back` | Deploying, Health check passed, Deployment failed, Rolled back |

## Contract change (additive; team note)

`contracts/agentforge-openapi.yaml`, 30 Oct 2026:
- `DeploymentRequest`:
  - `target` gains `github`;
  - new `repo_name` (optional; default `<project>-<run id>`, one repository per run) and `visibility`
    (`private` by default);
  - `health_check_path` gets a pattern.
- `DeploymentStatus`:
  - `status` gains `publishing` and `pushed`;
  - new optional fields `checkpoint_id`, `git_tag`, `repo_url`, `commit_sha`, `source_commit`, `notes`, `checks`.

**Why.** The old enum could only report a push as `healthy` or `failed`, which is wrong either way, and it had nowhere
to put the repository.

**Done.** The server, `web/src/api/schema.d.ts` (`npm run gen:api`) and the Deploy tab are updated. Existing clients
keep working.

## Nebius (`NebiusServerlessTarget`): disabled, interface kept

The real mechanism is **Nebius Serverless AI endpoints**:
- Created with `nebius ai endpoint create --image … --container-port …`, or
  `POST https://api.nebius.cloud/ai/v1/endpoints`.
- They run a **container image**, need a Nebius AI Cloud IAM token and project ID (not the Token Factory key), take
  about 5 minutes to create and bill a VM.
- The image can't be changed in place: you recreate the endpoint, or push to the same tag and restart.

`missing_requirements()` lists what's missing here:
- the adapter itself (image build + push + endpoint create/delete);
- the `nebius` CLI;
- `NEBIUS_PROJECT_ID`;
- AI Cloud credentials;
- `NEBIUS_CONTAINER_REGISTRY`.

`is_configured()` stays `False` and `deploy()` raises `NotConfigured`. No Nebius call was invented.

## Hardening added in this integration (tests written first: `tests/deploy/test_deploy_hardening.py`)

| Issue | Before | Fix |
|---|---|---|
| Junction/symlink in the staging path | `.agentforge/deploy` as a Windows junction made staging **delete a file outside the workspace** and write there (reproduced). Junctions inside the workspace were copied through, because `is_symlink()` is False for them and `rglob` follows them | `is_link()` (symlink, junction or reparse point), `safe_deploy_dir()` on every deploy path, and a walk that never follows links |
| Concurrent deployments of one workspace | shared staging and cache | `_WorkspaceLock`: thread lock + `deploy.lock` file with stale-process recovery |
| Git hooks in the reused publish repo | a hook in `.agentforge/deploy/git-cache` would run on commit | `core.hooksPath` pointed at an empty directory for every git call |
| Windows long paths | `Filename too long` in `staging/.git/objects` (35 of the agent's own tests failed from a long temp path) | `core.longpaths=true` |
| Input validation | ids, tag, repo name and health path accepted almost anything | validators on `DeployRequest`, and patterns on the API model |
| Tag moved after approval | commit check only if a checkpoint file happened to contain one | mandatory when the checkpoint service provides `approved_commit()` |
| Background deployment from the API | n/a (was always 409) | runs on its own worker thread; an unexpected error marks the deployment failed instead of leaving it `queued` |

Kept unchanged: the secret-scan policy, redaction, the approved-tag mode, no force push, bounded subprocesses,
deployment history and the rollback flow.

## Run it

```bash
# server (.env or environment; the token never goes to the browser, prompts, events or logs)
AF_DEPLOY_ENABLED=1
GITHUB_TOKEN=<fine-grained token with Administration + Contents read/write>
.venv/Scripts/python -m uvicorn server.app:app --port 8000
```
- **New runs:** create the run with `"options": {"enable_deploy_agent": true}`.
- **Finished runs:** `POST /api/v1/runs/<run>/deployments {"target": "github"}`, then poll
  `GET …/deployments/latest`, or use the dashboard's Deploy tab.
- **Standalone CLI:** `scripts/run_deploy_agent.py` (dry run by default). It verifies checkpoints from
  `.agentforge/checkpoints/<id>.json` files, so for AgentForge runs use the server path above.

## Tests

| Suite | Result |
|---|---|
| `tests/deploy/` (the agent's own 208 tests + 17 hardening) | 225 passed, 3 skipped (symlink tests need a privilege this Windows account lacks; junction equivalents run) |
| `tests/test_deploy_integration.py` | 17 passed |
| `web/src/test/deploy.test.tsx` | 3 passed |

The integration tests cover:
- after final approval; blocked before it; off by default; not configured;
- missing credentials; invalid checkpoint or tag; a moved tag;
- GitHub failure, with redaction; push success; repeat runs;
- cancellation; unavailable targets; healthy, failed and rolled-back outcomes;
- events; and API responses validated against the contract.

## For teammates

- **Planner, Reviewer, Frontend, Backend:** nothing to change.
- **Decision Model / Push-Pull:** if they take over scheduling, keep the deploy stage last and gated by
  `deploy_preconditions()`.
- **Whoever owns GitHub:** create a fine-grained token (see above), set the two variables, then run one real
  publish and report the result.
- **Nebius:** someone with Nebius AI Cloud access should implement the adapter behind `NebiusServerlessTarget`
  (see the requirements above).
