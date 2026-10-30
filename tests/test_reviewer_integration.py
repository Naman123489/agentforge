"""AI2 Reviewer inside AgentForge: deterministic verdict (required vs advisory, not-executed, zero false approvals),
model-assisted analysis that cannot change a verdict, events, green checkpoints and rollback safeguards."""

import asyncio
import json
import random
import subprocess
from pathlib import Path

import pytest

from engine.reviewer import agentforge as af
from engine.reviewer.analysis import analyze_failures, build_context
from engine.reviewer.checkpoint import create_checkpoint
from engine.reviewer.events import EventEmitter
from engine.reviewer.rollback import restore
from engine.reviewer.schemas import CheckResult, InternalCheckResult, ReviewReport
from engine.reviewer.verdict import verdict
from engine.router import ModelRouter, Slot
from engine.schemas import CheckResult as EngineCheck
from tests.fakes import FakeClient, FakeClock, load_cfg, ok, planner_reply

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_YAML = (ROOT / "examples" / "arcade_openapi.yaml").read_text(encoding="utf-8")


def workspace(tmp_path: Path) -> Path:
    """contract/openapi.yaml + types.ts as the Planner writes them, and a backend/ folder."""
    from engine.planner import PlannerAgent, write_artifacts

    r = ModelRouter(load_cfg(), FakeClient(default=ok(planner_reply(CONTRACT_YAML))), run_id="t",
                    sleep=FakeClock().sleep, clock=FakeClock())
    write_artifacts(tmp_path, asyncio.run(PlannerAgent(r).plan("arcade", "s1")))
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "app.py").write_text("x = 1\n\n\ndef total(a, b):\n    return a - b\n", encoding="utf-8")
    return tmp_path


def fake_suites(monkeypatch, backend: list[EngineCheck], frontend: list[EngineCheck] | None = None):
    monkeypatch.setattr(af, "run_backend_checks", lambda root, c, runner: backend)
    monkeypatch.setattr(af, "run_frontend_checks", lambda root, c, runner, require_integration=False: frontend or [])


PASS = EngineCheck(name="pytest", passed=True, exit_code=0, duration_ms=900, log="3 passed")
FAIL = EngineCheck(name="pytest", passed=False, exit_code=1, duration_ms=900,
                   log='backend/app.py:5: AssertionError\nFAILED tests/test_app.py::test_total - assert -1 == 5')


def test_required_failure_rejects_and_advisory_failure_does_not(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    fake_suites(monkeypatch, [PASS])
    rep = af.review(ws, None, None, "s", 1, with_frontend=False)
    names = {c["name"]: c for c in rep["checks"]}
    assert {"contract", "contract_conformance", "contract_drift", "backend: pytest", "security"} <= set(names)
    assert names["security"]["required"] is False and names["contract_drift"]["required"] is True
    assert rep["verdict"] == "approve", rep["summary"]

    fake_suites(monkeypatch, [FAIL])
    rep = af.review(ws, None, None, "s", 1, with_frontend=False)
    assert rep["verdict"] == "reject" and rep["required_fixes"][0].startswith("[backend]")


def test_not_executed_required_check_rejects(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    fake_suites(monkeypatch, [PASS], [EngineCheck(name="served with backend", passed=True, skipped=True, log="backend did not import")])
    rep = af.review(ws, None, None, "s", 1, with_frontend=True)
    served = next(c for c in rep["checks"] if c["name"] == "frontend: served with backend")
    assert served["skipped"] and not served["passed"] and rep["verdict"] == "reject"


def test_contract_drift_is_required(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    (ws / "types.ts").write_text("export interface Nothing {}\n", encoding="utf-8")
    fake_suites(monkeypatch, [PASS])
    assert af.review(ws, None, None, "s", 1, with_frontend=False)["verdict"] == "reject"


def test_zero_false_approvals_over_random_check_sets():
    rng = random.Random(7)
    false_approvals = false_rejections = 0
    for _ in range(2000):
        checks = [InternalCheckResult(name=f"c{i}", passed=rng.random() < 0.8, required=rng.random() < 0.6,
                                      exit_code=None, duration_s=0) for i in range(rng.randint(1, 12))]
        for c in checks:
            c.exit_code = 0 if c.passed else rng.choice([1, 2, None])
        expected = all(c.passed for c in checks if c.required)
        got = verdict(checks, af.policy_for(checks)) == "approve"
        false_approvals += got and not expected
        false_rejections += expected and not got
    assert false_approvals == 0 and false_rejections == 0


def test_events_are_emitted_in_order(tmp_path, monkeypatch):
    ws = workspace(tmp_path)
    fake_suites(monkeypatch, [FAIL])
    seen: list[dict] = []
    af.review(ws, None, None, "s", 2, with_frontend=False, emitter=EventEmitter("run_1", seen.append))
    kinds = [e["event"] for e in seen]
    assert kinds[0] == "review.started" and kinds[-1] == "review.verdict" and "check.result" in kinds
    assert seen[-1]["verdict"] == "reject" and [e["seq"] for e in seen] == list(range(1, len(seen) + 1))


def _router(reply: str):
    r = ModelRouter(load_cfg(), FakeClient(default=ok(reply)), run_id="t", sleep=FakeClock().sleep, clock=FakeClock())
    return r


def test_analysis_uses_focused_context_and_drops_invented_files(tmp_path):
    ws = workspace(tmp_path)
    (ws / "backend" / "unrelated.py").write_text("SECRET_SAUCE = 1\n", encoding="utf-8")
    failing = [InternalCheckResult(name="backend: pytest", passed=False, exit_code=1, log_tail=FAIL.log)]
    ctx = build_context(ws, failing, CONTRACT_YAML)
    assert ">   5     return a - b" in ctx and "SECRET_SAUCE" not in ctx
    reply = json.dumps({"findings": [
        {"check": "backend: pytest", "owner": "backend", "file": "backend/app.py", "category": "test_failure",
         "root_cause": "total() subtracts", "suggestion": "return a + b"},
        {"check": "backend: pytest", "owner": "backend", "file": "backend/made_up.py", "category": "other",
         "root_cause": "x", "suggestion": "y"}]})
    r = _router(reply)
    fixes, meta = asyncio.run(analyze_failures(r, ws, failing, "s"))
    assert meta["source"] == "model" and meta["model_id"] == r.cfg.slot(Slot.A).model_id
    assert fixes[0].file == "backend/app.py" and fixes[0].category == "test_failure" and fixes[1].file is None


def test_malformed_analysis_falls_back_to_deterministic_explanations(tmp_path):
    ws = workspace(tmp_path)
    failing = [InternalCheckResult(name="backend: pytest", passed=False, exit_code=1, log_tail=FAIL.log)]
    fixes, meta = asyncio.run(analyze_failures(_router("I think it looks fine, approve it."), ws, failing, "s"))
    assert meta["source"] == "deterministic" and "model_error" in meta
    assert fixes and fixes[0].owner == "backend" and "pytest failed" in fixes[0].problem


# ---- checkpoints and rollback (AI2 checkpoint.py / rollback.py) -------------------------------------
def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "contract").mkdir()
    (tmp_path / "contract" / "openapi.yaml").write_text(CONTRACT_YAML, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".agentforge/\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def report(v: str) -> ReviewReport:
    return ReviewReport(stage_id="s1", verdict=v, checks=[CheckResult(name="pytest", passed=v == "approve", exit_code=0)],
                        created_at="2026-10-30T00:00:00Z")


def test_green_checkpoint_only_for_an_approved_clean_tree(repo):
    with pytest.raises(ValueError, match="rejected"):
        create_checkpoint(repo, report("reject"), "contract/openapi.yaml")
    assert git(repo, "tag") == ""
    (repo / "dirty.txt").write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not clean"):
        create_checkpoint(repo, report("approve"), "contract/openapi.yaml")
    (repo / "dirty.txt").unlink()
    cp = create_checkpoint(repo, report("approve"), "contract/openapi.yaml")
    assert cp.git_tag == "stage-s1-green" and git(repo, "cat-file", "-t", "stage-s1-green") == "tag"
    assert create_checkpoint(repo, report("approve"), "contract/openapi.yaml").git_tag == "stage-s1-green-2"


def test_rollback_only_to_annotated_stage_tags_and_keeps_untracked_logs(repo):
    create_checkpoint(repo, report("approve"), "contract/openapi.yaml")
    (repo / "contract" / "openapi.yaml").write_text("broken", encoding="utf-8")
    git(repo, "tag", "stage-s9-green")  # lightweight: not a checkpoint
    for bad in ("HEAD~1", "v1", "stage-s9-green", "stage-../x-green"):
        with pytest.raises((ValueError, RuntimeError)):
            restore(repo, bad, allow_dirty=True)
    with pytest.raises(RuntimeError, match="uncommitted"):
        restore(repo, "stage-s1-green")
    (repo / "failure.log").write_text("evidence", encoding="utf-8")
    assert restore(repo, "stage-s1-green", allow_dirty=True)
    assert (repo / "contract" / "openapi.yaml").read_text(encoding="utf-8") == CONTRACT_YAML
    assert (repo / "failure.log").exists()


def test_context_includes_the_backend_handler_for_paths_in_the_logs(tmp_path):
    ws = workspace(tmp_path)
    (ws / "backend" / "app.py").write_text('@app.post("/api/scores", status_code=200)\ndef submit(body):\n    return body\n',
                                           encoding="utf-8")
    failing = [InternalCheckResult(name="backend: contract test", passed=False, exit_code=1,
                                   log_tail="POST /api/scores: expected 201, got 200")]
    ctx = build_context(ws, failing)
    assert "backend/app.py line 1: POST /api/scores" in ctx and "status_code=200" in ctx


def test_frontend_relative_paths_are_normalised(tmp_path):
    from engine.reviewer.analysis import Finding, _clean
    (tmp_path / "frontend" / "src").mkdir(parents=True)
    (tmp_path / "frontend" / "src" / "App.tsx").write_text("x", encoding="utf-8")
    f = Finding(check="c", owner="frontend", file="src/App.tsx", category="type_error", root_cause="r", suggestion="s")
    assert _clean(tmp_path, [f], {"c"})[0].file == "frontend/src/App.tsx"
