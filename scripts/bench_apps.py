"""Build the benchmark's fixed application set through the real API (planner + builders + reviewer + visual QA).

    .venv/Scripts/python scripts/bench_apps.py            # needs the API on :8000 and data/platform_token.txt

Writes data/bench_apps/fixtures.json: per category the run id, status, cost and project path. The frontend model
benchmark (scripts/bench_frontend_models.py --fixtures ...) reuses each app's contract + backend, so every
candidate model builds against identical inputs.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "http://localhost:8000/api/v1"

APPS = {
    "saas-dashboard": "A SaaS analytics dashboard: users sign up and log in, create projects, record events (name, numeric "
                      "value, timestamp) for a project, and see a dashboard with totals per day for the last 14 days, the top "
                      "5 event names and a filterable event list.",
    "ecommerce": "An online store: a product catalogue with categories, search and product pages; a shopping cart with "
                 "quantities; checkout that creates an order (no real payment); and an order history for logged-in users.",
    "task-manager": "A project management tool: users log in, create projects, add tasks with title, assignee, due date and "
                    "status (todo, doing, done), see them on a board grouped by status, and move tasks between columns.",
    "ai-notes": "A smart notes app: users log in, write notes; the server fills a 'summary' (first two sentences) and up to "
                "5 keyword tags extracted from the text for every note; users browse by tag and search notes.",
    "crud-auth": "A contacts manager: users sign up and log in, add contacts with name, email, phone, company and notes, "
                 "edit and delete them, search by name or company, and mark favourites.",
    "data-viz": "A personal finance tracker: users log in, record expenses with amount, category and date, see monthly "
                "totals per category as a bar chart and a 6-month trend line chart, and delete expenses.",
}
BUDGET = {"max_tokens": 1_000_000, "max_cost_usd": 2, "max_premium_cost_usd": 1, "max_wall_time_seconds": 3600,
          "max_retries_per_stage": 3}


def call(method: str, path: str, token: str, body: dict | None = None, key: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(API + path, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def main() -> int:
    token = (ROOT / "data" / "platform_token.txt").read_text(encoding="utf-8").strip()
    out = ROOT / "data" / "bench_apps" / "fixtures.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    results = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    pending = [k for k in APPS if results.get(k, {}).get("status") != "done"]
    active: dict[str, str] = {}
    stamp = int(time.time())
    while pending or active:
        while pending and len(active) < 2:
            name = pending.pop(0)
            try:
                run = call("POST", "/runs", token, {"prompt": APPS[name], "project_name": name, "budget": BUDGET},
                           key=f"bench-{name}-{stamp}")
                active[name] = run["id"]
                print(f"started {name}: {run['id']}", flush=True)
            except urllib.error.HTTPError as e:
                print(f"could not start {name}: HTTP {e.code}", flush=True)
                pending.insert(0, name)
                break
        time.sleep(10)
        for name, rid in list(active.items()):
            run = call("GET", f"/runs/{rid}", token)
            if run["status"] in ("done", "failed", "stopped", "cancelled"):
                active.pop(name)
                results[name] = {"run_id": rid, "status": run["status"], "cost_usd": run["usage"]["estimated_cost_usd"],
                                 "tokens": run["usage"]["total_tokens"], "error": (run.get("error") or {}).get("message"),
                                 "project": str(ROOT / "data" / "runs" / rid / "project")}
                out.write_text(json.dumps(results, indent=2), encoding="utf-8")
                print(f"finished {name}: {run['status']} ${run['usage']['estimated_cost_usd']:.3f} "
                      f"{(run.get('error') or {}).get('message', '')[:160]}", flush=True)
    total = sum(r.get("cost_usd", 0) for r in results.values())
    print(f"total estimated spend: ${total:.3f}  report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
