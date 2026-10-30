"""The Frontend agent's checks. Each verifies an outcome, not just an exit code; a check that cannot run is
reported as not executed (skipped) and never counts as passed for a required check.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from ..schemas import CheckResult
from .contract import Contract
from .frontend_context import GENERATED_PATH, generate_client, unique_names
from .runner import Runner

FE = "frontend"
REQUIRED = ("package.json", "index.html", "src/main.tsx", "src/App.tsx", "src/api/generated.ts")
TEMPLATE_MARKER = "AGENTFORGE_TEMPLATE"
RAW_HTTP = re.compile(r"\bfetch\s*\(|XMLHttpRequest|\baxios\b")
PLACEHOLDERS = [
    (re.compile(r"\b(TODO|FIXME|TBD)\b"), "TODO/FIXME marker"),
    (re.compile(r"lorem ipsum", re.I), "lorem ipsum text"),
    (re.compile(r"coming soon", re.I), "'coming soon' text"),
    (re.compile(r"""href=["']#["']"""), 'placeholder link href="#"'),
    (re.compile(r"onClick=\{\(\)\s*=>\s*\{\s*\}\}"), "button with an empty click handler"),
    (re.compile(r"\balert\s*\("), "window.alert instead of in-app feedback"),
]

PREVIEW_APP = '''"""Serve the generated backend and the built frontend on one origin (AgentForge preview/integration)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from backend.app import app  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

DIST = os.path.join(ROOT, "frontend", "dist")
if os.path.isdir(DIST):
    # A backend may mount the raw frontend/ folder at "/"; the preview must serve the BUILT app instead.
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) not in ("", "/")
                            or not isinstance(getattr(r, "app", None), StaticFiles)]
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")  # after API routes: /api/* still wins
'''

SMOKE = r'''
import json, os, re, sys, tempfile
os.environ["AF_DB"] = os.path.join(tempfile.mkdtemp(), "smoke.db")
sys.path.insert(0, os.path.join(os.getcwd(), ".agentforge"))
try:
    from preview_app import app
except Exception as e:  # the backend (another agent's work) does not import: not the frontend's failure
    print(f"backend did not import: {type(e).__name__}: {e}")
    sys.exit(3)
from fastapi.testclient import TestClient
c = TestClient(app)
fails = []
r = c.get("/")
if r.status_code != 200 or 'id="root"' not in r.text:
    fails.append(f"GET / did not return the built index.html ({r.status_code})")
assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', r.text)
if not assets:
    fails.append("index.html references no built assets")
for a in assets:
    ar = c.get(a)
    if ar.status_code != 200:
        fails.append(f"GET {a} -> {ar.status_code}")
for ep in json.load(open(".agentforge/frontend_endpoints.json")):
    if ep["method"] == "GET" and not ep["auth"] and "{" not in ep["path"]:
        q = {f["name"]: (f.get("enum") or [f.get("example") or "1"])[0] for f in ep["query"] if f.get("required", True)}
        er = c.get(ep["path"], params=q)
        ct = er.headers.get("content-type", "")
        if er.status_code >= 500 or "json" not in ct:
            fails.append(f"GET {ep['path']} via the frontend origin -> {er.status_code} {ct}")
        else:
            print(f"ok   GET {ep['path']} -> {er.status_code} (same origin as the UI)")
print(f"ok   GET / and {len(assets)} asset(s)" if not fails else "")
for f in fails:
    print("FAIL " + f)
sys.exit(1 if fails else 0)
'''


def _src_files(root: Path) -> dict[str, str]:
    src = root / FE / "src"
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(src.rglob("*")) if p.is_file() and p.suffix in (".ts", ".tsx", ".js", ".jsx")} if src.is_dir() else {}


def _deps_stamp(root: Path) -> str:
    return hashlib.sha256((root / FE / "package.json").read_bytes()).hexdigest()


def condense_tsc(log: str, limit: int = 3000) -> str:
    errs = [line for line in log.splitlines() if "error TS" in line]
    head = "\n".join(errs[:25])
    return (f"{len(errs)} TypeScript error(s):\n{head}" if errs else log)[-limit:]


def run_frontend_checks(root: Path, contract: Contract, runner: Runner, require_integration: bool = False) -> list[CheckResult]:
    out: list[CheckResult] = []
    fe = root / FE

    def add(name: str, passed: bool, log: str = "", skipped: bool = False, exit_code: int | None = None, t0: float | None = None):
        out.append(CheckResult(name=name, passed=passed, log=log, skipped=skipped, exit_code=exit_code,
                               duration_ms=int((time.perf_counter() - t0) * 1000) if t0 else None))
        return out[-1]

    missing = [f for f in REQUIRED if not (fe / f).is_file()]
    add("required files", not missing, f"missing: {missing}" if missing else ", ".join(REQUIRED))
    if missing:
        return out

    gen = (root / GENERATED_PATH).read_text(encoding="utf-8")
    add("generated API client unchanged", gen == generate_client(contract),
        "src/api/generated.ts matches contract/openapi.yaml" if gen == generate_client(contract)
        else "src/api/generated.ts was edited or is stale; it is generated from the contract and must not be changed")

    src = {p: t for p, t in _src_files(root).items() if not p.endswith("api/generated.ts")}
    app = (fe / "src" / "App.tsx").read_text(encoding="utf-8")
    raw = [f"{p}: raw HTTP call ({m.group(0).strip()}); use the generated `api` client" for p, t in src.items() for m in RAW_HTTP.finditer(t)]
    names = [n for n, _ in unique_names(contract)]
    used = {n for t in src.values() for n in re.findall(r"\bapi\.(\w+)", t)}
    unknown = sorted(used - set(names))
    unused = [f"{n} ({e.method} {e.path})" for n, e in unique_names(contract) if n not in used]
    problems = raw + [f"api.{n} does not exist in the contract" for n in unknown] + \
        [f"contract endpoint never called by the UI: {u}" for u in unused]
    add("contract usage", not problems, "\n".join(problems) or f"all {len(names)} contract endpoints called through the typed client")

    ph = [f"{p}: {what}" for p, t in src.items() for rx, what in PLACEHOLDERS if rx.search(t)]
    if TEMPLATE_MARKER in app:
        ph.insert(0, "src/App.tsx is still the starter template")
    add("no placeholders", not ph, "\n".join(ph[:20]) or "no TODOs, placeholder links, empty handlers or template screens")

    # ---- toolchain -----------------------------------------------------------------------------------
    t0 = time.perf_counter()
    stamp = fe / "node_modules" / ".agentforge-deps"
    if stamp.is_file() and stamp.read_text() == _deps_stamp(root):
        add("dependencies installed", True, "node_modules up to date", exit_code=0, t0=t0)
    else:
        r = runner.run(["npm", "install", "--no-audit", "--no-fund", "--prefer-offline", "--loglevel=error"], fe, 600)
        if r.exit_code == 127:
            add("dependencies installed", False, "npm is not available in this sandbox; the frontend could not be built",
                skipped=True, exit_code=127, t0=t0)
            return out
        add("dependencies installed", r.ok, r.log(2500), exit_code=r.exit_code, t0=t0)
        if not r.ok:
            return out
        stamp.write_text(_deps_stamp(root))

    t0 = time.perf_counter()
    r = runner.run(["node", "node_modules/typescript/bin/tsc", "--noEmit", "-p", "tsconfig.json"], fe, 240)
    add("typecheck (tsc)", r.ok, "no type errors" if r.ok else condense_tsc(r.log(60_000)), exit_code=r.exit_code, t0=t0)

    t0 = time.perf_counter()
    r = runner.run(["node", "node_modules/vite/bin/vite.js", "build", "--logLevel", "warn"], fe, 300)
    built = (fe / "dist" / "index.html").is_file()
    add("build (vite)", r.ok and built, r.log(3000) if not (r.ok and built) else "dist/index.html built",
        exit_code=r.exit_code, t0=t0)

    # ---- served together with the backend ------------------------------------------------------------
    tools = root / ".agentforge"
    tools.mkdir(exist_ok=True)
    (tools / "preview_app.py").write_text(PREVIEW_APP, encoding="utf-8")
    if not (root / "backend" / "app.py").is_file() or not built:
        why = "backend not built yet" if built else "no frontend build"
        add("served with backend", not require_integration, f"not executed: {why}"
            + ("" if require_integration else "; the Reviewer verifies integration after both builders finish"), skipped=True)
        return out

    (tools / "frontend_endpoints.json").write_text(json.dumps([e.model_dump() for e in contract.endpoints]), encoding="utf-8")
    (tools / "frontend_smoke.py").write_text(SMOKE, encoding="utf-8")
    t0 = time.perf_counter()
    r = runner.run(["python", ".agentforge/frontend_smoke.py"], root, 120)
    if r.exit_code == 3 and not require_integration:  # backend mid-build; the Reviewer re-runs this with both done
        add("served with backend", True, "not executed: " + r.log(800), exit_code=3, t0=t0, skipped=True)
    else:
        add("served with backend", r.ok, r.log(3000), exit_code=r.exit_code, t0=t0)
    return out
