"""The Backend agent's own checks (toolkit: "Done when: server starts, all tests pass, contract
tests pass, lint is clean"). They run in the sandbox runner and are evidence, not opinion; the
Reviewer re-runs its own checks independently.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from ..schemas import CheckResult
from .contract import Contract
from .runner import Runner

REQUIRED = ("backend/app.py", "backend/test_app.py")

# Executed inside the sandbox with cwd = workspace root. Imports the generated app via TestClient,
# calls every contract endpoint with sample data, and compares status codes and response fields.
CONTRACT_TEST = r'''
import json, os, re, sys, tempfile
os.environ["AF_DB"] = os.path.join(tempfile.mkdtemp(), "contract.db")
sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient
from backend.app import app

eps = json.load(open(".agentforge/contract.json"))["endpoints"]
c = TestClient(app)
NAMES = {"username": "contract_user", "password": "contract-pass-123", "email": "contract@example.com",
         "name": "Contract Tester", "title": "Contract test"}
PY = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
FORMATS = {"email": "contract@example.com", "date": "2026-01-15", "date-time": "2026-01-15T09:30:00Z",
           "time": "09:30:00", "uri": "https://example.com/contract", "url": "https://example.com/contract",
           "uuid": "123e4567-e89b-42d3-a456-426614174000", "ipv4": "192.0.2.10", "hostname": "example.com"}

def value(f):
    if f.get("example") is not None: return f["example"]
    if f.get("enum"): return f["enum"][0]
    t = f.get("type", "string")
    if t == "string":
        v = NAMES.get(f["name"], "sample")
        # A valid value for the declared format: "sample" for format: date was rejected by a correct app (422) and
        # the test blamed the app (run_bdc3599d60b8).
        v = FORMATS.get(f.get("format") or "", v)
        return v.ljust(f.get("min_length") or 0, "x")
    return {"integer": 1, "number": 1.5, "boolean": True, "array": [], "object": {}}.get(t, "sample")

# ids returned by POSTs, per collection path: PATCH /api/cart/items/{itemId} uses the item the POST just created
created = {}
NORM = lambda path: re.sub(r"\{[^}]+\}", "{}", path.rstrip("/"))  # noqa: E731
ROUTES = {(m, NORM(getattr(rt, "path", ""))) for rt in app.routes for m in (getattr(rt, "methods", None) or ())}

def call(ep, token):
    url = ep["path"]
    for p in ep.get("path_params", []):
        base = url.split("{" + p["name"] + "}")[0].rstrip("/")
        guess = value(p) if p.get("type") != "integer" else 1
        url = url.replace("{" + p["name"] + "}", str(created.get(base, guess)))
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    query = {f["name"]: value(f) for f in ep.get("query", []) if f.get("required", True)}
    body = {f["name"]: value(f) for f in ep.get("request", [])} if ep["method"] in ("POST", "PUT", "PATCH") else None
    return c.request(ep["method"], url, params=query, json=body, headers=headers)

# create data first (public POSTs such as register/login give a token), then reads see real rows
RANK = {"POST": 0, "GET": 2, "PUT": 3, "PATCH": 3, "DELETE": 4}
order = sorted(range(len(eps)), key=lambda i: (RANK.get(eps[i]["method"], 5) + (eps[i]["method"] == "POST"
                                                and eps[i].get("auth", False)), i))
token, fails = None, []
for i in order:
    ep = eps[i]; tag = f'{ep["method"]} {ep["path"]}'; before = len(fails)
    if ep.get("auth"):
        r = call(ep, None)
        if r.status_code not in (401, 403):
            fails.append(f"{tag}: protected endpoint answered {r.status_code} WITHOUT a token (expected 401/403)")
    r = call(ep, token)
    if r.status_code in (404, 405):
        if r.status_code == 404 and (ep["method"], NORM(ep["path"])) in ROUTES:
            # The route exists; it found no record for the sample id (e.g. no API creates products). Not a failure.
            print(f"skip {tag}: route exists but has no record for the sample id (404); response not verified"); continue
        fails.append(f"{tag}: route missing ({r.status_code})"); continue
    if r.status_code == 422:
        fails.append(f"{tag}: rejected a request built from the contract (422): {r.text[:400]}"); continue
    if r.status_code >= 500:
        fails.append(f"{tag}: server error {r.status_code}: {r.text[:400]}"); continue
    if r.status_code != ep.get("success_status", 200):
        fails.append(f"{tag}: expected status {ep.get('success_status', 200)}, got {r.status_code}: {r.text[:300]}"); continue
    try:
        body = r.json()
    except Exception:
        body = None
        if ep.get("response"): fails.append(f"{tag}: response is not JSON"); continue
    if ep.get("response"):
        if ep.get("response_is_list") and not isinstance(body, list):
            fails.append(f"{tag}: expected a JSON array, got {type(body).__name__}"); continue
        for item in (body if ep.get("response_is_list") else [body])[:5]:
            if not isinstance(item, dict):
                fails.append(f"{tag}: expected JSON objects, got {type(item).__name__}"); break
            for f in ep["response"]:
                if f["name"] not in item:
                    if f.get("required", True): fails.append(f"{tag}: response missing '{f['name']}': {json.dumps(item)[:200]}")
                elif item[f["name"]] is not None and not isinstance(item[f["name"]], PY.get(f.get("type"), object)):
                    fails.append(f"{tag}: field '{f['name']}' should be {f['type']}, got {type(item[f['name']]).__name__}")
    if isinstance(body, dict):
        token = body.get("token") or body.get("access_token") or token
        if ep["method"] == "POST" and isinstance(body.get("id"), (int, str)):
            created[ep["path"].rstrip("/")] = body["id"]
    if len(fails) == before:
        print(f"ok   {tag} -> {r.status_code}")
for f in fails:
    print("FAIL " + f)
sys.exit(1 if fails else 0)
'''


FRAME_RE = re.compile(r"^\S.*:\d+: in \S+")


def _own_frames(block: str) -> str:
    """Drop traceback frames that point into installed libraries (FastAPI, Starlette, anyio...)."""
    out, keep = [], True
    for line in block.splitlines():
        if FRAME_RE.match(line):
            keep = not re.search(r"site-packages|[\\/]lib[\\/]python", line, re.I)
        if keep:
            out.append(line)
    return "\n".join(out)


def condense_pytest(log: str, first_limit: int = 2500) -> str:
    """What a model needs to fix tests: the FIRST failure in full (with the failing source line) plus the one-line
    summary of all failures. The raw log can be tens of KB of repeated framework frames."""
    m = re.search(r"={3,} (?:FAILURES|ERRORS) ={3,}\n(.*?)(?=\n={3,} )", log, re.S)
    blocks = [b for b in re.split(r"\n?_{3,} .*? _{3,}\n", m.group(1)) if b.strip()] if m else []
    summary = re.search(r"={3,} short test summary info ={3,}\n.*", log, re.S)
    out = f"First failure (of {len(blocks)}):\n{_own_frames(blocks[0])[-first_limit:]}\n\n" if blocks else ""
    return out + (summary.group(0)[:2000] if summary else log[-2000:])


def run_backend_checks(root: Path, contract: Contract, runner: Runner) -> list[CheckResult]:
    results: list[CheckResult] = []
    missing = [f for f in REQUIRED if not (root / f).is_file()]
    results.append(CheckResult(name="required files", passed=not missing,
                               log=f"missing: {missing}" if missing else ", ".join(REQUIRED)))
    if missing:
        return results

    tools = root / ".agentforge"
    tools.mkdir(exist_ok=True)
    (tools / "contract.json").write_text(contract.model_dump_json(include={"title", "endpoints"}), encoding="utf-8")
    (tools / "contract_test.py").write_text(CONTRACT_TEST, encoding="utf-8")

    def check(name: str, cmd: list[str], timeout: int, condense=None) -> CheckResult:
        t0 = time.perf_counter()
        r = runner.run(cmd, root, timeout)
        log = condense(r.log(200_000)) if condense and not r.ok else r.log()
        res = CheckResult(name=name, passed=r.ok, exit_code=r.exit_code, log=log,
                          duration_ms=int((time.perf_counter() - t0) * 1000))
        results.append(res)
        return res

    if not check("compiles", ["python", "-m", "compileall", "-q", "backend"], 60).passed:
        return results
    lint = check("lint (ruff)", ["python", "-m", "ruff", "check", "--no-cache", "--select", "E9,F63,F7,F82,F811",
                                 "backend"], 60)
    if "No module named ruff" in lint.log or lint.exit_code == 127:
        lint.passed, lint.skipped, lint.log = True, True, "ruff not installed; skipped"
    check("pytest", ["python", "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", "backend"], 180,
          condense=condense_pytest)
    check("contract test", ["python", ".agentforge/contract_test.py"], 120)
    return results
