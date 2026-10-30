# Planner and Reviewer (ForgeAI AI1 + AI2) in AgentForge, with Nemotron 3 Super

Status, 30 Oct 2026: both are integrated, connected to the shared Model Router and driven by the orchestrator
(`server/pipeline.py`). The Reviewer's verdict stays fully deterministic. Every number below was measured in this
repository; raw reports are in `data/bench_planner_reviewer/`.

## 1. Audit (before changes)

| Source | What was there | Findings |
|---|---|---|
| `ForgeAI-ai1-planner/ai1-planner` | `main.py` (ProjectPlan, cycle/dependency checks, OpenAPI structure check, TypeScript generator, task assignments), `mock_response.py`, `model_policy.md` | Its own OpenAI client (a second router); `MOCK_MODE` is documented but `main.py` never reads it; mock tasks have no `files_allowed`; the OpenAPI check does not require `/api/` paths, request schemas or success schemas, so 0/10 of its plans were usable by the Backend agent (section 5). |
| `ForgeAI-main/ai2-reviewer` | checks, check runner, health check, verdict, checkpoint, rollback, events, explanations, schemas, config; 18 tests (nested in `ai2-reviewer/ai2-reviewer/tests`) | Duplicate `DEFAULT_POLICY`/`load_policy` in `checks.py` and `config.py`; `npm` run without a shell fails on Windows (`npm.cmd`); `explanations.py` typed as platform `CheckResult` but reads `log_tail`; `run_project_checks` expects a backend virtualenv, a frontend `test` script and `types.ts`, so it rejects every app AgentForge generates; a missing frontend passes as "not applicable"; flat imports (`from schemas import …`). |
| `agentforge` (this repo) | Router, orchestrator, contract-only planner (`server/planner.py`), Backend and Frontend agents, inline reviewer in the pipeline | Planner produced no plan, tasks or acceptance checks; reviewer logic was inline in the pipeline; checkpoint tags were lightweight `cp-N-green`. |
| Decision Model, Push/Pull distribution | Not in any of the three codebases | Nothing to integrate; the orchestrator keeps scheduling the fixed stages. |

Dependency map after integration:

```
API (server/app.py) -> Orchestrator (server/pipeline.py)
   -> engine/planner  (PlannerAgent)  --router.call("planner")-->  ModelRouter (engine/router) -> Token Factory / Spark
        writes plan.json, contract/openapi.yaml, types.ts, task_assignments.json
   -> Backend agent, Frontend agent (instructions include their plan tasks)  --router.call(...)--> ModelRouter
   -> engine/reviewer/agentforge.review()   checks: AI2 contract checks + engine backend/frontend suites + bandit
        verdict = engine/reviewer/verdict.py (AI2, unchanged)
        on reject: engine/reviewer/analysis.py --router.call("reviewer")--> ModelRouter   (explains only)
   -> engine/reviewer/checkpoint.py  (annotated stage-{stage_id}-green)     rollback: engine/reviewer/rollback.py
```

## 2. What changed

| File | Change | Why |
|---|---|---|
| `engine/planner/{schemas,validate,artifacts,agent}.py` (new) | AI1 schema, `validate_plan`, `validate_openapi`, TypeScript and assignment generators kept; added requirements, open questions, per-task endpoints, file scope, plan rules, parallel waves, AgentForge contract rules and validator re-asks | One Planner, on the shared router, whose output the Backend agent can always load |
| `engine/reviewer/*` (new, from AI2) | All AI2 modules moved in as a package. Fixes: executable resolution (`npm.cmd`), one policy table, contract checks callable alone, `RequiredFix.category` | Preserve AI2 and make it run on Windows and inside AgentForge |
| `engine/reviewer/agentforge.py` (new) | Feeds the engine's executed check suites + AI2 contract checks + advisory bandit into AI2's `verdict()`; not-executed required checks fail | AI2's generic checks do not fit AgentForge workspaces; the verdict logic is AI2's |
| `engine/reviewer/analysis.py` (new) | Model-assisted root cause on rejection, focused context, schema-validated, invented files dropped, deterministic fallback | Better repair hints without touching the verdict |
| `server/pipeline.py` | Planner and Reviewer wired in; AI2 events bridged to RunEvents; checkpoints via AI2 `create_checkpoint`; rollback via AI2 `restore` (+ `git clean -fd`, ignored logs kept) | Orchestrator keeps stage ownership |
| `engine/router/router.py` | `_parse` decodes the first complete JSON object | It took everything up to the last `}`, so trailing text broke every schema call |
| `config/profiles/web-nemotron.toml` (new) | Slot A = `nvidia/nemotron-3-super-120b-a12b`, thinking on | Planner and review analysis on Super |
| `server/planner.py` | Marked legacy (benchmark baseline) | |
| `scripts/bench_planner_reviewer.py` (new) | Reproducible Planner and Reviewer benchmarks + `summary` | |
| Tests: `tests/reviewer/*` (AI2's 18), `tests/test_planner_agent.py` (9), `tests/test_reviewer_integration.py` (11), `tests/fakes.py`, `tests/test_server.py`, `tests/test_frontend_agent.py` | New tests; the two old `cp-N` tag assertions now assert the annotated `stage-{id}-green` convention | |
| `pyproject.toml` | `openapi-spec-validator` (runtime), `bandit` (dev) | AI2 requirements |

## 3. Nemotron 3 Super: what is actually available

| | DGX Spark | Nebius Token Factory |
|---|---|---|
| Reachable from this laptop | **No.** NVIDIA Sync 0.117.3 is installed but has no paired device (its device list is empty and its SSH config file does not exist); its network discovery on 2026-10-29 found no Spark; `SPARK_HOST` is not set | Yes |
| Model id | Not inspectable. `deploy/spark/setup.sh` would download `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` + MTP draft and serve it with vLLM `v0.27.1 --quantization fp4` | `nvidia/nemotron-3-super-120b-a12b` (from `GET /v1/models?verbose=true`) |
| Quantization / context | NVFP4 per the setup script (unverified on the device) | fp4, 262,144 tokens |
| Features / limits | — | tools, reasoning; 1M tokens/min, 1000 requests/min |
| Price | $0 per token (local) | $0.30 in / $0.90 out per 1M, cached input $0.06 |

The integration does not depend on where the model runs: `config/profiles/spark-super.toml` already points all
slots at the Spark's OpenAI-compatible vLLM endpoint. Set `SPARK_HOST` and `AF_ROUTER_CONFIG`, then rerun the
benchmark. Hackathon rule H1 still applies to the submitted demo.

**Default since 30 Oct 2026:** `.env` uses `AF_ROUTER_CONFIG=config/profiles/web-nemotron.toml`. Model configuration in `web-nemotron`: Planner = slot A (Super, thinking on); Reviewer analysis = slot A
(`complex_debug`); builders = slot B (Beam), escalating to slot A after 2 failures (frontend: 3). Fallbacks are the
router's: A→B→C, B→A→C, C→B.

## 4. Optimizations

Implemented and measured:
- **Contract rules and validator re-asks** in the Planner: the validator's exact findings go back to the model.
- **Actionable validator messages**: a map-shaped response schema now says what to write instead. It had caused
  repeated identical failures on Super and Ultra.
- **Robust JSON extraction** in the Planner and the router (first complete object).
- **Focused reviewer context**: failing log tails (deduplicated), source around the locations the logs name, the
  backend handler for every API path in the logs, the matching contract lines, previous fixes; never the
  repository. Typical context: 1.5–4k characters.
- **Advisory security scan** limited to application code. Before, generated tests were flagged, so it failed on
  every clean app.

Tested and rejected:
- **Provider JSON mode with the injected schema** for the Planner. Super fell from 10/10 to 4/10 valid plans:
  it returned an `openapi_spec` without paths or components. Ultra went from 9/10 to 10/10. The Planner stays in
  plain mode.

Not done (needs the Spark):
- KV-cache, batching, concurrency and speculative-decoding measurements. Token Factory exposes none of these knobs.
- Prompt caching: Token Factory bills cached input at $0.06, but this account does not report cache hits per call.
  The system prompts are already a fixed prefix, which is what caching needs.

## 5. Benchmarks (fixed dataset, same cases for every candidate, 2 trials each)

Planner: 5 requests (todo, recipes, expenses, arcade, polls). "Valid" means it passes the Backend agent's contract
loader, OpenAPI 3.1 validation and the plan rules. Coverage is a fixed regex rubric per request; a failed run
counts as 0.

| Candidate (slot A model) | Valid | Coverage | Calls | Latency (median) | Cost / plan |
|---|---|---|---|---|---|
| AI1 original prompt (Super) | **0/10** (9/10 by AI1's own validators) | 0.79 | 1.0 | 28.5 s | $0.0050 |
| Legacy contract-only planner (Super) | 8/10 | 0.97 | 1.7 | 16.0 s | $0.0033 |
| Legacy contract-only planner (Ultra) | 10/10 | 0.97 | 1.1 | 5.2 s | $0.0057 |
| Integrated Planner, first version (Super) | 9/10 | 1.00 | 1.6 | 38.8 s | $0.0076 |
| Integrated Planner, first version (Ultra) | 8/10 | 0.90 | 1.8 | 22.6 s | $0.0256 |
| **Integrated Planner, optimized (Super)** | **10/10** | **1.00** | 1.2 | 25.8 s | **$0.0059** |
| Integrated Planner, optimized (Ultra) | 9/10 | 0.90 | 1.6 | 15.2 s | $0.0239 |
| Optimized + JSON mode (Super) | 4/10 | 1.00 | 2.8 | 52.8 s | $0.0121 |
| Optimized + JSON mode (Ultra) | 10/10 | 1.00 | 1.5 | 15.1 s | $0.0241 |

The legacy planner is faster but produces only a contract: no tasks, dependencies, acceptance checks or file scope.

Reviewer: the real approved bookmarks app (`run_ebeb6813a4d7`) with 5 injected faults, plus the clean app.

- Verdicts: **6/6 correct, 0 false approvals, 0 false rejections**. The shell-injection fault is flagged as
  advisory only.
- Verdict logic: 2,000 random check sets, 0 false approvals (`tests/test_reviewer_integration.py`).
- Failure analysis on the 4 rejected faults (2 trials):

| Analyzer | Owner | File | Category | Fix names the change | Latency | Cost |
|---|---|---|---|---|---|---|
| Deterministic (AI2 fallback) | 4/4 | 0/4 | — | 3/4 | 0 s | $0 |
| Super (A), first context | 8/8 | 2/8 | 8/8 | 8/8 | 1.6 s | $0.0005 |
| Nano (C), first context | 8/8 | 4/8 | 8/8 | 8/8 | 1.2 s | $0.00006 |
| **Super (A), focused context** | 8/8 | **6/8** | 8/8 | 8/8 | 2.3 s | $0.0005 |
| Nano (C), focused context | 8/8 | 6/8 | 8/8 | 8/8 | 1.3 s | $0.00008 |

- The remaining file misses are the deleted `types.ts`, which can no longer be cited.
- With the first context, Nano blamed the test file and invented Flask code (`jsonify`) in 2 of its answers.
  Super gave no file rather than guess.
- With focused context, neither invented code.
- Analysis stays on Super: equal accuracy on this small set, more grounded before the context fix, and about
  $0.0005 per rejection.

End to end, real models, `web-nemotron` profile:
- `run_57d12c4018b4` (workout log): **done and verified, $0.041**.
  - Super planned it in 1 call; Beam built backend and frontend on the first attempt.
  - The Reviewer approved on its first attempt.
  - Annotated checkpoints `stage-…-plan_and_contract-green` and `stage-…-review_and_integrate-green`; the preview
    is ready and passed its health check.
- `run_2f2c72a7f745` (reading list, run before the JSON-mode revert): done and verified, $0.072.
- No live run was rejected, so the analysis path inside a live run is covered only by the offline tests and the
  benchmark above.

Reproduce:
```bash
.venv/Scripts/python scripts/bench_planner_reviewer.py planner --profile config/profiles/web-nemotron.toml --candidates legacy,ai1-original,improved
.venv/Scripts/python scripts/bench_planner_reviewer.py reviewer --fixture data/runs/run_ebeb6813a4d7/project --analyzers deterministic,A,C
.venv/Scripts/python scripts/bench_planner_reviewer.py summary data/bench_planner_reviewer/<report>.json
```

## 6. Fine-tuning Nemotron 3 Super: not feasible now, and not worthwhile yet

- **Hardware.** NVIDIA's NeMo Megatron Bridge supports LoRA and full fine-tuning for Nemotron 3 Super. Its LoRA
  example runs on one 8-GPU node and needs a Megatron/BF16 checkpoint. One DGX Spark has 128 GB of unified
  memory. A third-party estimate puts BF16 LoRA at about 400 GB. The Spark's NVFP4 checkpoint is an inference
  format, not a training base. QLoRA on this hybrid Mamba-Transformer MoE has no documented support that I found.
- **Hosted.** Token Factory's fine-tuning catalogue has no Nemotron model. It lists Llama, Qwen, gpt-oss, Gemma
  and DeepSeek, and the Chinese-origin ones are excluded here anyway.
- **Data.** There are 13 real runs, 11 Reviewer reports and 3 real rejections. A useful dataset needs hundreds of
  validated examples per objective.
- **Need.** The optimized Super Planner is already valid 10/10, with full coverage, at $0.006 a plan. Analysis
  finds owner and category on every fault.

The next steps are more benchmark cases and better context, not training.

When there is enough data, a dataset would be built separately per objective, from runs whose result was verified:
- **Planner examples:** request → validated plan and contract, kept only if the Reviewer later approved the build.
- **Reviewer examples:** failing check logs + focused context → owner, file, category and fix, kept only if the
  next attempt passed.

## 7. Open items for teammates

- **Decision Model / Push-Pull:** consume `plan.json` → `tasks`, `parallel_groups` and `task_assignments.json`.
  The orchestrator still runs one backend stage and one frontend stage.
- **Spark owner:** pair the Spark (NVIDIA Sync) or share its address. Then run `deploy/spark/check.sh` and the
  benchmark with `spark-super.toml`.
- **AI2 repo:** the fixes in section 2 belong upstream too.
