"""Visual QA loop: measured browser defects go to the Frontend agent; every repaired build must pass the full
deterministic review again (new green checkpoint), and a repair that breaks a required check is rolled back.
The browser is faked here (engine.agents.visual has its own live check in scripts); everything else is real."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import engine.agents.visual as visual
from engine.agents.frontend import FrontendAgent
from engine.agents.visual import PageReport, VisualReport
from engine.router import AccountLedger, ModelRouter, RouterConfig
from tests.fakes import ok, planner_reply
from tests.test_frontend_agent import CONTRACT, GOOD, GOOD_BACKEND, AgentAwareClient, FakeToolRunner

H = {"Authorization": "Bearer tok-for-tests-123"}


def report(defective: bool) -> VisualReport:
    page = PageReport("/", "desktop", "shot.png", 900, 0, False, 0, 0, 0 if defective else 1, 2 if defective else 0, 0, 0.9)
    return VisualReport(url="http://x", pages=[page])


class SequencedClient(AgentAwareClient):
    """Frontend answers in order (first build, then repairs); other agents as AgentAwareClient."""

    def __init__(self, answers, frontend_answers):
        super().__init__(answers)
        self.frontend_answers = list(frontend_answers)
        self.frontend_prompts: list[str] = []

    async def _route(self, **req):
        if "[agent:frontend]" in req["messages"][0]["content"]:
            self.calls.append(req)
            self.frontend_prompts.append(req["messages"][-1]["content"])
            return self.frontend_answers.pop(0) if len(self.frontend_answers) > 1 else self.frontend_answers[0]
        return await super()._route(**req)


def run_app(tmp_path, client):
    cfg = RouterConfig.load("config/profiles/web-light.toml", env={})
    app = create(tmp_path, client, cfg)
    c = TestClient(app)
    rid = c.post("/api/v1/runs", json={"prompt": "Build the arcade"}, headers=H).json()["id"]
    for _ in range(900):
        r = c.get(f"/api/v1/runs/{rid}", headers=H).json()
        if r["status"] in ("done", "failed", "stopped"):
            break
        time.sleep(0.2)
    return c, app, r


def create(tmp_path, client, cfg):
    from server.app import create_app
    return create_app(base_router=ModelRouter(cfg, client, account=AccountLedger(cfg)), data_dir=tmp_path / "runs",
                      runner=FakeToolRunner(), token="tok-for-tests-123", auth=True, serve_web=False,
                      frontend_agent=FrontendAgent)


@pytest.fixture
def fake_browser(monkeypatch):
    calls: list[Path] = []
    plan: list[bool] = []

    def inspect_workspace(ws, out, **kw):
        calls.append(Path(out))
        return report(plan.pop(0) if plan else False)
    monkeypatch.setattr(visual, "inspect_workspace", inspect_workspace)
    monkeypatch.setenv("AF_VISUAL_QA_ROUNDS", "2")
    return calls, plan


def answers():
    return {"planner": ok(planner_reply(CONTRACT.read_text(encoding="utf-8"))), "backend": ok(GOOD_BACKEND), "frontend": ok(GOOD)}


def test_defects_are_repaired_and_the_repair_is_re_verified_and_checkpointed(tmp_path, fake_browser):
    calls, plan = fake_browser
    plan.extend([True, False])  # round 1 finds defects, round 2 is clean
    client = SequencedClient(answers(), [ok(GOOD)])
    c, app, r = run_app(tmp_path, client)
    try:
        assert r["status"] == "done", r["error"]
        cps = c.get(f"/api/v1/runs/{r['id']}/checkpoints", headers=H).json()["checkpoints"]
        reviewer = next(s for s in r["stages"] if s["agent"] == "reviewer")
        assert [cp["stage_id"] for cp in cps].count(reviewer["id"]) == 2  # first approval + the re-verified repair
        assert len(calls) == 2 and any("visual QA" in p and "stretch across" in p for p in client.frontend_prompts)
        rec = app.state.runs.get(r["id"])
        assert rec.visual["round"] == 2 and rec.visual["defects"] == []
    finally:
        app.state.preview.stop_all()


def test_a_repair_that_breaks_a_required_check_is_rolled_back(tmp_path, fake_browser):
    calls, plan = fake_browser
    plan.extend([True, True, True])
    broken = GOOD + "\n=== FILE: frontend/src/Leak.tsx ===\nexport default function Leak() { fetch('/api/scores'); return null; }\n=== END FILE ===\n"
    client = SequencedClient(answers(), [ok(GOOD), ok(broken)])
    c, app, r = run_app(tmp_path, client)
    try:
        assert r["status"] == "done", r["error"]  # the verified build stands
        cps = c.get(f"/api/v1/runs/{r['id']}/checkpoints", headers=H).json()["checkpoints"]
        reviewer = next(s for s in r["stages"] if s["agent"] == "reviewer")
        assert [cp["stage_id"] for cp in cps].count(reviewer["id"]) == 1  # no green for the broken repair
        ws = app.state.runs.get(r["id"]).workspace
        assert not (ws / "frontend" / "src" / "Leak.tsx").exists()  # rolled back to the approved build
        review = c.get(f"/api/v1/runs/{r['id']}/review", headers=H).json()
        assert review["verdict"] == "approve"
        msgs = [json.dumps(e) for e in app.state.runs.get(r["id"]).events if e["type"] == "agent_message"]
        assert any("Visual repair broke a required check" in m for m in msgs)
    finally:
        app.state.preview.stop_all()


def test_visual_qa_can_be_disabled(tmp_path, fake_browser, monkeypatch):
    calls, _ = fake_browser
    monkeypatch.setenv("AF_VISUAL_QA_ROUNDS", "0")
    c, app, r = run_app(tmp_path, SequencedClient(answers(), [ok(GOOD)]))
    try:
        assert r["status"] == "done" and calls == []
    finally:
        app.state.preview.stop_all()
