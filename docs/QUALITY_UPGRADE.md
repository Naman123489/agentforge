# AgentForge quality upgrade: audit, frontend overhaul, model benchmark (30 Oct 2026)

Every number here was measured in this repository. The raw data is in `data/frontend_bench/`, `data/bench_apps/`
and `data/bench_planner_reviewer/`, and the screenshots are under each run's `.agentforge/visual/`.

## A. Model inventory (as running, `.env` → `config/profiles/web-nemotron.toml`)

| Component | Code | Model (Nebius Token Factory) | Role |
|---|---|---|---|
| Planner / Architect | `engine/planner` | slot A `nvidia/nemotron-3-super-120b-a12b` (thinking on) | plan, tasks, OpenAPI contract |
| Backend Agent | `engine/agents/backend.py` | slot B `reflection/beam` → A after 2 failures | FastAPI + SQLite + tests |
| Frontend Agent | `engine/agents/frontend.py` | slot B `reflection/beam` → A after 3 failures | React + TypeScript UI |
| Reviewer verdict | `engine/reviewer` | no model (deterministic checks) | approve / reject |
| Reviewer failure analysis | `engine/reviewer/analysis.py` | slot A (Super) | root cause on rejection |
| Visual QA | `engine/agents/visual.py` | no model (real Chrome, measured) | layout, flows, errors → repairs |
| Benchmark design judge | `scripts/bench_frontend_models.py` | `nvidia/Nemotron-3.5-Super-VL` | evaluation only |
| Economy | router slot C | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | fallback only |
| Deploy Agent | `engine/agents/deploy*.py` | no model | GitHub publish (off by default) |
| Router / Orchestrator | `engine/router`, `server/pipeline.py` | n/a | routing, budgets; stages |
| Decision Model, Push/Pull | not in this repository | n/a | teammates' modules |

## B. Model changes: none

The benchmark (section E) did not justify replacing Beam as the coder:
- **Muse Spark** scored 2 points higher on quality, but part of the difference came from content it invented (for
  example a "Pro plan" tile that does not exist in the app). It also cost 4.4× more per app and took 2.6× longer.
- **The other candidates were worse.** Nemotron Ultra, gpt-oss-120b and GLM-5.3 failed one build each, and Kimi
  was 6× slower.

Qwen, Kimi and GLM were measured for the benchmark only; the router still blocks them.

## C. Frontend quality: what was wrong and what changed

**Diagnosis (screenshots of apps that had passed every check):**
- A full-width login form stuck to the top-left corner, with no app shell.
- A game that only works with arrow keys, so it is unusable on a phone.
- Pages titled with the API's name ("Snake Star API").
- A missing favicon, which produced 404 errors.

**Root causes:**
- The 5,300-character prompt said nothing about layout, mobile or typography.
- The template was a bare dark stylesheet.
- Nothing ever looked at the rendered result.

**Changes:**
1. **Design system v2** (template):
   - `AppShell` with a mobile menu, `Page`, `AuthLayout`, `Grid`, `Stat`, `Badge`, `DataTable`, `Skeleton`,
     `Toolbar` and `Checkbox`;
   - a light token set with one product accent;
   - a favicon, and a product name in the page title instead of the API title.
2. **Design brief** in the Frontend prompt:
   - the required structure (sign-in screens, app shell, page headers);
   - hierarchy, a single accent colour, rules for 390 px screens, touch controls for games;
   - loading, empty and error states, and inline confirmation of destructive actions;
   - opaque session tokens (see bug 2).
3. **Visual QA loop** (`engine/agents/visual.py`, `server/pipeline.py`):
   - After the Reviewer approves, the built app opens in headless Chrome at 1440 px and 390 px.
   - It signs up with a fresh test account, visits every linked screen, submits forms and takes screenshots.
   - It measures defects: JS errors, 5xx responses, mobile overflow, stretched layouts, missing h1, text
     touching the edge, wrapping headers, broken sign-in or session restore, and failed forms.
   - Defects go to the Frontend agent for up to 2 repair rounds.
   - Every repaired build must pass the **full deterministic review** and gets its own green checkpoint. A repair
     that breaks a required check is rolled back.
   - The loop stops early when a round changes nothing, and can be turned off with `AF_VISUAL_QA_ROUNDS=0`.

**Measured before and after** (same judge, same browser checks):

| | Design judge (mean) | Browser defects (mean) |
|---|---|---|
| Before (old workflow; focus, snake, workout, recipe, bookmarks) | 4.8 | 5.0 |
| After (new workflow, Beam; 5 benchmark apps) | 6.4 | 0.2 |

## D. Bugs found and fixed (root causes)

| # | Bug | Found by | Fix |
|---|---|---|---|
| 1 | The contract test reported any 404 as "route missing", so the e-commerce build failed 4× on a correct backend | live fixture run | reuse IDs from POST responses; consult the app's route table |
| 2 | Frontends decoded opaque login tokens as JWTs, so every reload logged the user out | visual QA | prompt rule; a specific QA message |
| 3 | The Planner rejected malformed JSON (trailing commas, comments) 3×, so the run failed | live run | deterministic repair; the error position is quoted in the re-ask |
| 4 | Template CSS order made the menu button appear on desktop; the mobile header was cramped | screenshots | CSS fix; right slot moves into the mobile menu |
| 5 | Visual QA false positive: a signed-out 401 cost 2 repair rounds per app | live runs | 4xx is expected, not a defect; no-progress stop |
| 6 | Visual QA only saw sign-in screens | live runs | sign-up and login flow using labels; real form submission |
| 7 | A screenshot path outside the workspace crashed a verified run | tests | path-safe; QA can never fail a verified build |
| 8 | Earlier today: login locked behind a token, `"sample"` sent for date fields, item operations on collection paths, body-less POST rejected, query strings in task endpoints | live runs | deterministic Planner repairs and contract-test formats |

## E. Benchmark (5 apps × 7 models; identical contract and backend; 1 visual repair round; escalation off)

Quality (0–100) = 50% vision judge + 30% measured defects + 20% working forms. A failed build scores 0.

| Model | Builds OK | Quality | Judge | Defects before → after | Forms OK | Cost / app | Time / app |
|---|---|---|---|---|---|---|---|
| meta-models/muse-spark-open | 5/5 | 83.4 | 7.2 | 0.2 → 0.2 | 7/7 | $0.164 | 265 s |
| **reflection/beam** (kept) | 5/5 | 81.4 | 6.4 | 0.2 → 0.2 | 9/9 | $0.037 | 102 s |
| Qwen/Qwen3.5-397B-A17B (bench only) | 5/5 | 78.8 | 6.0 | 1.6 → 0.4 | 9/9 | $0.076 | 146 s |
| moonshotai/Kimi-K2.7-Code (bench only) | 5/5 | 73.8 | 6.0 | 0.8 → 0.4 | 6/8 | $0.141 | 673 s |
| nvidia/Nemotron-3-Ultra-550b-a55b | 4/5 | 56.6 | 5.8 | 1.0 → 1.0 | 5/7 | $0.148 | 133 s |
| zai-org/GLM-5.3 (bench only) | 4/5 | 54.0 | 4.2 | 0.8 → 1.2 | 7/7 | $0.640 | 705 s |
| openai/gpt-oss-120b | 4/5 | 47.6 | 4.2 | 3.0 → 2.2 | 4/6 | $0.018 | 99 s |

- **Failed builds:** all three were TypeScript errors left after 4 repair attempts (Ultra on task-manager, GLM on
  saas-dashboard, gpt-oss on data-viz).
- **Judge reliability:** the judge gave the same score to visibly different apps several times, and missed a
  stretched layout. It is a secondary signal; the measured checks and the screenshots in `data/frontend_bench/` are
  the primary evidence.
- **Application fixtures** (built through the full pipeline): saas-dashboard $0.194, ecommerce $0.076 (after fix 1),
  task-manager $0.109, ai-notes $0.097, crud-auth $0.089, data-viz $0.111.

## F. Budget

Token Factory does not report billing per call, so these are the router's estimates from catalog prices.

| Item | Cost |
|---|---|
| Benchmark | $6.16 |
| Fixtures, including 2 failed e-commerce attempts | ≈ $1.00 |
| Pilot | $0.16 |
| Judging | ≈ $0.01 |
| Final end-to-end run (habit tracker) | $0.065 |
| **Total for this work** | **≈ $7.4** |

For the confirmed balance, check the Nebius console.

## G. Fine-tuning

Not feasible now (see `docs/PLANNER_REVIEWER.md` §6):
- Token Factory offers no Nemotron fine-tuning.
- A BF16 LoRA of Super needs about 400 GB; one Spark has 128 GB, and the Spark was not reachable.
- The data is too thin.

It is also not needed. The frontend gains came from the design system, the design brief and visual QA, and the
remaining failures are fixable in code.

## H. Tests

- **Python:** 421 passed, 4 skipped, 0 failed (final run).
- **Web:** 38 passed.
- **New in this work:**
  - `tests/test_visual_checks.py`: real Chrome;
  - `tests/test_visual_qa.py`: the loop, including rollback;
  - Planner JSON repair;
  - contract-test ID reuse and route table.

## I. Remaining issues

- **The design judge is coarse.** Human review of the screenshots remains the final word on visual quality.
- **Some screens are still never visited:** visual QA reaches only screens linked from navigation, and submits one
  form per screen.
- **Live repair rate is unmeasured:** the visual repair loop fixed defects in the benchmark (Qwen 1.6 → 0.4) but
  rarely triggers with Beam, which had few defects to start with.
- **Decision Model and Push/Pull** are still not part of this repository.

## J. End-to-end validation (final configuration)

`run_e4eec544bbdc` (habit tracker), a fresh prompt through the real API:
- **Result:** planner, backend, frontend and reviewer each passed on the first attempt; done and verified; $0.065.
- **Visual QA:** 8 screenshots of 4 screens, 0 measured defects; sign-up, login and create-habit forms worked (3/3).
- **Checkpoints:** two annotated green tags.

**Ready:** AgentForge generates a complete, usable application end to end. Judge for yourself from the screenshots in
`data/runs/run_e4eec544bbdc/project/.agentforge/visual/round1/`.
