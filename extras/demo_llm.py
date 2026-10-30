"""Scripted stand-in for a model provider (AF_MODE=demo). SIMULATED: no model is called.

Answers each agent from the reference arcade in engine/demo_app so the whole
pipeline (router, orchestrator, sandbox checks, UI) can run without a GPU or
credits. The UI labels every demo run as simulated.
"""

from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path
from types import SimpleNamespace

APP = Path(__file__).parent / "demo_app"

PLAN = {
    "title": "Mini Arcade",
    "summary": "Arcade site with Snake, 2048 and Memory Match, user accounts, score submission and leaderboards.",
    "features": ["Sign up / log in with token auth", "Snake (canvas)", "2048", "Memory Match",
                 "Submit scores when logged in", "Per-game leaderboard"],
    "backend_files": ["backend/app.py", "backend/test_app.py"],
    "frontend_files": ["frontend/index.html", "frontend/style.css", "frontend/api.js", "frontend/snake.js",
                       "frontend/game2048.js", "frontend/memory.js", "frontend/app.js"],
    "acceptance": ["pytest passes for backend/test_app.py", "contract test passes against the running app",
                   "frontend calls only contract endpoints with contract field names",
                   "a logged-in score appears on GET /api/leaderboard"],
    "contract": {"endpoints": [
        {"method": "POST", "path": "/api/register", "summary": "create account",
         "request": [{"name": "username"}, {"name": "password"}],
         "response": [{"name": "token"}, {"name": "username"}]},
        {"method": "POST", "path": "/api/login", "summary": "log in",
         "request": [{"name": "username"}, {"name": "password"}],
         "response": [{"name": "token"}, {"name": "username"}]},
        {"method": "POST", "path": "/api/scores", "summary": "submit a score", "auth": True,
         "request": [{"name": "game"}, {"name": "score", "type": "integer"}],
         "response": [{"name": "id", "type": "integer"}, {"name": "username"}, {"name": "game"},
                      {"name": "score", "type": "integer"}]},
        {"method": "GET", "path": "/api/leaderboard", "summary": "top scores for a game",
         "query": [{"name": "game"}], "response_is_list": True,
         "response": [{"name": "username"}, {"name": "game"}, {"name": "score", "type": "integer"}]},
    ], "notes": "games: snake, 2048, memory"},
}


def _files(side: str) -> str:
    out = []
    for p in sorted((APP / side).glob("*")):
        out.append(f"=== FILE: {side}/{p.name} ===\n{p.read_text(encoding='utf-8')}\n=== END FILE ===")
    return "\n\n".join(out)


class DemoClient:
    def __init__(self, speed: float = 1.0):
        self.speed = speed
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, model, messages, max_tokens=None, **_):
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        if "[agent:planner]" in system:
            content, delay = json.dumps(PLAN), 2.5
        elif "[agent:backend]" in system:
            content, delay = _files("backend"), 3.0
        elif "[agent:frontend]" in system:
            content, delay = _files("frontend"), 4.0
        elif "[agent:reviewer]" in system:
            content, delay = ("- The frontend calls a path that is not in the contract.\n"
                              "- Fix: use the exact contract path in frontend/api.js."), 1.0
        else:
            content, delay = "ok", 0.3
        await asyncio.sleep(delay * self.speed * random.uniform(0.7, 1.3))
        prompt_chars = sum(len(m.get("content") or "") for m in messages)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=prompt_chars // 4, completion_tokens=len(content) // 4))
