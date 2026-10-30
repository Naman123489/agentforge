# Frontend Agent

`engine/agents/frontend.py`: builds a React + TypeScript (Vite) app in `frontend/**` from the run's OpenAPI
contract, checks it with real tools, and repairs it from the real errors. It mirrors the Backend Agent and uses
the same router, `TaskAssignment` in, and `BuildResult` out.

```python
agent = FrontendAgent(router, runner=None, max_iterations=4, on_progress=None)
result = await agent.run(task, workspace_root)   # task.agent == "frontend", files_allowed under frontend/
```

## How it works

1. **Contract.** It reads `task.contract_ref`. A missing or invalid contract returns `contract_change_requested`
   without spending on a model call.
2. **Scaffold.** It seeds the template (`engine/agents/templates/frontend/`) only when `frontend/package.json` is
   missing, so existing work is kept.
   - The template pins React 19.3, TypeScript 5.9.3 and Vite 7.3.7.
   - It also provides:
     - the hash router;
     - `useAsync` for data loaded on mount;
     - `useAction` for form submits and other mutations, with `errorMessage` and `errorStatus` so the model never casts errors;
     - UI components: `Button`, `Field`, `TextArea`, `Select`, `Card`, `Alert`, `Spinner`, `EmptyState`;
     - the design tokens.
3. **Typed client.** `frontend/src/api/generated.ts` is regenerated from the contract on every run
   (`frontend_context.generate_client`).
   - It has one typed function per operation: `api.postLogin(body)`, `api.getLeaderboard(query)`, …
   - Enums become TypeScript unions, so `tsc` enforces the contract.
   - Bearer auth is handled from the contract's `security` blocks.
   - The model never writes `fetch`, and it cannot edit this file.
4. **Model.** It calls `router.call("frontend", …)` with `task_type` set as follows:
   - `code_frontend` for the first attempt;
   - `debug` while repairing;
   - `test_repair` for reviewer failures.
   It uses whatever slot the router picks; no models were added.
5. **Write.** Each file is checked before it is written:
   - it must be under `files_allowed`;
   - no `..` paths;
   - no `backend/`;
   - it must not touch `generated.ts`, `package.json`, `package-lock.json`, `node_modules/` or `dist/`;
   - it must contain no secrets.
6. **Checks.** These are listed in the next section. Failing logs, such as real `tsc` errors, are fed back,
   along with the source lines around each error and an instruction to make the smallest fix.
   - A reply that stops mid-file is never written; the model is told to send fewer files.
   - Each attempt's check logs are saved to `.agentforge/frontend_iter{n}_checks.md`.
   - The system prompt lists the TypeScript mistakes seen in live runs, such as default vs named imports,
     `cond && "text"` passed to string props, and the DOM `MouseEvent` type instead of React's.
   - A regression guard restores the best earlier attempt.
   - The loop is bounded by `max_iterations`.
7. **Result.** `done` only when every check passes. Otherwise the result is one of:
   - `failed`;
   - `stopped`, for a budget, key or HTTP 402 error from the router;
   - `contract_change_requested`, when the model sends a `CONTRACT_CHANGE_REQUEST` block.

## Which model writes the frontend

The router's slot B, which is **Reflection Beam** in `web-quality.toml`. A frontend task moves up to slot A (Ultra) only
after 3 failed attempts; the backend moves up after 2. In the benchmark below (2 apps, escalation off), Beam passed
both apps on its first attempt, while Nemotron 3 Super needed 3 and 2 attempts. To compare models again:

```bash
.venv/Scripts/python scripts/bench_frontend_models.py --app data/runs/<run>/project --model reflection/beam:0.25:1.0 --model openai/gpt-oss-120b:0.15:0.60
```

## Checks (`frontend_checks.run_frontend_checks`)

| Check | What it proves |
|---|---|
| required files | `index.html`, `src/main.tsx`, `src/App.tsx`, `generated.ts`, `package.json` exist |
| generated API client unchanged | `generated.ts` is exactly what the contract produces |
| contract usage | every operation is used via `api.X`; no raw `fetch` / XMLHttpRequest / axios; no unknown ops |
| no placeholders | no TODO/FIXME, lorem ipsum, "coming soon", `href="#"`, empty handlers, `alert(`, template marker |
| dependencies installed | `npm install` (cached by a stamp in `node_modules`) |
| typecheck (tsc) | `tsc --noEmit`, with condensed errors |
| build (vite) | `vite build` produces `dist/index.html` |
| served with backend | the built `dist/` is mounted on the generated `backend.app`; `/`, its assets and public GET endpoints all answer through TestClient |

If a tool or the backend is missing, the check is reported as **skipped (not executed)**. That is never counted as a pass.

- At build time the backend may not exist yet, or may not import because it is still being written, so the
  integration check is optional and those cases are reported as not executed.
- The Reviewer re-runs every check with `require_integration=True`.

## Orchestrator integration (`server/pipeline.py`)

- The Backend and Frontend stages run in parallel. A retry rebuilds only the failing side, and either side can raise a CCR.
- `stage_progress` events carry `phase` (plan, implement, repair, verify, done or failed).
- `finish_stage` carries check counts.
- The Reviewer adds the `frontend: …` checks, and the checkpoint summary says both sides were approved.
- **Preview.** When `frontend/dist` exists, `.agentforge/preview_app.py` serves UI and API on one origin, with the preview URL at `/`.
- `create_app(frontend_agent="auto" | None | cls)`: `None` means backend-only, which is the default in the server tests.

## Tests

`tests/test_frontend_agent.py` runs offline with a scripted model. npm, tsc and vite are faked; the Python
integration smoke test runs for real. It covers:
- client generation;
- the build and every check;
- integration with the generated backend;
- repair from tsc errors, and bounded retries;
- partial success not reported as done;
- placeholders and raw fetch;
- ownership, locked files, path traversal and secrets;
- preserving an existing frontend;
- invalid assignments and contracts;
- CCR;
- reviewer feedback;
- 402 and 401 stops;
- npm missing;
- a full orchestrator run with the Reviewer and preview.

```bash
.venv/Scripts/python -m pytest -q tests/test_frontend_agent.py
AF_REAL_TOOLCHAIN=1 .venv/Scripts/python -m pytest -q tests/test_frontend_agent.py -k real_toolchain   # real npm/tsc/vite
```

## Limits

- `npm install` needs access to the npm registry. Checks run as local processes, not in Docker.
- There is no browser E2E (Playwright). The integration check is HTTP-level: it confirms the UI and API are served together, not that clicks work.
