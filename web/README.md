# AgentForge web: landing page, project creation, live observer and Platform Insights

React + TypeScript + Tailwind client of the **AgentForge Control Plane API**
([`../contracts/agentforge-openapi.yaml`](../contracts/agentforge-openapi.yaml)). It starts builds, shows every
agent, stage, model-routing decision, check and checkpoint live, and lets you inspect files, preview, usage
and rollback. The backend owns run state, routing, budgets and verification; the UI only observes and sends
documented commands.

## Run it

```bash
npm install
npm run dev          # http://localhost:5173, proxies /api to the orchestrator (AGENTFORGE_API_PROXY, default :8000)
npm run dev:demo     # labelled DEMO MODE: in-browser mock of the API (no backend needed)
npm run build        # type check + production build -> dist/
npm test             # 34 tests (Vitest + Testing Library)
npm run lint         # ESLint (typescript-eslint, react-hooks)
npm run gen:api      # regenerate src/api/schema.d.ts from the contract
npm run check:secrets  # after build: fails if dist/ contains keys or demo code
```

## Configuration

| Variable | Where | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `.env` (browser) | API base, default `/api/v1` |
| `AGENTFORGE_API_PROXY` | `.env` (dev server only) | where Vite proxies `/api`, default `http://localhost:8000` |
| `VITE_DEMO_MODE` | `.env.demo` | `1` enables the labelled demo mock (`npm run dev:demo`) |
| Platform token | Settings page | sent as `Authorization: Bearer …`; kept in this tab's `sessionStorage` |

Provider keys (e.g. `NEBIUS_API_KEY`) never belong in the browser. Settings warns if one is pasted, and
`check:secrets` scans the bundle.

## Screens

The design is light "liquid glass": white surfaces, near-black ink, NVIDIA green (`nv-*` tokens in
`src/styles/index.css`) used sparingly. Green means a stage really passed its checks, amber means running or
waiting, and red means a real failure. Animations are CSS/SVG with no animation library, and all of them stop
under `prefers-reduced-motion`.

| Route | Page | Data |
|---|---|---|
| `/` | **Landing**: animated multi-agent hero, product glimpse, 5-step process, agents, model routing, CTA | `GET /models` for the real slot model ids (falls back to slot roles) |
| `/new` | **Create project**: greeting, one large prompt, 4 example cards (they fill the prompt, never submit), Options (name + hard limits) | `POST /runs` with `Idempotency-Key`, then navigates to the observer |
| `/runs/:id` | **Live observer**: header, run progress rail (Planning → Building → Integration & tests → Checkpoint → Completion), pipeline, agents, SSE event stream, inspector, and tabs for Files, Preview, Review, Models & Usage, Checkpoints, Contract and Deploy | every run endpoint below |
| `/runs` | **Projects**: all runs with a status filter | `GET /runs` |
| `/insights` | **Platform Insights**: Architecture (as in this repo), Agents, Model router, Execution, Usage and Documentation tabs | `GET /runs?limit=100`, `GET /models`, `GET /health` |
| `/settings` | API base and platform token (the "Sign in" link) | `GET /health` |

Insights shows "unavailable" rather than a number when the server does not report something, for example
confirmed cost or per-model call counts. The Decision Model and Push/Pull modules are teammates' work and are not
connected to this API, so they appear as labelled, dashed placeholders.
The selected run, tab, stage and file live in the URL, so reloads and shared links restore the same view.

## API integration (contract endpoints only)

| Endpoint | Used for |
|---|---|
| `GET /health` | top-bar health, deploy capability (`dependencies.deploy`) |
| `POST /runs` (`Idempotency-Key`) | start a build. **202 = queued**, the UI says so |
| `GET /runs`, `GET /runs/{id}` | lists, header; polled every 5 s while live as a safety net |
| `DELETE /runs/{id}` | cancel (confirmation; status changes only when the server reports it) |
| `POST /runs/{id}/followups` | follow-up request → new run |
| `GET /runs/{id}/events` | live SSE (below) |
| `GET /runs/{id}/stages` | pipeline, agents, builder results |
| `GET /runs/{id}/contracts` | Contract tab |
| `GET /runs/{id}/files`, `/files/content`, `/download` | Files tab (path traversal rejected client-side too) |
| `GET /runs/{id}/checkpoints`, `POST /runs/{id}/rollback` | Checkpoints tab |
| `GET /runs/{id}/review` | Review tab, verified badge |
| `GET /runs/{id}/usage` | usage (`confirmed_cost_usd: null` is shown as "not confirmed", never $0) |
| `GET /models` | model fleet |
| `GET /runs/{id}/preview` | Preview tab |
| `POST /runs/{id}/deployments`, `GET …/deployments/latest` | Deploy tab (only when the server reports deploy `ok`) |

Errors follow the contract's `ErrorResponse` / `ValidationErrorResponse`. 401 points to Settings, 422 lists the
field issues, 429 shows Retry-After, 404 shows "not found", and a 5xx from the dev proxy shows "API not reachable".

## Live events (SSE)

- **Transport:** fetch-based, because `EventSource` cannot send the required Bearer token. The parser handles
  multi-line `data`, comments, `retry` and CRLF.
- **Reconnects:** exponential backoff (1–15 s), resuming with `Last-Event-ID`. Duplicate event ids are dropped
  and events are ordered by timestamp. 401 or 404 stop the stream with a clear status. A finished run's history
  is read once.
- **Not the only source of truth:** each (re)connect and each relevant event type re-fetches the affected REST
  resources (run, stages, usage, review, checkpoints, files, preview), and live runs are also polled.
- **Navigation:** a per-run event store lives outside React, so streams survive navigation, and bursts update
  the UI at most ~10×/s.

## Demo mode

`npm run dev:demo` shows an amber **DEMO MODE** banner on every page and swaps the HTTP transport for an
in-browser mock of the contract. It **replays real recorded data** from a live Backend Agent build on Nebius
Token Factory: the routing events (including the real escalation to the premium model), files, check results,
model notes and usage. It also includes synthetic planner/frontend/reviewer activity, tagged
`"demo": "synthetic"` in the event JSON. Two seeded runs (`run_demo_arcade` verified, `run_demo_notes` stopped
by budget) keep stable ids; runs you start in the demo live only in the tab's memory. Deployment and ZIP
download are reported as unavailable in the demo. Regenerate the fixtures with `node scripts/build-demo-fixtures.mjs`.

## Blocked by missing backend capabilities

The Control Plane API server now exists (`server/`, see [docs/CONTROL_PLANE_API.md](../docs/CONTROL_PLANE_API.md)):
`uvicorn server.app:app --port 8000`, then paste the printed platform token in Settings. Without a running server,
the normal build shows "API not reachable" everywhere; nothing is faked. Also:
- **Frontend stage:** reported as skipped until a Frontend agent is installed on the server.
- **Deploy:** stays disabled until `/health` reports `dependencies.deploy: "ok"`.
- **File editing:** not in the contract, so the Files tab is a read-only viewer.
- **Per-agent token split:** summed from `model_call_*` events, because `UsageSummary` only has `by_model`.
  It's labelled as such.
- **Playwright E2E status:** shown only if the Reviewer reports it.
