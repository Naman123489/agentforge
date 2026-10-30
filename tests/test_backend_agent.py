import asyncio
import shutil
from pathlib import Path

import pytest

from engine.agents.backend import BackendAgent, parse_output
from engine.agents.contract import load_contract
from engine.agents.runner import LocalRunner
from engine.router import ModelRouter, RouterConfig, Slot
from engine.schemas import CheckResult, TaskAssignment
from tests.fakes import FakeAPIError, FakeClient, ok

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "extras" / "demo_app" / "backend"
CONTRACT = ROOT / "examples" / "arcade_openapi.yaml"
LIGHTNING = "nvidia/Nemotron-3_5-Lightning"


def files(**overrides) -> str:
    out = []
    for p in sorted(REF.glob("*.py")):
        body = overrides.get(p.name, p.read_text(encoding="utf-8"))
        out.append(f"=== FILE: backend/{p.name} ===\n{body}\n=== END FILE ===")
    return "\n\n".join(out) + "\n=== NOTES ===\nsqlite + token table\n=== END NOTES ==="


GOOD = files()
AUTH_HOLE = files(**{"app.py": (REF / "app.py").read_text().replace(
    "    user = _current_user(authorization)\n", "    user = 'anonymous'\n")})


def make(*responses, max_iterations=3):
    cfg = RouterConfig.load("config/profiles/web-light.toml", env={})
    client = FakeClient()
    client.script(LIGHTNING, *responses)
    router = ModelRouter(cfg, client, run_id="t")
    return BackendAgent(router, LocalRunner(), max_iterations=max_iterations), client


def task(**kw) -> TaskAssignment:
    kw.setdefault("contract_ref", str(CONTRACT))
    return TaskAssignment(stage_id="backend-1", agent="backend", instructions="Build the arcade backend.", **kw)


def run(agent, t, tmp_path):
    return asyncio.run(agent.run(t, tmp_path))


def test_contract_loader_normalises_openapi():
    c = load_contract(CONTRACT)
    by = {f"{e.method} {e.path}": e for e in c.endpoints}
    assert by["POST /api/scores"].auth and not by["POST /api/login"].auth
    assert by["GET /api/leaderboard"].response_is_list
    assert [f.name for f in by["POST /api/scores"].request] == ["game", "score"]
    assert by["POST /api/scores"].request[0].enum == ["snake", "2048", "memory"]


def test_builds_backend_that_passes_its_own_checks(tmp_path):
    agent, client = make(ok(GOOD, 3000, 2500))
    r = run(agent, task(), tmp_path)
    assert r.status == "done" and r.iterations == 1, [c.log for c in r.checks if not c.passed]
    assert {c.name for c in r.checks} >= {"compiles", "pytest", "contract test"}
    assert r.files_changed == ["backend/app.py", "backend/test_app.py"]
    assert r.models_used == [f"B:{LIGHTNING}"] and r.notes == ["sqlite + token table"]
    assert r.tokens == 5500 and r.cost_usd > 0
    c = next(c for c in r.checks if c.name == "pytest")
    assert c.exit_code == 0 and c.duration_ms > 0


def test_fixes_from_executed_failures(tmp_path):
    agent, client = make(ok(AUTH_HOLE), ok(GOOD))
    r = run(agent, task(), tmp_path)
    assert r.status == "done" and r.iterations == 2
    fix_prompt = client.calls[1]["messages"][-1]["content"]
    assert "EXECUTED" in fix_prompt and "WITHOUT a token" in fix_prompt  # real contract-test output
    assert "backend/app.py" in r.files_changed


def test_repeated_failures_escalate_to_premium_model(tmp_path):
    agent, client = make(ok(AUTH_HOLE), ok(AUTH_HOLE), ok(GOOD))
    r = run(agent, task(), tmp_path)
    assert r.status == "done" and r.iterations == 3
    assert r.models_used == [f"B:{LIGHTNING}", f"A:{LIGHTNING}"]
    thinking = [c["extra_body"]["chat_template_kwargs"] for c in client.calls]
    assert thinking[-1] == {"enable_thinking": True}  # slot A = full reasoning


def test_gives_up_after_max_iterations(tmp_path):
    agent, _ = make(ok(AUTH_HOLE), ok(AUTH_HOLE), max_iterations=2)
    r = run(agent, task(), tmp_path)
    assert r.status == "failed" and r.iterations == 2
    assert any(c.name == "contract test" and not c.passed for c in r.checks)


def test_cannot_write_outside_backend(tmp_path):
    sneaky = GOOD + "\n=== FILE: frontend/app.js ===\nalert(1)\n=== END FILE ==="
    agent, client = make(ok(sneaky), ok(GOOD))
    r = run(agent, task(), tmp_path)
    assert not (tmp_path / "frontend").exists()
    assert r.status == "done" and r.iterations == 2
    assert "may only write backend/**" in client.calls[1]["messages"][-1]["content"]


def test_files_allowed_is_enforced(tmp_path):
    extra = GOOD + "\n=== FILE: backend/secrets.py ===\nX = 1\n=== END FILE ==="
    agent, _ = make(ok(extra), ok(GOOD))
    r = run(agent, task(files_allowed=["backend/app.py", "backend/test_app.py"]), tmp_path)
    assert r.status == "done" and not (tmp_path / "backend" / "secrets.py").exists()


def test_contract_change_request(tmp_path):
    agent, _ = make(ok("=== CONTRACT_CHANGE_REQUEST ===\nproblem: scores has no game list\n"
                       "proposed_change: add enum to ScoreIn.game\n=== END ==="))
    r = run(agent, task(), tmp_path)
    assert r.status == "contract_change_requested"
    assert r.contract_change.problem == "scores has no game list"
    app = (tmp_path / "backend" / "app.py").read_text()
    assert "ROUTES: implement every endpoint" in app and "@app." not in app  # only the seeded template


def test_invalid_contract_is_rejected_without_calling_a_model(tmp_path):
    bad = tmp_path / "bad.yaml"
    # A declared request body with no usable JSON object schema (a POST with no body at all is valid since 30 Oct).
    bad.write_text("openapi: 3.1.0\ninfo: {title: x, version: '1'}\npaths:\n  /api/x:\n    post:\n"
                   "      requestBody: {content: {application/json: {schema: {type: object}}}}\n"
                   "      responses: {'200': {description: ok}}\n")
    agent, client = make()
    r = run(agent, task(contract_ref=str(bad)), tmp_path / "ws")
    assert r.status == "contract_change_requested" and not client.calls


def test_router_stop_returns_stopped(tmp_path):
    agent, _ = make(FakeAPIError(401, "bad key"))
    r = run(agent, task(), tmp_path)
    assert r.status == "stopped" and any("auth_failed" in n for n in r.notes)


def test_reviewer_failures_start_in_fix_mode(tmp_path):
    shutil.copytree(REF, tmp_path / "backend")
    agent, client = make(ok(GOOD))
    t = task(failed_attempts=1, reviewer_failures=[CheckResult(name="integration", passed=False,
                                                               log="frontend sends 'points', contract says 'score'")])
    r = run(agent, t, tmp_path)
    assert r.status == "done"
    first = client.calls[0]["messages"][-1]["content"]
    assert "contract says 'score'" in first and "Current backend files" in first
    assert r.files_changed == ["backend/app.py", "backend/test_app.py"]
    assert t.reviewer_failures[0].startswith("integration: ")  # contract form: list of strings


def test_parse_output_handles_think_tags_and_fences():
    text = "<think>plan</think>=== FILE: backend/app.py ===\n```python\nx = 1\n```\n=== END FILE ==="
    files, notes, ccr = parse_output(text)
    assert files == {"backend/app.py": "x = 1\n"} and ccr is None


@pytest.mark.parametrize("text", [
    "=== FILE: backend/app.py ===\nx = 1\n=== END FILE: backend/app.py ===",            # END with path
    "=== FILE: backend/app.py ===\nx = 1\n=== NOTES ===\nn\n=== END NOTES ===",          # no END, then notes
    "Here you go:\n=== FILE: backend/app.py ===\nx = 1",                                  # truncated answer
    "### `backend/app.py`\n```python\nx = 1\n```\n",                                     # markdown fallback
])
def test_parse_output_tolerates_format_variations(text):
    files, _, _ = parse_output(text)
    assert files == {"backend/app.py": "x = 1\n"}


def test_regression_is_reverted_and_best_version_kept(tmp_path):
    broken = files(**{"app.py": "this is not python\n"})
    agent, client = make(ok(AUTH_HOLE), ok(broken), ok(GOOD))
    r = run(agent, task(), tmp_path)
    assert r.status == "done" and r.iterations == 3
    third_prompt = client.calls[2]["messages"][-1]["content"]
    assert "was reverted" in third_prompt
    assert "this is not python" not in third_prompt  # model sees the best (reverted) files, not the broken ones


def test_failed_run_keeps_best_version_on_disk(tmp_path):
    broken = files(**{"app.py": "this is not python\n"})
    agent, _ = make(ok(AUTH_HOLE), ok(broken), max_iterations=2)
    r = run(agent, task(), tmp_path)
    assert r.status == "failed"
    assert "_current_user" in (tmp_path / "backend" / "app.py").read_text()  # the better (auth-hole) version
    assert any(c.name == "pytest" for c in r.checks)


def test_contract_test_sample_values_respect_string_formats():
    """The contract test must send values a correct app accepts (format: date got "sample" -> 422, blamed on the app)."""
    import datetime as dt
    import re
    import uuid
    from engine.agents.backend_checks import CONTRACT_TEST
    src = CONTRACT_TEST[CONTRACT_TEST.index("NAMES = "):CONTRACT_TEST.index("# ids returned by POSTs")]
    ns: dict = {}
    exec(src, ns)  # noqa: S102 - our own script text, defines NAMES/FORMATS/value
    value = ns["value"]
    dt.date.fromisoformat(value({"name": "due_date", "type": "string", "format": "date"}))
    dt.datetime.fromisoformat(value({"name": "at", "type": "string", "format": "date-time"}).replace("Z", "+00:00"))
    uuid.UUID(value({"name": "id", "type": "string", "format": "uuid"}))
    assert re.match(r"^https://", value({"name": "link", "type": "string", "format": "uri"}))
    assert value({"name": "email", "type": "string", "format": "email"}) == "contract@example.com"
    assert value({"name": "title", "type": "string"}) == "Contract test"  # unchanged without a format
    assert value({"name": "d", "type": "string", "format": "date", "example": "2025-12-31"}) == "2025-12-31"


def test_contract_test_reuses_created_ids_and_tolerates_404_from_an_existing_route(tmp_path):
    """run_f7e74dd288f1: GET /api/products/{id} (no API creates products) and PATCH /api/cart/items/{itemId}
    (sample id 1, not the created item) were reported as 'route missing' 4 times on a correct backend."""
    import json as _json
    import subprocess as sp
    import sys as _sys
    import yaml as _yaml
    from engine.agents.backend_checks import CONTRACT_TEST
    from engine.agents.contract import load_contract
    obj = {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}, "qty": {"type": "integer"}}}
    resp = {"content": {"application/json": {"schema": obj}}}
    spec = {"openapi": "3.1.0", "info": {"title": "Shop", "version": "1"}, "paths": {
        "/api/products/{id}": {"get": {"parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                                       "responses": {"200": resp}}},
        "/api/cart/items": {"post": {"requestBody": {"content": {"application/json": {"schema": {
            "type": "object", "required": ["qty"], "properties": {"qty": {"type": "integer"}}}}}}, "responses": {"201": resp}}},
        "/api/cart/items/{itemId}": {"patch": {"parameters": [{"name": "itemId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                                               "requestBody": {"content": {"application/json": {"schema": {
                                                   "type": "object", "required": ["qty"], "properties": {"qty": {"type": "integer"}}}}}},
                                               "responses": {"200": resp}}}}}
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "__init__.py").write_text("")
    (tmp_path / "backend" / "app.py").write_text('''
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
app = FastAPI()
items = {}
class Qty(BaseModel):
    qty: int
@app.get("/api/products/{id}")
def product(id: int):
    raise HTTPException(404, "Product not found")
@app.post("/api/cart/items", status_code=201)
def add(b: Qty):
    i = 41 + len(items); items[i] = b.qty; return {"id": i, "qty": b.qty}
@app.patch("/api/cart/items/{itemId}")
def update(itemId: int, b: Qty):
    if itemId not in items: raise HTTPException(404, "Item not found")
    items[itemId] = b.qty; return {"id": itemId, "qty": b.qty}
''')
    (tmp_path / ".agentforge").mkdir()
    (tmp_path / ".agentforge" / "contract.json").write_text(_json.dumps(load_contract(spec).model_dump(mode="json")))
    r = sp.run([_sys.executable, "-c", CONTRACT_TEST], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "skip GET /api/products/{id}: route exists" in r.stdout and "ok   PATCH /api/cart/items/{itemId}" in r.stdout
    (tmp_path / "backend" / "app.py").write_text((tmp_path / "backend" / "app.py").read_text().replace(
        '@app.patch("/api/cart/items/{itemId}")', '@app.patch("/api/cart/item/{itemId}")'))
    r = sp.run([_sys.executable, "-c", CONTRACT_TEST], cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert r.returncode == 1 and "PATCH /api/cart/items/{itemId}: route missing (404)" in r.stdout  # a really missing route still fails
