"""Fake OpenAI-compatible client, clock and errors for router tests."""

from __future__ import annotations

from collections import defaultdict, deque
from types import SimpleNamespace

from engine.router import RouterConfig
from engine.router.config import DEFAULT_CONFIG_PATH


class FakeAPIError(Exception):
    def __init__(self, status_code: int, msg: str = "error", retry_after: float | None = None):
        super().__init__(msg)
        self.status_code = status_code
        headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
        self.response = SimpleNamespace(headers=headers)


def ok(content: str = "ok", prompt_tokens: int = 1000, completion_tokens: int = 500):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


class FakeClient:
    """Per-model scripts: a deque of responses or exceptions; `default` when empty."""

    def __init__(self, default=None):
        self.scripts: dict[str, deque] = defaultdict(deque)
        self.default = default if default is not None else ok()
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def script(self, model_id: str, *items) -> "FakeClient":
        self.scripts[model_id].extend(items)
        return self

    async def _create(self, **req):
        self.calls.append(req)
        q = self.scripts[req["model"]]
        item = q.popleft() if q else self.default
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    def models_called(self) -> list[str]:
        return [c["model"] for c in self.calls]


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.t += s


def load_cfg(**budget) -> RouterConfig:
    """Config from the real models.toml with known prices on every slot (deterministic tests)."""
    env = {"AF_MODEL_A_PRICE_IN": "1.0", "AF_MODEL_A_PRICE_OUT": "3.0"}
    cfg = RouterConfig.load(DEFAULT_CONFIG_PATH, env=env)
    for k, v in budget.items():
        setattr(cfg.budget, k, v)
    return cfg


def planner_reply(contract_yaml: str) -> str:
    """A valid Planner reply ({plan, openapi_spec} JSON) for an existing contract: one backend and one frontend task
    covering every operation, frontend independent of backend."""
    import json

    import yaml

    spec = yaml.safe_load(contract_yaml)
    ops = [f"{m.upper()} {p}" for p, item in spec["paths"].items() for m in item
           if m.lower() in ("get", "post", "put", "patch", "delete")]
    plan = {"project_name": "test-app", "summary": "Scripted plan for tests.", "requirements": ["the contract's features"],
            "assumptions": [], "open_questions": [], "technology_stack": ["FastAPI", "SQLite", "React", "TypeScript", "Vite"],
            "tasks": [{"task_id": "T1", "title": "API", "owner": "backend", "description": "Implement every endpoint.",
                       "endpoints": ops, "dependencies": [], "acceptance_checks": ["pytest passes"], "files_allowed": ["backend/app.py"]},
                      {"task_id": "T2", "title": "UI", "owner": "frontend", "description": "Pages for every endpoint.",
                       "endpoints": ops, "dependencies": [], "acceptance_checks": ["vite build passes"], "files_allowed": ["frontend/src/App.tsx"]}],
            "risks": []}
    return json.dumps({"plan": plan, "openapi_spec": spec})
