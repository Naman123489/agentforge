"""Backend agent: implements the API exactly as the OpenAPI contract says, with tests (toolkit section 2).

Loop (same pattern as every agent): read the task -> write code -> run its own checks in the
sandbox -> fix from the real failures -> repeat until the checks pass or the iteration/budget limit
is hit -> return a typed BuildResult. All LLM calls go through the ModelRouter, so a task that keeps
failing escalates from the main coding model (B) to the premium model (A).

    agent = BackendAgent(router)
    result = await agent.run(assignment, workspace_root=Path("runs/42/project"))

Permissions: writes only backend/** (and only the globs in assignment.files_allowed). It never edits
the contract or the frontend; if the contract is wrong it returns a ContractChangeRequest instead.
"""

from __future__ import annotations

import asyncio
import fnmatch
import re
from pathlib import Path, PurePosixPath

import yaml

from ..router import ModelRouter, RouterStopped
from ..schemas import BuildResult, CheckResult, ContractChangeRequest, TaskAssignment
from .backend_checks import run_backend_checks
from .contract import Contract, contract_problems, load_contract
from .runner import Runner, default_runner

MAX_FILE_BYTES = 200_000
TEMPLATES = Path(__file__).parent / "templates" / "backend"

SYSTEM_PROMPT = """\
[agent:backend] You are the Backend agent of AgentForge, a team of AI agents that builds web apps.
You implement the backend EXACTLY as the OpenAPI contract says. A Frontend agent is building against the same
contract in parallel and a Reviewer will run automated contract tests, so every path, method, status code,
field name and field type must match the contract.

Stack and rules:
- backend/app.py and backend/test_app.py START FROM A WORKING TEMPLATE (shown under "Current backend files").
  Keep its imports and helpers - get_conn(), hash_password(pw) -> stored str, verify_password(pw, stored),
  issue_token(conn, user_id),
  Depends(current_user) - add your tables to SCHEMA, implement every route, and write the tests.
  Do not invent imports; everything you need is already imported.
- backend/app.py defines `app = FastAPI()`. Python 3.11+. Only fastapi, pydantic and the standard library.
- Storage: sqlite3 (stdlib). DB path = os.environ.get("AF_DB", <folder of app.py>/app.db), read at import time;
  create tables at import time. Open a connection per request (no global cursor).
- Request bodies are Pydantic models with exactly the contract's field names. Responses are JSON with exactly the
  contract's fields. Use the contract's success status code (e.g. 200 vs 201).
- Auth (only where the contract has security): opaque tokens stored in SQLite; read "Authorization: Bearer <t>";
  return 401 when missing or invalid. Hash passwords with hashlib.pbkdf2_hmac or sha256 plus a random salt.
  Never store plain-text passwords.
- Validate inputs (e.g. enums, non-negative numbers) and return 400 or 409 with {"detail": "..."} as described.
- Add CORSMiddleware allowing all origins (the frontend dev server calls this API).
- backend/test_app.py: pytest tests using fastapi.testclient.TestClient. Set os.environ["AF_DB"] to a temp file
  BEFORE `from backend.app import app`. Test every endpoint, including auth failures and validation errors.
  Keep the template's autouse fresh_db fixture: every test starts with an EMPTY database, so each test creates
  the users and rows it needs itself (use unique("user") for names).
- You may add helper modules under backend/ (import them as backend.<module>). You may only write under backend/.

Output format - output every file you create or change IN FULL, exactly like this, and nothing else:
=== FILE: backend/app.py ===
<complete file content>
=== END FILE ===
Optionally finish with:
=== NOTES ===
<one or two lines about design decisions>
=== END NOTES ===

If the contract is contradictory or impossible to implement, output ONLY:
=== CONTRACT_CHANGE_REQUEST ===
problem: <what is wrong>
proposed_change: <the exact change to the contract>
reason: <why the backend cannot be built correctly without it>
affected_paths: <comma-separated contract paths, e.g. /api/scores>
=== END ===
"""

FILE_RE = re.compile(r"=== FILE: (.+?) ===\n(.*?)\n=== END FILE ===", re.S)
NOTES_RE = re.compile(r"=== NOTES ===\n(.*?)\n=== END NOTES ===", re.S)
CCR_RE = re.compile(r"=== CONTRACT_CHANGE_REQUEST ===\s*(.*?)=== END ===", re.S)
CCR_KEYS = ("problem", "proposed_change", "reason", "affected_paths")


def parse_ccr(block: str) -> ContractChangeRequest:
    """'key: value' lines (values may continue on following lines) -> contract ContractChangeRequest."""
    fields: dict[str, list[str]] = {}
    key = None
    for line in block.splitlines():
        head = line.split(":", 1)[0].strip().lower()
        if ":" in line and head in CCR_KEYS:
            key = head
            fields[key] = [line.split(":", 1)[1].strip()]
        elif key:
            fields[key].append(line.strip())
    text = {k: " ".join(v).strip() for k, v in fields.items()}
    paths = [x.strip() for x in text.get("affected_paths", "").split(",") if x.strip()]
    return ContractChangeRequest(problem=text.get("problem") or "unspecified contract problem",
                                 proposed_change=text.get("proposed_change") or "see problem",
                                 reason=text.get("reason") or "the backend cannot follow the contract as written",
                                 affected_paths=paths)


class OwnershipError(ValueError):
    pass


def _score(checks: list[CheckResult]) -> float:
    """Higher is better: passing checks dominate, then the share of the agent's own tests that pass."""
    s = 100.0 * sum(c.passed and not c.skipped for c in checks)
    for c in checks:
        if c.name == "pytest":
            nums = {k: int(n) for n, k in re.findall(r"(\d+) (passed|failed|error)", c.log)}
            total = sum(nums.values())
            s += 50.0 * nums.get("passed", 0) / total if total else 0
    return s


def _snapshot_files(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
            for p in (root / "backend").rglob("*") if p.is_file() and "__pycache__" not in p.parts
            and p.suffix not in (".db", ".pyc")}


def _restore(root: Path, files: dict[str, str]) -> None:
    for p in list((root / "backend").rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".db", ".pyc") and rel not in files:
            p.unlink()
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8", newline="\n")


START_RE = re.compile(r"^\s*={3,}\s*FILE:\s*(.+?)\s*={3,}\s*$")
END_RE = re.compile(r"^\s*={3,}\s*END(\s+FILE)?\b.*$")
SECTION_RE = re.compile(r"^\s*={3,}\s*(NOTES|CONTRACT_CHANGE_REQUEST)\s*={3,}\s*$")
MD_FILE_RE = re.compile(r"(?:^|\n)[#*`\s]*(backend/[\w./-]+\.py)[`*:\s]*\n```[\w-]*\n(.*?)\n```", re.S)


def _clean(body: str) -> str:
    body = re.sub(r"^\s*```[\w-]*\n", "", body)
    body = re.sub(r"\n```\s*$", "", body.rstrip())
    return body.rstrip() + "\n"


def parse_output(text: str) -> tuple[dict[str, str], str, ContractChangeRequest | None]:
    """Files from the model's answer. Tolerates END markers with a path, a missing END marker
    (truncated answer: the file runs to the next marker or the end), and Markdown-style
    '### backend/app.py' + code fence as a fallback."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    ccr = CCR_RE.search(text)
    files: dict[str, str] = {}
    path, buf = None, []
    for line in text.splitlines():
        m = START_RE.match(line)
        if m or (path and (END_RE.match(line) or SECTION_RE.match(line))):
            if path:
                files[path] = _clean("\n".join(buf))
            path, buf = (m.group(1).strip().strip("`"), []) if m else (None, [])
            continue
        if path:
            buf.append(line)
    if path:
        files[path] = _clean("\n".join(buf))
    if not files:
        files = {p: _clean(b) for p, b in MD_FILE_RE.findall(text)}
    notes = NOTES_RE.search(text)
    return files, (notes.group(1).strip() if notes else ""), \
        (parse_ccr(ccr.group(1)) if ccr else None)


class BackendAgent:
    def __init__(self, router: ModelRouter, runner: Runner | None = None, max_iterations: int = 4):
        self.router = router
        self.runner = runner or default_runner()
        self.max_iterations = max_iterations

    async def run(self, task: TaskAssignment, workspace_root: Path) -> BuildResult:
        root = Path(workspace_root)
        (root / "backend").mkdir(parents=True, exist_ok=True)
        contract_path = Path(task.contract_ref or "contract/openapi.yaml")
        if not contract_path.is_absolute() and (root / contract_path).exists():
            contract_path = root / contract_path
        contract = load_contract(contract_path)
        problems = contract_problems(contract)
        result = BuildResult(stage_id=task.stage_id, status="failed")
        if problems:
            result.status = "contract_change_requested"
            result.contract_change = ContractChangeRequest(
                problem="; ".join(problems), proposed_change="fix the listed problems in the OpenAPI document",
                reason="the per-run contract failed validation before any code was written")
            result.notes = ["contract rejected by validation: " + "; ".join(problems)]
            return result

        failing = [CheckResult(name="reviewer", passed=False, log=t) for t in task.reviewer_failures]
        changed: dict[str, str] = {}
        if not (root / "backend" / "app.py").exists():  # toolkit tip: start builders from a known-good skeleton
            for tmpl in sorted(TEMPLATES.glob("*.tmpl")):
                rel = f"backend/{tmpl.stem}"
                body = tmpl.read_text(encoding="utf-8").replace("{title}", contract.title or "API")
                (root / rel).write_text(body, encoding="utf-8", newline="\n")
                changed[rel] = "created"
        best: tuple[float, dict[str, str], list[CheckResult]] | None = None  # (score, files, checks)
        try:
            for i in range(self.max_iterations):
                result.iterations = i + 1
                res = await self.router.call(
                    "backend", self._messages(task, contract, root, failing),
                    task_type="code_backend" if not failing else ("debug" if i == 0 else "test_repair"),
                    failed_attempts=task.failed_attempts + i, stage_id=task.stage_id)
                result.tokens += res.input_tokens + res.output_tokens
                if res.cost_usd is None:
                    result.unpriced_calls += 1
                else:
                    result.cost_usd += res.cost_usd
                model = f"{res.slot.value}:{res.model_id}"
                if model not in result.models_used:
                    result.models_used.append(model)

                log_dir = root / ".agentforge"
                log_dir.mkdir(exist_ok=True)
                (log_dir / f"backend_iter{i + 1}.md").write_text(res.content, encoding="utf-8")  # for debugging
                files, notes, ccr = parse_output(res.content)
                if ccr:
                    result.status = "contract_change_requested"
                    result.contract_change = ccr
                    break
                if notes:
                    result.notes.append(notes)
                if not files:
                    failing = [CheckResult(name="output format", passed=False,
                                           log="No files found. Use the exact === FILE: path === format.")]
                    result.checks = failing
                    continue
                try:
                    for path, content in files.items():
                        rel, action = self._write(root, task, path, content)
                        changed.setdefault(rel, action)  # first write decides created vs modified
                except OwnershipError as e:
                    if best:
                        _restore(root, best[1])
                    failing = [CheckResult(name="file ownership", passed=False, log=str(e))]
                    result.checks = failing
                    continue

                checks = await asyncio.to_thread(run_backend_checks, root, contract, self.runner)  # parallel with frontend
                failing = [c for c in checks if not c.passed]
                if not failing:
                    result.checks, result.status = checks, "done"
                    break
                score = _score(checks)
                if best is None or score >= best[0]:
                    best = (score, _snapshot_files(root), checks)
                    result.checks = checks
                else:
                    # Regression guard: this attempt made things worse. Go back to the best version and say so.
                    _restore(root, best[1])
                    result.checks = best[2]
                    failing = [c for c in best[2] if not c.passed] + [CheckResult(
                        name="regression", passed=False,
                        log="Your previous change made more checks fail (" + ", ".join(c.name for c in checks
                            if not c.passed) + "), so it was reverted. Fix the failures below starting from the "
                        "current files, changing as little as possible.")]
        except RouterStopped as e:
            result.status = "stopped"
            result.notes.append(f"router stopped: {e.reason}. {e.detail[:300]}")
        result.files_changed = sorted(changed)
        return result

    # ---- helpers -------------------------------------------------------------------
    def _messages(self, task: TaskAssignment, contract: Contract, root: Path,
                  failing: list[CheckResult]) -> list[dict]:
        current = self._snapshot(root)
        user = (f"Task ({task.stage_id}): {task.instructions}\n\n"
                f"API contract, summary:\n{contract.prompt_text()}\n\n"
                f"API contract, full OpenAPI document:\n{yaml.safe_dump(contract.raw, sort_keys=False)[:20000]}\n"
                f"Files you may write: {task.files_allowed}\n\nCurrent backend files:\n{current}")
        if failing:
            user += ("\n\nThese checks were EXECUTED against your code and FAILED. Find the root cause and fix it. "
                     "The contract is the referee: if the app disagrees with the contract, fix the app; if a test "
                     "expects something the contract does not say (or tests depend on each other), fix the test. "
                     "Output every file you change in full.\n\n"
                     + "\n\n".join(f"## {c.name}\n{c.log[-3500:]}" for c in failing))
        else:
            user += ("\n\nComplete the starter template: implement every contract endpoint in backend/app.py and "
                     "tests for each in backend/test_app.py. Output both files in full.")
        user += ("\n\nDo not explain or plan in prose. Start your reply directly with a line like "
                 "'=== FILE: backend/app.py ===' and output only file blocks (and the optional notes).")
        return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]

    def _snapshot(self, root: Path, limit: int = 60_000) -> str:
        parts, total = [], 0
        for p in sorted((root / "backend").rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            body = p.read_text(encoding="utf-8", errors="replace")
            total += len(body)
            rel = p.relative_to(root).as_posix()
            parts.append(f"=== FILE: {rel} ===\n{body}\n=== END FILE ===" if total <= limit
                         else f"(omitted {rel}: too long)")
        return "\n\n".join(parts) or "(none yet)"

    @staticmethod
    def _write(root: Path, task: TaskAssignment, rel: str, content: str) -> tuple[str, str]:
        p = PurePosixPath(rel.replace("\\", "/").lstrip("/"))
        if ".." in p.parts or not p.parts or p.parts[0] != "backend":
            raise OwnershipError(f"backend agent may only write backend/**, not {rel}")
        if not any(fnmatch.fnmatch(str(p), g) for g in task.files_allowed):
            raise OwnershipError(f"{p} is outside files_allowed {task.files_allowed}")
        if len(content.encode()) > MAX_FILE_BYTES:
            raise OwnershipError(f"{p} is larger than {MAX_FILE_BYTES} bytes")
        target = root / p
        action = "modified" if target.exists() else "created"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        return str(p), action
