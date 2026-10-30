> **Superseded for AgentForge (30 Oct 2026):** this is the original standalone brief. The integrated behaviour (gates, states, contract change, hardening, how to run) is in [DEPLOY_AGENT.md](DEPLOY_AGENT.md). In AgentForge the orchestrator wires the agent with `RunCheckpointVerifier`, not `FileCheckpointVerifier`.

# AgentForge Deploy agent: team brief

`DeployAgent` publishes an approved final checkpoint to GitHub and can optionally deploy and health-check it. Code lives in `engine/agents/deploy*.py`; the CLI is `scripts/run_deploy_agent.py`, and tests are in `tests/`.

## When it runs

Run it only after the Reviewer approves the FINAL stage and only when the run option `enable_deploy_agent` is `true`. It is **OFF by default**.

## Orchestrator integration

```python
from pathlib import Path
from engine.agents.deploy_agent import DeployAgent, to_deployment_status
from engine.agents.deploy_git import GitPublisher
from engine.agents.deploy_github import HttpxGitHubClient
from engine.agents.deploy_models import DeployRequest
from engine.agents.deploy_runner import SubprocessRunner
from engine.agents.deploy_support import DeploymentHistory, DisabledTarget, FileCheckpointVerifier, HttpxHealthChecker

workspace_root = Path("/path/to/workspace")
agent = DeployAgent(
    github=HttpxGitHubClient(),
    publisher=GitPublisher(SubprocessRunner()),
    health=HttpxHealthChecker(),
    target=DisabledTarget(),
    history=DeploymentHistory(workspace_root / ".agentforge" / "deploy" / "deployments.json"),
    verifier=FileCheckpointVerifier(workspace_root),
    on_event=on_event,
    should_stop=should_stop,
    content_mode="tag",
)
request = DeployRequest(
    run_id="run_123", stage_id="final", checkpoint_id="cp_123",
    git_tag="cp-123", repo_name="arcade", visibility="private",
)
result = await agent.run_async(request, workspace_root)
deployment = to_deployment_status(result)
```

Wire `on_event` to the SSE stream and `should_stop` to run cancellation. The approved checkpoint must be at `.agentforge/checkpoints/<checkpoint_id>.json`, with at least `id` and `git_tag`; for the example above, the complete file is:

```json
{"id": "cp_123", "run_id": "run_123", "git_tag": "cp-123"}
```

The orchestrator must create `git_tag` inside the run workspace's Git repository; `workspace_root` is that repository's root. Default `content_mode="tag"` exports and publishes the tagged commit's tree, excluding the same private/generated paths and adding missing publish metadata; an optional checkpoint `commit` must match the tag's commit SHA. Explicit `content_mode="working_tree"` publishes current workspace files and marks `content_from_approved_tag` false with an `UNVERIFIED` note.

Set `GITHUB_TOKEN` in the **server environment only**; never put it in a request, log, or response. `DeployResult.status` is `done` (push completed), `failed` (a gate or operation failed), or `stopped` (cancellation requested). `to_deployment_status(result)` returns `None` when no deployment happened, including push-only success or failure before deployment. For push-only success, show **“pushed to GitHub, no deployment”**, never “healthy”.

Events emitted: `stage_started`, `stage_progress`, `deployment_status`, `stage_finished`, `error`.

## Try it

```powershell
py scripts/run_deploy_agent.py --workspace "path to workspace" --run-id run_123 --stage-id final --checkpoint-id cp_123 --git-tag cp-123 --repo-name arcade
py scripts/run_deploy_agent.py --workspace "path to workspace" --run-id run_123 --stage-id final --checkpoint-id cp_123 --git-tag cp-123 --repo-name arcade --push
```

The first command is a dry run: it previews the tag, or explicitly warns when a non-repo folder forces a working-tree preview. The second needs `GITHUB_TOKEN`, the approved checkpoint file and tag, and typed `yes` confirmation (or explicit `--yes`). Only `--allow-working-tree` overrides tag mode for a real push, with a loud warning. For an existing target repo, use a fine-grained token scoped to that repo with **Contents read/write**; add **Workflows write** only with `--include-ci`. Creating a new repo needs a different permission, so create the empty private repo on GitHub first. See [GitHub repository permissions](https://docs.github.com/en/rest/repos/repos) and [workflow file permissions](https://docs.github.com/en/rest/repos/contents).

## Safety and limits

- The secret scan blocks known credentials and sensitive files; generic assignment warnings do not block. Binary files are scanned as raw bytes.
- Git never force-pushes. Source files in the original workspace are not modified; `.agentforge/deploy` holds local staging, cache, and history. A dry run leaves the entire source workspace unchanged.
- **NOT done yet:** Nebius deployment is a stub; no real GitHub call has been verified; hardening Batches 2 and 3 (staging tricks, concurrency lock, Git robustness, input validation, hanging components) are pending. When an awaiting task is cancelled, a step already in progress finishes before the worker stops at the next boundary.
- Merge note: do not copy `pytest.ini` or `decision.md` into the main repo. Skip any `engine/__init__.py` or `engine/agents/__init__.py` that already exists. `tests/fixtures` contains synthetic test data.
