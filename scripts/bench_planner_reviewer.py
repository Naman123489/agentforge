"""Reproducible benchmark for the Planner and the Reviewer's failure analysis (real models via the shared router).

Planner: a fixed set of requests, each with a feature rubric (regexes over the produced contract + plan). Candidates:
  legacy          server/planner.plan_contract (contract only, YAML) on the given profile's slot A
  ai1-original    ForgeAI ai1-planner's own prompt, one call, judged by its own validators and by AgentForge's
  improved        engine.planner.PlannerAgent (validated plan + contract, re-asks with validator findings)
Reviewer: copies a real generated project, injects known faults, runs the deterministic Reviewer once per fixture,
then compares failure explanations: deterministic fallback vs model analysis on slot A or slot C.

  .venv/Scripts/python scripts/bench_planner_reviewer.py planner --profile config/profiles/web-nemotron.toml \
      --candidates legacy,ai1-original,improved --trials 2
  .venv/Scripts/python scripts/bench_planner_reviewer.py reviewer --fixture data/runs/<run>/project \
      --profile config/profiles/web-nemotron.toml --analyzers deterministic,A,C --trials 2
Results: data/bench_planner_reviewer/<stamp>-<kind>.json. Nothing here is estimated: every number is measured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from engine.agents.contract import load_contract  # noqa: E402
from engine.planner import PlannerAgent, PlannerFailed  # noqa: E402
from engine.planner.validate import contract_problems_for, validate_openapi, validate_plan  # noqa: E402
from engine.router import ModelRouter, RouterConfig  # noqa: E402

OUT = ROOT / "data" / "bench_planner_reviewer"

# ---- planner dataset: request + features the result must cover (regex over contract + plan, lower-cased) ----
PLANNER_CASES = [
    {"id": "todo", "request": "A todo app: users register and log in, create todos with a title and optional due date, "
     "mark them done, filter by done/open, and delete them.",
     "features": {"auth": r"/api/(register|login)", "create": r"post\s*/api/todos|/api/todos[^\n]*post|\"post\"",
                  "complete": r"done|complete", "filter": r"filter|status|done\b.*query|in: query",
                  "delete": r"delete", "due date": r"due"}},
    {"id": "recipes", "request": "A recipe box: users register and log in, add recipes with a title, ingredients and "
     "steps, browse and search their recipes, edit and delete them.",
     "features": {"auth": r"/api/(register|login)", "ingredients": r"ingredient", "steps": r"step",
                  "search": r"search|in: query|\bq\b", "edit": r"\bput\b|\bpatch\b", "delete": r"delete"}},
    {"id": "expenses", "request": "An expense tracker: log expenses with amount, category and date; list them by month; "
     "show the total per category for a month; delete an expense. No login needed.",
     "features": {"amount": r"amount", "category": r"categor", "month": r"month",
                  "totals": r"total|summary|sum", "delete": r"delete", "no auth": r"^(?!.*bearer).*$"}},
    {"id": "arcade", "request": "An arcade site with Snake and 2048: user accounts, score submission per game, and a "
     "global top-10 leaderboard per game.",
     "features": {"auth": r"/api/(register|login)", "scores": r"score", "per game": r"game",
                  "leaderboard": r"leaderboard|top", "limit 10": r"10|limit"}},
    {"id": "polls", "request": "A polling app: anyone can create a poll with 2-5 options, vote once per browser using a "
     "voter id, and see live results with vote counts.",
     "features": {"create poll": r"/api/polls", "options": r"option", "vote": r"vote",
                  "one vote": r"voter|already|409|once", "results": r"result|count"}},
]

AI1_SYSTEM = """
You are the Planner and Software Architect for AgentForge.

Return one JSON object with exactly these top-level fields:
- plan: a project plan
- openapi_spec: an OpenAPI 3.1.0 document for the requested application

The plan must contain:
project_name, summary, assumptions, technology_stack, tasks, risks.

Every task must contain:
task_id, title, description, owner, dependencies,
acceptance_checks, files_allowed.

Rules:
- Use unique task IDs such as TASK-001.
- Dependencies must reference existing tasks and must not form cycles.
- Put prerequisite tasks before dependent tasks.
- Use practical, specific relative file paths in files_allowed.
- Make acceptance checks testable.
- Define real application endpoints and schemas in openapi_spec.
- Include openapi, info, paths, and components.schemas as appropriate.
- Use OpenAPI version 3.1.0.
- Do not include AgentForge control-plane routes such as /runs
  unless the user explicitly requests an application that implements them.
- Do not claim code has already been written or tested.
- Return JSON only, without Markdown fences.
"""  # verbatim from ForgeAI ai1-planner main.py


def router(profile: str, tag: str) -> ModelRouter:
    cfg = RouterConfig.load(profile)
    cfg.budget.run_cost_limit_usd = 2.0
    cfg.budget.premium_cost_limit_usd = 2.0
    cfg.budget.run_token_limit = 3_000_000
    return ModelRouter(cfg, run_id=f"bench-{tag}")


def coverage(text: str, features: dict[str, str]) -> tuple[float, list[str]]:
    t = text.lower()
    missed = [k for k, rx in features.items() if not re.search(rx, t, re.M | re.S)]
    return 1 - len(missed) / len(features), missed


async def plan_once(candidate: str, case: dict, profile: str, trial: int) -> dict:
    r = router(profile, f"{candidate}-{case['id']}-{trial}")
    t0 = time.monotonic()
    row: dict = {"candidate": candidate, "case": case["id"], "trial": trial}
    try:
        if candidate == "legacy":
            from server import planner as legacy
            text, problems = await legacy.plan_contract(r, case["request"], "bench")
            spec = yaml.safe_load(text) if not problems else None
            blob, row["valid"], row["problems"] = text, not problems, problems[:5]
        elif candidate == "ai1-original":
            from engine.router.router import _THINK_RE
            res = await r.call("planner", [{"role": "system", "content": AI1_SYSTEM},
                                           {"role": "user", "content": "Plan this application and define its API contract:\n\n" + case["request"]}],
                               task_type="plan", stage_id="bench")
            text = _THINK_RE.sub("", res.content).strip()
            text = text[text.find("{"): text.rfind("}") + 1]
            d = json.loads(text)
            spec = d["openapi_spec"]
            ai1_ok = True
            try:  # its own bar
                from engine.planner.schemas import ProjectPlan  # noqa: F401 - AI1 tasks have free-form owners
                tasks = d["plan"]["tasks"]
                ids = [x["task_id"] for x in tasks]
                assert len(ids) == len(set(ids))
                validate_openapi(spec)
            except Exception as e:  # noqa: BLE001
                ai1_ok, row["ai1_error"] = False, str(e)[:200]
            problems = contract_problems_for(spec)
            blob = json.dumps(d)
            row.update(valid=ai1_ok and not problems, ai1_own_validation=ai1_ok, problems=problems[:5],
                       tasks=len(d["plan"].get("tasks", [])))
        else:
            a = await PlannerAgent(r).plan(case["request"], "bench")
            spec = yaml.safe_load(a.contract_yaml)
            blob = a.contract_yaml + json.dumps(a.plan.model_dump())
            tasks = a.plan.tasks
            row.update(valid=not a.problems, problems=a.problems[:5], attempts=a.attempts, tasks=len(tasks),
                       backend_tasks=sum(t.owner == "backend" for t in tasks), frontend_tasks=sum(t.owner == "frontend" for t in tasks),
                       waves=len(a.parallel_groups), open_questions=len(a.plan.open_questions),
                       requirements=len(a.plan.requirements),
                       acceptance_per_task=round(statistics.mean(len(t.acceptance_checks) for t in tasks), 2))
            validate_plan(a.plan)
        if spec is not None and row["valid"]:
            row["endpoints"] = len(load_contract(spec).endpoints)
        row["coverage"], row["missed"] = coverage(blob, case["features"])
    except (PlannerFailed, ValueError, KeyError, json.JSONDecodeError) as e:
        row.update(valid=False, error=f"{type(e).__name__}: {e}"[:300], coverage=0.0)
    except Exception as e:  # noqa: BLE001 - router stops, network errors: recorded, not hidden
        row.update(valid=False, error=f"{type(e).__name__}: {e}"[:300], coverage=0.0)
    s = r.usage_summary()
    row.update(latency_s=round(time.monotonic() - t0, 1), input_tokens=s["input_tokens"], output_tokens=s["output_tokens"],
               cost_usd=round(s["estimated_cost_usd"], 5), calls=sum(m.get("calls", 0) for m in s["by_model"]),
               models=[m["model_id"] for m in s["by_model"]])
    print(f"{candidate:13} {case['id']:9} t{trial} valid={row['valid']!s:5} cov={row['coverage']:.2f} "
          f"calls={row['calls']} {row['latency_s']:6.1f}s ${row['cost_usd']:.4f} {row.get('error', '') or row.get('problems', '')}"[:220], flush=True)
    return row


async def bench_planner(args) -> list[dict]:
    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(*a):
        async with sem:
            return await plan_once(*a)

    jobs = [guarded(c, case, args.profile, t) for c in args.candidates.split(",") for case in PLANNER_CASES
            for t in range(1, args.trials + 1)]
    return list(await asyncio.gather(*jobs))


# ---- reviewer dataset: faults injected into a copy of a real, approved project ----------------------
def _sub(path: str, old: str, new: str):
    def f(ws: Path):
        p = ws / path
        s = p.read_text(encoding="utf-8")
        assert old in s, (path, old)
        p.write_text(s.replace(old, new, 1), encoding="utf-8")
    return f


def _append(path: str, text: str):
    def f(ws: Path):
        with open(ws / path, "a", encoding="utf-8") as fh:
            fh.write(text)
    return f


REVIEW_FAULTS = [
    {"id": "clean", "inject": lambda ws: None, "verdict": "approve"},
    {"id": "wrong_status", "inject": _sub("backend/app.py", '@app.post("/api/bookmarks", status_code=201)', '@app.post("/api/bookmarks", status_code=200)'),
     "verdict": "reject", "owner": "backend", "file": "backend/app.py", "category": {"contract_mismatch", "test_failure"}, "keyword": r"201"},
    {"id": "syntax_error", "inject": _sub("backend/app.py", "def _now_iso() -> str:", "def _now_iso() -> str"),
     "verdict": "reject", "owner": "backend", "file": "backend/app.py", "category": {"syntax"}, "keyword": r"colon|:"},
    {"id": "frontend_type", "inject": _append("frontend/src/pages/BookmarksPage.tsx", '\nexport const pageSize: number = "20";\n'),
     "verdict": "reject", "owner": "frontend", "file": "frontend/src/pages/BookmarksPage.tsx", "category": {"type_error"}, "keyword": r"number|string|20"},
    {"id": "types_drift", "inject": lambda ws: (ws / "types.ts").unlink(),
     "verdict": "reject", "owner": {"planner", "shared"}, "file": "types.ts", "category": {"missing_file", "contract_mismatch"}, "keyword": r"types\.ts"},
    {"id": "shell_injection", "inject": _append("backend/app.py", "\n\ndef _debug(cmd: str) -> int:\n    import subprocess\n    return subprocess.call(cmd, shell=True)\n"),
     "verdict": "approve", "advisory": "security"},
]


def _copy_fixture(src: Path, dst: Path) -> None:
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("node_modules", "dist", "__pycache__", "*.db", ".git"))
    nm = src / "frontend" / "node_modules"
    if nm.is_dir():  # reuse the installed packages (junction on Windows, symlink elsewhere)
        try:
            import _winapi
            _winapi.CreateJunction(str(nm.resolve()), str(dst / "frontend" / "node_modules"))
        except (ImportError, OSError):
            (dst / "frontend" / "node_modules").symlink_to(nm.resolve(), target_is_directory=True)
    if not (dst / "types.ts").exists():  # projects built before the Planner integration have no types.ts
        from engine.planner.artifacts import schema_to_typescript
        (dst / "types.ts").write_text(schema_to_typescript(yaml.safe_load((dst / "contract/openapi.yaml").read_text(encoding="utf-8"))), encoding="utf-8")


def judge(fixes, fault: dict) -> dict:
    if not fixes:
        return {"owner_ok": False, "file_ok": False, "category_ok": False, "keyword_ok": False, "invented_code": False}
    f = fixes[0]
    owners = fault["owner"] if isinstance(fault["owner"], set) else {fault["owner"]}
    text = f"{f.problem} {f.suggestion}".lower()
    return {"owner_ok": f.owner in owners, "file_ok": (f.file or "").replace("\\", "/") == fault["file"],
            "category_ok": (f.category in fault["category"]) if f.category else None,
            "keyword_ok": bool(re.search(fault["keyword"], text)),
            # the fixture is FastAPI + React: advice in another framework's idiom was invented, not read
            "invented_code": bool(re.search(r"jsonify|flask|express|django|res\.status\(|@app\.route", text))}


async def bench_reviewer(args) -> list[dict]:
    from engine.agents.runner import default_runner
    from engine.reviewer import agentforge as af
    from engine.reviewer.analysis import analyze_failures
    from engine.reviewer.explanations import fallback_explanations
    from engine.reviewer.schemas import InternalCheckResult

    src = Path(args.fixture).resolve()
    base = OUT / "fixtures"
    rows = []
    for fault in REVIEW_FAULTS:
        ws = base / fault["id"]
        _copy_fixture(src, ws)
        fault["inject"](ws)
        contract = load_contract(ws / "contract/openapi.yaml")
        t0 = time.monotonic()
        rep = await asyncio.to_thread(af.review, ws, contract, default_runner(), f"bench-{fault['id']}", 1, True)
        review_s = round(time.monotonic() - t0, 1)
        failing = [c for c in rep["checks"] if not c["passed"] and c["required"]]
        advisory = [c["name"] for c in rep["checks"] if not c["passed"] and not c["required"]]
        base_row = {"fault": fault["id"], "expected": fault["verdict"], "verdict": rep["verdict"], "review_s": review_s,
                    "false_approval": rep["verdict"] == "approve" and fault["verdict"] == "reject",
                    "false_rejection": rep["verdict"] == "reject" and fault["verdict"] == "approve",
                    "failing_required": [c["name"] for c in failing], "advisory_failed": advisory}
        if "advisory" in fault:
            base_row["advisory_detected"] = fault["advisory"] in advisory
        print(f"review {fault['id']:16} verdict={rep['verdict']:7} expected={fault['verdict']:7} {review_s}s "
              f"failing={base_row['failing_required']} advisory={advisory}", flush=True)
        rows.append({**base_row, "analyzer": None})
        if rep["verdict"] != "reject" or fault["verdict"] != "reject":
            continue
        checks = [InternalCheckResult(name=c["name"], passed=False, exit_code=c["exit_code"], log_tail=c["log"]) for c in failing]
        contract_yaml = (ws / "contract/openapi.yaml").read_text(encoding="utf-8")
        for analyzer in args.analyzers.split(","):
            for trial in range(1, (1 if analyzer == "deterministic" else args.trials) + 1):
                if analyzer == "deterministic":
                    fixes, meta = fallback_explanations(checks), {"source": "deterministic"}
                else:
                    r = router(args.profile, f"review-{analyzer}-{fault['id']}-{trial}")
                    task_type = "complex_debug" if analyzer == "A" else "classify_error"
                    fixes, meta = await analyze_failures(r, ws, checks, "bench", contract_yaml, task_type=task_type)
                row = {**base_row, "analyzer": analyzer, "trial": trial, **judge(fixes, fault), "source": meta.get("source"),
                       "model_id": meta.get("model_id"), "latency_s": meta.get("latency_s"), "cost_usd": meta.get("cost_usd"),
                       "input_tokens": meta.get("input_tokens"), "output_tokens": meta.get("output_tokens"),
                       "context_chars": meta.get("context_chars"), "model_error": meta.get("model_error"),
                       "first_fix": (f"[{fixes[0].owner}] {fixes[0].file}: {fixes[0].problem} -> {fixes[0].suggestion}"[:400] if fixes else None)}
                rows.append(row)
                print(f"  {analyzer:13} t{trial} owner={row['owner_ok']} file={row['file_ok']} cat={row['category_ok']} "
                      f"kw={row['keyword_ok']} invented={row['invented_code']} src={row['source']} {row['latency_s']}s ${row['cost_usd']}", flush=True)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["planner", "reviewer"])
    ap.add_argument("--profile", default="config/profiles/web-nemotron.toml")
    ap.add_argument("--candidates", default="legacy,ai1-original,improved")
    ap.add_argument("--analyzers", default="deterministic,A,C")
    ap.add_argument("--fixture", default="")
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--label", default="")
    args = ap.parse_args()
    rows = asyncio.run(bench_planner(args) if args.kind == "planner" else bench_reviewer(args))
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{datetime.now():%Y%m%d-%H%M%S}-{args.kind}{('-' + args.label) if args.label else ''}.json"
    cfg = RouterConfig.load(args.profile)
    out.write_text(json.dumps({"kind": args.kind, "profile": args.profile,
                               "slots": {s.value: cfg.slot(s).model_id for s in cfg.slots}, "rows": rows},
                              indent=1, default=str), encoding="utf-8")
    print(f"report: {out}")


if __name__ == "__main__" and not (len(sys.argv) > 1 and sys.argv[1] == "summary"):
    main()


def summarize(paths: list[str]) -> str:
    """Markdown tables from saved reports (python scripts/bench_planner_reviewer.py summary <report.json>...)."""
    lines = []
    for p in paths:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        rows = d["rows"]
        lines.append(f"\n### {Path(p).name} (slot A = {d['slots']['A']}, C = {d['slots']['C']})\n")
        if d["kind"] == "planner":
            lines.append("| candidate | valid | coverage (mean) | model calls (mean) | latency s (median) | cost $ (mean) |")
            lines.append("|---|---|---|---|---|---|")
            for c in dict.fromkeys(r["candidate"] for r in rows):
                rs = [r for r in rows if r["candidate"] == c]
                valid = [r for r in rs if r["valid"]]
                lines.append(f"| {c} | {len(valid)}/{len(rs)} | {statistics.mean(r['coverage'] for r in rs):.2f} | "
                             f"{statistics.mean(r['calls'] for r in rs):.1f} | {statistics.median(r['latency_s'] for r in rs):.1f} | "
                             f"{statistics.mean(r['cost_usd'] for r in rs):.4f} |")
        else:
            verdicts = [r for r in rows if r["analyzer"] is None]
            fa = sum(r["false_approval"] for r in verdicts)
            fr = sum(r["false_rejection"] for r in verdicts)
            lines.append(f"Verdicts: {sum(r['verdict'] == r['expected'] for r in verdicts)}/{len(verdicts)} correct, "
                         f"false approvals {fa}, false rejections {fr}.\n")
            lines.append("| analyzer | owner | file | category | fix names the change | invented code | latency s | cost $ |")
            lines.append("|---|---|---|---|---|---|---|---|")
            for a in dict.fromkeys(r["analyzer"] for r in rows if r["analyzer"]):
                rs = [r for r in rows if r["analyzer"] == a]
                n = len(rs)
                cnt = lambda k: f"{sum(bool(r.get(k)) for r in rs)}/{n}"  # noqa: E731
                lat = [r["latency_s"] for r in rs if r.get("latency_s") is not None]
                cost = [r["cost_usd"] for r in rs if r.get("cost_usd") is not None]
                lines.append(f"| {a} | {cnt('owner_ok')} | {cnt('file_ok')} | {cnt('category_ok') if a != 'deterministic' else 'n/a'} | "
                             f"{cnt('keyword_ok')} | {cnt('invented_code')} | {statistics.median(lat) if lat else '-'} | "
                             f"{(statistics.mean(cost) if cost else 0):.5f} |")
    return "\n".join(lines)


if __name__ == "__main__" and len(sys.argv) > 2 and sys.argv[1] == "summary":
    print(summarize(sys.argv[2:]))
