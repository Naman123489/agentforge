"""Frontend agent: builds a React + TypeScript (Vite) frontend for the per-run OpenAPI contract.

Same discipline as the Backend agent (engine/agents/backend.py):
read the task -> start from a known-good template (only if no frontend exists) -> regenerate the typed API client
from the contract -> write code -> run its own checks (install, tsc, vite build, contract usage, placeholders,
served-with-backend) -> repair from the real failures -> stop at the iteration/budget limit -> typed BuildResult.

    agent = FrontendAgent(router)
    result = await agent.run(TaskAssignment(stage_id="frontend", agent="frontend", files_allowed=["frontend/**"],
                                            instructions="...", contract_ref="contract/openapi.yaml"), workspace_root)

Ownership: writes only frontend/** within files_allowed. src/api/generated.ts and package.json are AgentForge-owned
(generated from the contract / pinned template) and cannot be changed by the model. Never touches backend/** or
the contract; a contract problem becomes a ContractChangeRequest.
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Callable

from ..router import ModelRouter, RouterStopped
from ..schemas import BuildResult, CheckResult, ContractChangeRequest, TaskAssignment
from .backend import END_RE, SECTION_RE, START_RE, OwnershipError, _score, parse_output
from .contract import Contract, contract_problems, load_contract
from .frontend_checks import run_frontend_checks
from .frontend_context import GENERATED_PATH, client_summary, generate_client
from .runner import Runner, default_runner

TEMPLATES = Path(__file__).parent / "templates" / "frontend"
MAX_FILE_BYTES = 200_000
PROTECTED = {"frontend/src/api/generated.ts", "frontend/package.json", "frontend/package-lock.json"}
SKIP_DIRS = {"node_modules", "dist", ".vite"}

SYSTEM_PROMPT = """\
[agent:frontend] You are the Frontend agent of AgentForge, a team of AI agents that builds web apps. Build a polished,
working React + TypeScript frontend for the application, against the backend's API contract.

Fixed stack (the project already contains it; do not add dependencies - package.json is locked):
- React 19 + TypeScript (strict) + Vite. Plain CSS in src/styles.css with the design tokens in :root.
- src/api/generated.ts is GENERATED from the contract and must not be edited. Import from it:
    import { api, ApiError, getToken, setToken, type PostLoginResponse } from "./api/generated";
  Every server call goes through `api.<operation>(...)`. Never use fetch/axios/XMLHttpRequest. Payload and response
  types come from that file, so TypeScript enforces the contract. Store the login token with setToken(); protected
  calls send it automatically. ApiError has .status (0 = network failure) and .message (the server's detail).
- Helpers you may use: src/components/ui.tsx:
    layout: AppShell({brand, nav: [{href: "#/x", label}], current: route, right}), Page({title, subtitle, actions}),
            AuthLayout({brand, title, subtitle, footer}), Grid({min}), Toolbar;
    data:   Card({title, actions}), Stat({label, value, hint}), Badge({tone}), DataTable({columns: [{header, cell}], rows,
            rowKey, empty}), Skeleton({lines}), EmptyState({title, action});
    forms:  Button, Field (an <input>), TextArea, Select (options: [value, label][]), Checkbox({label}), Alert, Spinner.
  src/lib/useHashRoute.ts (hash routing: const [route, navigate] = useHashRoute("home")),
  src/lib/useAsync.ts (data that loads on mount: const { data, error, loading, reload } = useAsync(() => api.x(...), key)),
  src/lib/useAction.ts (form submits / buttons: const save = useAction((b: XRequest) => api.postX(b));
    const out = await save.run(body); if (out) {...}  -> save.pending, save.error (a string), save.status, save.done;
    also errorMessage(e) and errorStatus(e) for any caught error).
  These shared files (ui.tsx, lib/*) are part of the template: import them, do not rewrite them. Put new
  components in new files.
- Structure: src/App.tsx (shell: header, navigation, routes), src/pages/*.tsx (one per screen),
  src/components/*.tsx (reusable pieces). Do not put the whole app in one file.

TypeScript is strict and the build fails on any type error. Avoid these known mistakes:
- Never use useAsync for a form submit or a mutation; use useAction. Never render a value that may be void
  (`{data && ...}` where data is void): use `save.done` for success messages.
- useAsync and useAction return OBJECTS, not arrays: `const login = useAction(...)` then login.run(...),
  login.pending; never `const [run, state] = useAction(...)`. Button takes `loading`, not `pending`.
- Never cast errors (`err as ApiError`, `null as ApiError`): use `errorMessage(e)` / `errorStatus(e)` or
  `e instanceof ApiError`. Type catch variables as unknown.
- Use only props a component declares (Button variant: "primary" | "secondary" | "ghost" | "danger"; Button has
  no href - for navigation use <a className="btn btn-primary" href="#/route">).
- Optional string props take a string or undefined: `error={bad ? "Title is required" : undefined}`, never
  `error={bad && "..."}` (that passes false).
- Field is only an <input>: use TextArea for multi-line text and Select for choices.
- Event handlers use React's types (`import type { FormEvent, MouseEvent } from "react"`), not the DOM's global
  MouseEvent/Event. Every page and component you use in App.tsx must be imported there.
- Values typed unknown cannot be rendered; narrow them (String(x), Array.isArray(x)) first.
- When one form handles create and edit, call the matching operation in each branch with its own request type
  (no union-typed body).
- Imports follow ONE convention:
    pages and components: `export default function RecipesPage()`, imported as `import RecipesPage from "./pages/RecipesPage"`;
    helpers are named: `import { Button, Card } from "../components/ui"`, `import { useAsync, useAction } from "../lib/useAction"`.
  Relative paths depend on the file's folder: from src/App.tsx use "./api/generated", "./lib/...", "./pages/...";
  from src/pages/* and src/components/* use "../api/generated", "../lib/...", "../components/...".
- Use operation and type names exactly as listed in the generated client below.

Product requirements:
- Call EVERY contract operation from the UI (the checks verify this). If a resource has both PUT and PATCH,
  use both (e.g. PUT when saving the edit form, PATCH for a quick toggle or rename).
- Sessions: the login token is OPAQUE (not a JWT). Never decode, split or validate it in the browser. The user is
  signed in when getToken() returns a token, including right after a page reload; the client clears it on a 401.
- Every data view has loading, empty and error states; every form validates input, shows the server's error
  message, disables its submit while pending, and confirms success. Handle 401 by asking the user to log in.
- Responsive (works at 360px wide and on desktop), keyboard accessible, labelled inputs, visible focus.
- A coherent visual design that fits THIS product (see Design below).
- No placeholders: no TODO, lorem ipsum, href="#", empty click handlers, alert(), fake data or fake charts.

Design - your UI is opened in a real browser at 1440px and 390px, screenshotted, and its forms are submitted:
- Product name: "{product}" (never show the word "API" or the contract title to users).
- Structure: signed-out users see AuthLayout (centered, branded) for log in / sign up; signed-in users see AppShell with
  the product's sections in `nav` and the current route marked; every screen is a Page with a clear title, a
  one-line subtitle and its primary action in `actions`. Never render a bare full-width form or card on the body.
- Hierarchy: one h1 per screen (Page title), section titles in Card headers, secondary text muted. Use Stat tiles for
  key numbers, DataTable or a list for collections, Grid for card layouts, Toolbar for search and filters.
- Accent: set --primary (and --primary-soft, a very light tint of it) in src/styles.css :root to a colour that fits the
  product; change nothing else there. One accent; no gradients on content, no decorative emoji, no random colours.
- Layout must work at 390px: no fixed widths over 360px, no horizontal scrolling (DataTable scrolls itself), tap
  targets at least 40px. Games and canvases scale to the container width and offer on-screen touch buttons as well as
  keyboard controls; overlays (start / game over) never cover the board's content without a backdrop.
- States: Skeleton while loading, EmptyState with a call to action when a list is empty, Alert for errors, a success
  message after saves. Destructive actions ask for confirmation in the UI (inline confirm, not window.confirm).
- Content: real labels and helpful microcopy for THIS product; no "Welcome to the app", no lorem, no fake numbers.

Output format - output every file you create or change IN FULL, exactly like this, and nothing else:
=== FILE: frontend/src/App.tsx ===
<complete file content>
=== END FILE ===
Optionally finish with:
=== NOTES ===
<one or two lines>
=== END NOTES ===

If the contract cannot support a required screen (missing endpoint or field), output ONLY:
=== CONTRACT_CHANGE_REQUEST ===
problem: <what is missing>
proposed_change: <exact contract change>
reason: <why the UI needs it>
affected_paths: <comma-separated contract paths>
=== END ===
"""


def _truncated_file(text: str) -> str | None:
    """Path of the last file block if the answer ended before its END marker (model hit its output limit)."""
    last = None
    for line in text.splitlines():
        if m := START_RE.match(line):
            last = m.group(1).strip().strip("`")
        elif END_RE.match(line) or SECTION_RE.match(line):
            last = None
    return last


TS_ERROR_RE = re.compile(r"^(src/[\w./-]+\.tsx?)\((\d+),\d+\): error", re.M)


def _error_signatures(checks: list[CheckResult]) -> set[str]:
    """TypeScript errors without line numbers (they shift between attempts), to notice a fix that changed nothing."""
    return {re.sub(r"\(\d+,\d+\)", "", line).strip() for c in checks if not c.passed
            for line in c.log.splitlines() if TS_ERROR_RE.match(line)}


def _error_context(root: Path, failing: list[CheckResult], radius: int = 3, limit: int = 8) -> str:
    """The source lines around each TypeScript error, so the fix stays local instead of becoming a rewrite."""
    seen, parts = set(), []
    for c in failing:
        for path, line in TS_ERROR_RE.findall(c.log):
            if (path, line) in seen or len(seen) >= limit:
                continue
            seen.add((path, line))
            f = root / "frontend" / path
            if not f.is_file():
                continue
            lines, n = f.read_text(encoding="utf-8", errors="replace").splitlines(), int(line)
            snippet = "\n".join(f"{'>' if i + 1 == n else ' '}{i + 1:4} | {lines[i]}"
                                for i in range(max(0, n - 1 - radius), min(len(lines), n + radius)))
            parts.append(f"frontend/{path} line {n}:\n{snippet}")
    return ("\n\n## Source at the errors (> marks the line)\n" + "\n\n".join(parts)) if parts else ""


def _fe_files(root: Path) -> dict[str, str]:
    fe = root / "frontend"
    out = {}
    for p in sorted(fe.rglob("*")) if fe.is_dir() else []:
        rel = p.relative_to(root).as_posix()
        if p.is_file() and not (set(p.relative_to(fe).parts) & SKIP_DIRS):
            out[rel] = p.read_text(encoding="utf-8", errors="replace")
    return out


def _restore(root: Path, files: dict[str, str]) -> None:
    for rel in set(_fe_files(root)) - set(files):
        (root / rel).unlink()
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8", newline="\n")


def product_name(api_title: str | None) -> str:
    """What users see: "Snake Star API" -> "Snake Star" (the contract's title names the API, not the product)."""
    name = re.sub(r"\s*(REST\s+)?(API|Service|Backend|Server)\s*(v?\d+(\.\d+)*)?\s*$", "", (api_title or "").strip(), flags=re.I)
    return name or (api_title or "App").strip() or "App"


class FrontendAgent:
    def __init__(self, router: ModelRouter, runner: Runner | None = None, max_iterations: int = 4,
                 on_progress: Callable[[str, str], None] | None = None):
        self.router = router
        self.runner = runner or default_runner()
        self.max_iterations = max_iterations
        self.on_progress = on_progress  # (phase, message): plan | implement | verify | repair | done | failed

    def _progress(self, phase: str, message: str) -> None:
        if self.on_progress:
            self.on_progress(phase, message)

    async def run(self, task: TaskAssignment, workspace_root: Path) -> BuildResult:
        if task.agent != "frontend":
            raise ValueError(f"FrontendAgent received a task for agent '{task.agent}'")
        if not task.files_allowed or not all(g.startswith("frontend/") for g in task.files_allowed):
            raise ValueError(f"files_allowed must stay under frontend/**, got {task.files_allowed}")
        root = Path(workspace_root)
        result = BuildResult(stage_id=task.stage_id, agent="frontend", status="failed")
        contract_path = Path(task.contract_ref or "contract/openapi.yaml")
        if not contract_path.is_absolute():
            contract_path = root / contract_path
        if not contract_path.is_file():
            result.status = "contract_change_requested"
            result.contract_change = ContractChangeRequest(problem=f"contract not found at {task.contract_ref}",
                                                           proposed_change="the Planner must write contract/openapi.yaml",
                                                           reason="the frontend is generated against the contract")
            return result
        contract = load_contract(contract_path)
        problems = contract_problems(contract)
        if problems:
            result.status = "contract_change_requested"
            result.contract_change = ContractChangeRequest(problem="; ".join(problems), proposed_change="fix the listed problems",
                                                           reason="the per-run contract failed validation")
            result.notes = ["contract rejected by validation: " + "; ".join(problems)]
            return result

        changed: set[str] = set()
        fe = root / "frontend"
        if not (fe / "package.json").exists():  # preserve an existing frontend; scaffold only an empty one
            self._progress("plan", "Scaffolding the React + TypeScript template")
            for tmpl in TEMPLATES.rglob("*.tmpl"):
                rel = tmpl.relative_to(TEMPLATES).with_suffix("").as_posix()
                rel = ".gitignore" if rel == "gitignore" else rel
                dest = fe / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(tmpl.read_text(encoding="utf-8").replace("{title}", product_name(contract.title)),
                                encoding="utf-8", newline="\n")
                changed.add(f"frontend/{rel}")
        client = generate_client(contract)
        gen = root / GENERATED_PATH
        if not gen.exists() or gen.read_text(encoding="utf-8") != client:
            gen.parent.mkdir(parents=True, exist_ok=True)
            gen.write_text(client, encoding="utf-8", newline="\n")
            changed.add(GENERATED_PATH)
        self._progress("plan", f"Typed API client generated for {len(contract.endpoints)} endpoints")

        failing = [CheckResult(name="reviewer", passed=False, log=t) for t in task.reviewer_failures]
        best: tuple[float, dict[str, str], list[CheckResult]] | None = None
        prev_errors: set[str] = set()
        try:
            for i in range(self.max_iterations):
                result.iterations = i + 1
                self._progress("repair" if failing else "implement",
                               f"Attempt {i + 1}: " + (f"fixing {', '.join(c.name for c in failing)}" if failing else "writing pages and components"))
                res = await self.router.call(
                    "frontend", self._messages(task, contract, root, failing),
                    task_type="code_frontend" if not failing else ("debug" if i == 0 else "test_repair"),
                    failed_attempts=task.failed_attempts + i, stage_id=task.stage_id, output_tokens_est=12_000)
                result.tokens += res.input_tokens + res.output_tokens
                if res.cost_usd is None:
                    result.unpriced_calls += 1
                else:
                    result.cost_usd += res.cost_usd
                model = f"{res.slot.value}:{res.model_id}"
                if model not in result.models_used:
                    result.models_used.append(model)
                (root / ".agentforge").mkdir(exist_ok=True)
                (root / ".agentforge" / f"frontend_iter{i + 1}.md").write_text(res.content, encoding="utf-8")

                files, notes, ccr = parse_output(res.content)
                cut = _truncated_file(res.content)
                if cut and cut in files:  # never write half a file: it only produces confusing compiler errors
                    del files[cut]
                if ccr:
                    result.status, result.contract_change = "contract_change_requested", ccr
                    break
                if notes:
                    result.notes.append(notes)
                if cut:
                    log = (f"Your answer stopped in the middle of {cut} (no === END FILE === marker), so that file was "
                           "NOT written. Output fewer files: only the ones that need changes, each complete.")
                    if not files:
                        failing = [CheckResult(name="output truncated", passed=False, log=log)]
                        result.checks = failing
                        continue
                    result.notes.append(f"output truncated: {cut} not written")
                if not files:
                    failing = [CheckResult(name="output format", passed=False, log="No files found. Use the exact === FILE: path === format.")]
                    result.checks = failing
                    continue
                try:
                    for path, content in files.items():
                        changed.add(self._write(root, task, path, content))
                except OwnershipError as e:
                    if best:
                        _restore(root, best[1])
                    failing = [CheckResult(name="file ownership", passed=False, log=str(e))]
                    result.checks = failing
                    continue

                self._progress("verify", "Installing, type-checking, building and checking contract usage")
                checks = await asyncio.to_thread(run_frontend_checks, root, contract, self.runner)
                failing = [c for c in checks if not c.passed]
                (root / ".agentforge" / f"frontend_iter{i + 1}_checks.md").write_text(
                    "\n\n".join(f"## {c.name}: {'not executed' if c.skipped else 'ok' if c.passed else 'FAILED'}\n{c.log}"
                                for c in checks), encoding="utf-8")
                if not failing:
                    result.checks, result.status = checks, "done"
                    break
                score = _score(checks)
                if best is None or score >= best[0]:
                    best = (score, _fe_files(root), checks)
                    result.checks = checks
                else:  # regression guard, as in the Backend agent
                    _restore(root, best[1])
                    result.checks = best[2]
                    failing = [c for c in best[2] if not c.passed] + [CheckResult(
                        name="regression", passed=False,
                        log="Your previous change made more checks fail, so it was reverted. Fix the failures below "
                            "starting from the current files, changing as little as possible.")]
                if cut:
                    failing.append(CheckResult(name="output truncated", passed=False, log=log))
                errors = _error_signatures(checks)
                if repeated := sorted(errors & prev_errors):
                    failing.append(CheckResult(name="repeated errors", passed=False, log=(
                        "These errors are unchanged since your previous attempt, so that fix did not work. Re-read the "
                        "TypeScript rules in the system prompt and try a DIFFERENT fix:\n" + "\n".join(repeated[:8]))))
                prev_errors = errors
        except RouterStopped as e:
            result.status = "stopped"
            result.notes.append(f"router stopped: {e.reason}. {e.detail[:300]}")
        result.files_changed = sorted(changed)
        self._progress("done" if result.ok else "failed",
                       f"{result.status}: " + ", ".join(f"{c.name} {'ok' if c.passed else 'FAILED'}" for c in result.checks))
        return result

    # ---- helpers -----------------------------------------------------------------------------------------
    def _messages(self, task: TaskAssignment, contract: Contract, root: Path, failing: list[CheckResult]) -> list[dict]:
        gen = generate_client(contract)
        types = gen[gen.find("export interface"):gen.find("export const api")] if "export interface" in gen else ""
        current = "\n\n".join(f"=== FILE: {p} ===\n{t}\n=== END FILE ===" for p, t in _fe_files(root).items()
                              if p.startswith("frontend/src/") and not p.endswith("api/generated.ts"))[:70_000]
        user = (f"Task ({task.stage_id}): {task.instructions}\n\nAPI contract:\n{contract.prompt_text()}\n\n"
                f"Generated client (src/api/generated.ts) - operations:\n{client_summary(contract)}\n\n"
                f"Generated types:\n{types[:12_000]}\n\nCurrent frontend source files:\n{current}")
        if failing:
            user += ("\n\nThese checks were EXECUTED and FAILED. Make the SMALLEST change that fixes each one: keep "
                     "everything else in the files exactly as it is (do not restructure or restyle). Output only the "
                     "files you change, each in full.\n\n"
                     + "\n\n".join(f"## {c.name}\n{c.log[-3500:]}" for c in failing)
                     + _error_context(root, failing))
        else:
            user += ("\n\nReplace the starter App.tsx and build the complete frontend: pages, components, navigation, "
                     "forms and data views for every operation above.")
        user += ("\n\nDo not explain or plan in prose. Start your reply directly with a line like "
                 "'=== FILE: frontend/src/App.tsx ===' and output only file blocks (and the optional notes).")
        system = SYSTEM_PROMPT.replace("{product}", product_name(contract.title))
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    @staticmethod
    def _write(root: Path, task: TaskAssignment, rel: str, content: str) -> str:
        p = PurePosixPath(rel.replace("\\", "/").lstrip("/"))
        if ".." in p.parts or not p.parts or p.parts[0] != "frontend":
            raise OwnershipError(f"frontend agent may only write frontend/**, not {rel}")
        if str(p) in PROTECTED:
            raise OwnershipError(f"{p} is generated/locked by AgentForge and cannot be changed (no new dependencies; "
                                 "the API client is generated from the contract)")
        if set(p.parts) & SKIP_DIRS:
            raise OwnershipError(f"{p} is a build/dependency folder")
        if not any(fnmatch.fnmatch(str(p), g) for g in task.files_allowed):
            raise OwnershipError(f"{p} is outside files_allowed {task.files_allowed}")
        if len(content.encode()) > MAX_FILE_BYTES:
            raise OwnershipError(f"{p} is larger than {MAX_FILE_BYTES} bytes")
        if re.search(r"(NEBIUS_API_KEY|sk-[A-Za-z0-9]{20,}|\bv1\.[A-Za-z0-9_-]{40,})", content):
            raise OwnershipError(f"{p} contains what looks like a provider secret; secrets never go in browser code")
        target = root / p
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        return str(p)


def clean_build_dirs(root: Path) -> None:
    """Remove frontend/node_modules and dist (used when copying a workspace)."""
    for d in ("node_modules", "dist"):
        shutil.rmtree(root / "frontend" / d, ignore_errors=True)
