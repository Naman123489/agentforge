"""Reviewer checks: executed evidence, never an LLM opinion (FR-5).

backend      files present, compiles, its own pytest suite, contract test against the real app
frontend     files present, scripts referenced by index.html exist, JS syntax (node --check)
integration  every API call in the frontend matches a contract path; contract endpoints are used;
             POST body field names used by the frontend match the contract
"""

from __future__ import annotations

import json
import re
import shutil

from . import sandbox
from .schemas import Check, Contract
from .workspace import Workspace

CHECKER = r'''
import json, os, sys, tempfile
os.environ.setdefault("AF_DB", os.path.join(tempfile.mkdtemp(), "contract.db"))
sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient
from backend.app import app

contract = json.load(open(".agentforge/contract.json"))
c = TestClient(app)
SAMPLE = {"string": "demo", "integer": 42, "number": 4.5, "boolean": True, "array": [], "object": {}}

def sample(fields):
    out = {}
    for f in fields:
        v = SAMPLE[f.get("type", "string")]
        if f.get("type", "string") == "string":
            v = {"username": "demo_user", "password": "demo-pass-123", "game": "snake"}.get(f["name"], "demo")
        out[f["name"]] = v
    return out

def fill(path):
    import re
    return re.sub(r"\{[^}]+\}", "1", path)

token, failures, order = None, [], sorted(contract["endpoints"], key=lambda e: (e.get("auth", False), e["method"] != "POST"))
for ep in order:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = fill(ep["path"])
    if ep["method"] == "GET":
        r = c.get(url, params=sample(ep.get("query", [])), headers=headers)
    else:
        r = c.request(ep["method"], url, json=sample(ep.get("request", [])), headers=headers)
    tag = f'{ep["method"]} {ep["path"]}'
    if r.status_code in (404, 405):
        failures.append(f"{tag}: route missing ({r.status_code})"); continue
    if r.status_code == 422:
        failures.append(f"{tag}: rejected the contract's request fields (422): {r.text[:300]}"); continue
    if r.status_code >= 500:
        failures.append(f"{tag}: server error {r.status_code}: {r.text[:300]}"); continue
    if r.status_code == 401 and ep.get("auth"):
        failures.append(f"{tag}: 401 even with a token from the login/register endpoint"); continue
    if 200 <= r.status_code < 300 and ep.get("response"):
        body = r.json()
        items = body if ep.get("response_is_list") else [body]
        if ep.get("response_is_list") and not isinstance(body, list):
            failures.append(f"{tag}: expected a JSON list, got {type(body).__name__}"); continue
        for item in items[:3]:
            missing = [f["name"] for f in ep["response"] if f.get("required", True) and f["name"] not in item]
            if missing:
                failures.append(f"{tag}: response missing fields {missing}: {json.dumps(item)[:200]}"); break
    if isinstance(r.json() if r.content else None, dict):
        token = r.json().get("token") or r.json().get("access_token") or token
    print(f"ok   {tag} -> {r.status_code}")
for f in failures:
    print("FAIL " + f)
sys.exit(1 if failures else 0)
'''

URL_RE = re.compile(r"""["'`]\.?/?(api/[A-Za-z0-9_\-/{}$.]*)""")


def _path_regex(path: str) -> re.Pattern:
    p = re.escape(path.lstrip("/"))
    p = re.sub(r"\\\{[^}]+\\\}", r"[^/]+", p)
    return re.compile("^" + p + "/?$")


def run_checks(ws: Workspace, contract: Contract, only: set[str] | None = None) -> list[Check]:
    only = only or {"backend", "frontend", "integration"}
    checks: list[Check] = []
    ws.write(".agentforge/contract.json", contract.model_dump_json(indent=2))
    ws.write(".agentforge/contract_check.py", CHECKER)

    if "backend" in only:
        missing = [f for f in ("backend/app.py", "backend/test_app.py") if f not in ws.files("backend/")]
        checks.append(Check(name="backend: required files", owner="backend", passed=not missing,
                            log=f"missing: {missing}" if missing else "backend/app.py, backend/test_app.py"))
        if not missing:
            r = sandbox.run(["python", "-m", "compileall", "-q", "backend"], ws.root, 60)
            checks.append(Check(name="backend: compiles", owner="backend", passed=r.ok, log=r.log()))
            r = sandbox.run(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "backend"], ws.root, 180)
            checks.append(Check(name="backend: pytest", owner="backend", passed=r.ok, log=r.log()))
            r = sandbox.run(["python", ".agentforge/contract_check.py"], ws.root, 120)
            checks.append(Check(name="backend: contract test", owner="backend", passed=r.ok, log=r.log()))

    js_files = [f for f in ws.files("frontend/") if f.endswith(".js")]
    if "frontend" in only:
        problems = []
        if "frontend/index.html" not in ws.files("frontend/"):
            problems.append("frontend/index.html missing")
        else:
            html = ws.read("frontend/index.html")
            for ref in re.findall(r"""<(?:script[^>]+src|link[^>]+href)=["']([^"']+)["']""", html):
                if not ref.startswith(("http:", "https:", "//", "data:")) and \
                        f"frontend/{ref.lstrip('./')}" not in ws.files("frontend/"):
                    problems.append(f"index.html references missing file {ref}")
        checks.append(Check(name="frontend: files and references", owner="frontend", passed=not problems,
                            log="\n".join(problems) or f"{len(ws.files('frontend/'))} files"))
        logs, ok = [], True
        for f in js_files:
            if re.search(r"^\s*(import|export)\s", ws.read(f), re.M):
                ok = False
                logs.append(f"{f}: uses ES modules; use plain <script> files (no import/export)")
                continue
            r = sandbox.run(["node", "--check", f], ws.root, 30)
            if r.exit_code == 127:
                logs.append("node not available in sandbox; JS syntax check skipped")
                break
            if not r.ok:
                ok = False
                logs.append(f"{f}:\n{r.log(1500)}")
        checks.append(Check(name="frontend: JS syntax", owner="frontend", passed=ok,
                            log="\n".join(logs) or f"{len(js_files)} files ok"))

    if "integration" in only:
        js = {f: ws.read(f) for f in js_files}
        js["frontend/index.html"] = ws.read("frontend/index.html") if "frontend/index.html" in ws.files() else ""
        regexes = [(e, _path_regex(e.path)) for e in contract.endpoints]
        unknown, used = [], set()
        for f, src in js.items():
            for url in URL_RE.findall(src):
                url = re.sub(r"\$\{[^}]*\}", "x", url.split("?")[0]).rstrip("/")
                hits = [e for e, rx in regexes if rx.match(url) or (url.endswith("/") and rx.match(url + "x"))]
                if hits:
                    used.update(e.path for e in hits)
                elif url not in ("api", "api/"):
                    unknown.append(f"{f}: calls '{url}' which is not in the contract")
        unused = [f"{e.method} {e.path}" for e in contract.endpoints if e.path not in used]
        checks.append(Check(name="integration: API calls match contract", owner="frontend",
                            passed=not unknown and not unused,
                            log="\n".join(unknown + [f"contract endpoint never called by frontend: {u}" for u in unused])
                            or f"{len(used)} endpoints used, all in contract"))
        all_js = "\n".join(js.values())
        missing = []
        for e in contract.endpoints:
            for fld in e.request:
                if fld.required and not re.search(rf"""(\b{re.escape(fld.name)}\s*[:,}}]|["']{re.escape(fld.name)}["'])""",
                                                  all_js):
                    missing.append(f"{e.method} {e.path}: frontend never sends required field '{fld.name}'")
        checks.append(Check(name="integration: request fields match contract", owner="frontend",
                            passed=not missing, log="\n".join(missing) or "all required fields sent"))
    return checks


def node_available() -> bool:
    return sandbox.backend_name() == "docker" or shutil.which("node") is not None


def contract_problems(contract: Contract) -> list[str]:
    """Static validation of the planner's contract (FR-3)."""
    errs, seen = [], set()
    if not contract.endpoints:
        errs.append("contract has no endpoints")
    for e in contract.endpoints:
        key = (e.method, e.path)
        if key in seen:
            errs.append(f"duplicate endpoint {e.method} {e.path}")
        seen.add(key)
        if not e.path.startswith("/api/"):
            errs.append(f"{e.method} {e.path}: path must start with /api/")
        if e.method in ("POST", "PUT", "PATCH") and not e.request:
            errs.append(f"{e.method} {e.path}: no request fields")
        if e.method == "GET" and e.request:
            errs.append(f"GET {e.path}: GET cannot have a JSON body; use query")
    return errs


def summarize(checks: list[Check]) -> str:
    return json.dumps({c.name: "pass" if c.passed else "FAIL" for c in checks})
