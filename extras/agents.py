"""Planner, Backend, Frontend and Reviewer agents. Every LLM call goes through the ModelRouter."""

from __future__ import annotations

import json
import re

from engine.router import ModelRouter
from engine.router.errors import RouterStopped
from .schemas import BuildResult, Check, Contract, FileOut, Plan
from .workspace import OwnershipError, Workspace

STACK_RULES = """\
Target stack (fixed, so the Reviewer can execute checks):
- backend/app.py: one FastAPI app object named `app`. SQLite via the stdlib sqlite3 module. Database file path
  from os.environ.get("AF_DB", <dir of app.py>/app.db); create tables at import time. All routes under /api/.
  Auth (only if needed): POST /api/register and POST /api/login return {"token": "..."}; protected routes read
  the header "Authorization: Bearer <token>". Hash passwords with hashlib.sha256 + a random salt.
  After all API routes, serve the frontend: if the folder ../frontend exists, mount
  StaticFiles(directory=<that folder>, html=True) at "/".
- backend/test_app.py: pytest tests using fastapi.testclient.TestClient. Set os.environ["AF_DB"] to a temp file
  BEFORE importing backend.app (import it as `from backend.app import app`). Cover every endpoint.
- frontend/: static HTML/CSS/vanilla JavaScript, no build step, no frameworks, no CDNs, no ES modules
  (plain <script src="..."> tags, no import/export). frontend/index.html is the entry page.
  Call the API with RELATIVE URLs written as full string literals, e.g. fetch('api/scores', ...) or
  fetch(`api/leaderboard?game=${game}`) - never '/api/...' with a leading slash and never build the path from
  pieces. Send JSON bodies with exactly the contract's field names.
"""

FILE_FORMAT = """\
Output format - for every file you create or change, output the COMPLETE file like this and nothing else:
=== FILE: path/to/file ===
<full file content>
=== END FILE ===
"""

FILE_RE = re.compile(r"=== FILE: (.+?) ===\n(.*?)\n=== END FILE ===", re.S)


def parse_files(text: str) -> list[FileOut]:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    out = []
    for path, body in FILE_RE.findall(text):
        body = re.sub(r"^```[\w-]*\n", "", body)
        body = re.sub(r"\n```\s*$", "", body)
        out.append(FileOut(path=path.strip().strip("`"), content=body.rstrip() + "\n"))
    return out


class Planner:
    def __init__(self, router: ModelRouter):
        self.router = router

    async def plan(self, request: str, existing: Plan | None = None, failures: list[str] | None = None) -> Plan:
        sys_prompt = (
            "[agent:planner] You are the Planner/Architect of AgentForge, a multi-agent team that builds web apps.\n"
            "Turn the user's request into a plan and an API contract that a Backend agent and a Frontend agent will "
            "implement in parallel without talking to each other, so the contract must be complete and exact.\n"
            + STACK_RULES +
            "Contract rules: every path starts with /api/; POST/PUT/PATCH list request fields; GET uses query "
            "fields; list every response field; set response_is_list for endpoints returning a JSON array; every "
            "endpoint must be used by the frontend. Keep it small: only endpoints the features need.\n"
            "Respond with one JSON object matching the schema.")
        user = f"User request:\n{request}"
        if existing:
            user += ("\n\nThis is a FOLLOW-UP on an existing app. Current plan and contract (keep existing endpoints "
                     f"unchanged unless the request requires otherwise):\n{existing.model_dump_json(indent=1)}")
        if failures:
            user += "\n\nYour previous contract was rejected:\n- " + "\n- ".join(failures)
        res = await self.router.call("planner", [{"role": "system", "content": sys_prompt},
                                                 {"role": "user", "content": user}],
                                     schema=Plan, task_type="api_contract" if existing else "plan",
                                     failed_attempts=len(failures or []), stage_id="plan")
        return res.parsed


class Builder:
    def __init__(self, router: ModelRouter, side: str):
        assert side in ("backend", "frontend")
        self.router, self.side = router, side

    async def build(self, request: str, plan: Plan, ws: Workspace, failing: list[Check],
                    attempt: int) -> BuildResult:
        files = plan.backend_files if self.side == "backend" else plan.frontend_files
        sys_prompt = (
            f"[agent:{self.side}] You are the {self.side.title()} agent of AgentForge. Implement your part of the "
            f"app exactly as the API contract says. You may only write files under {self.side}/.\n"
            + STACK_RULES + FILE_FORMAT)
        user = (f"User request: {request}\n\nPlan:\n{plan.model_dump_json(indent=1, exclude={'contract'})}\n\n"
                f"API contract (source of truth):\n{plan.contract.model_dump_json(indent=1)}\n\n"
                f"Files you own: {files}\n\nCurrent {self.side}/ files:\n{ws.snapshot(self.side + '/')}")
        if self.side == "frontend":
            user += "\n\nThe backend serves frontend/ at '/', so relative 'api/...' URLs reach the API."
        if failing:
            user += ("\n\nThe Reviewer EXECUTED checks and these FAILED. Fix the cause; output every file you change "
                     "in full:\n" + "\n\n".join(f"## {c.name}\n{c.log[-3000:]}" for c in failing))
        else:
            user += "\n\nWrite all the files now."
        task = "debug" if failing else ("code_backend" if self.side == "backend" else "code_frontend")
        res = await self.router.call(self.side, [{"role": "system", "content": sys_prompt},
                                                {"role": "user", "content": user}],
                                     task_type=task, failed_attempts=attempt, stage_id=f"build-{self.side}")
        out = parse_files(res.content)
        if not out:
            raise BuildFormatError(f"{self.side} agent returned no files in the required format")
        written = []
        for f in out:
            try:
                written.append(FileOut(path=ws.write(f.path, f.content, owner=self.side), content=f.content))
            except OwnershipError as e:
                raise BuildFormatError(str(e)) from None
        return BuildResult(agent=self.side, files=written)


class BuildFormatError(Exception):
    pass


class Reviewer:
    """Verdict comes from executed checks; the LLM (economy slot) only explains failures."""

    def __init__(self, router: ModelRouter):
        self.router = router

    async def explain(self, failing: list[Check]) -> str:
        if not failing:
            return "All checks passed."
        logs = "\n\n".join(f"## {c.name} ({c.owner})\n{c.log[-2500:]}" for c in failing)
        try:
            res = await self.router.call(
                "reviewer", [{"role": "system", "content": "[agent:reviewer] Summarise failing build checks for the "
                              "developer agents in at most 5 short bullet points: root cause and concrete fix. "
                              "No preamble."},
                             {"role": "user", "content": logs}],
                task_type="summarize_log", stage_id="review")
            return res.content.strip()
        except RouterStopped:
            return "Failing checks: " + ", ".join(c.name for c in failing)


def contract_from_json(text: str) -> Contract:
    return Contract.model_validate(json.loads(text))
