"""Benchmark Token Factory models as the Frontend agent's coder (slot B), with escalation to slot A disabled.

    python scripts/bench_frontend_models.py --fixtures data/bench_apps/fixtures.json \
        --model reflection/beam --model moonshotai/Kimi-K2.7-Code --visual-rounds 1 --budget 12

Every candidate builds each fixture app's frontend from scratch against the SAME contract and backend (copied from a
finished run). Measured per (model, app):
  build      the Frontend agent's executed checks (npm, tsc, vite build, contract usage, served with backend)
  browser    real Chrome at 1440px and 390px: measured defects before/after one visual repair round, forms that work
  judge      a vision model (nvidia/Nemotron-3.5-Super-VL, thinking off) scores screenshots 1-10 against a fixed rubric;
             an evaluation signal only, never part of AgentForge's approval gate
  cost       the router's estimate from catalog prices (Token Factory does not report per-call billing)
Prices come from the live /v1/models catalog. Chinese-origin models are BLOCKED in AgentForge's router config; this
script may place one in slot B for measurement only (rows are marked bench_only) at the user's explicit request.
Report: data/frontend_bench/<stamp>.json and .md. --budget stops launching new jobs once the estimate reaches it.

Legacy single-app mode still works: --app data/runs/<run>/project --model id:price_in:price_out
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import shutil
import statistics
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.agents.frontend import FrontendAgent  # noqa: E402
from engine.router import ModelRouter, RouterConfig, Slot  # noqa: E402
from engine.router.config import is_banned_model, load_dotenv  # noqa: E402
from engine.schemas import TaskAssignment  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
JUDGE_MODEL = "nvidia/Nemotron-3.5-Super-VL"
RUBRIC = """You are a strict senior product designer reviewing screenshots of ONE web app (desktop 1440px and phone 390px).
Score each criterion 1-10 (10 = a polished commercial product, 5 = acceptable but plain, 1 = broken):
layout (structure, alignment, use of space), hierarchy (clear titles, typography scale, emphasis), consistency
(spacing, colours, components look like one system), polish (finished, no placeholder look, good states),
mobile (the phone screenshots: fits, readable, usable), overall.
Reply with ONE JSON object only: {"layout":n,"hierarchy":n,"consistency":n,"polish":n,"mobile":n,"overall":n,
"issues":["the 3 most important concrete problems"]}"""


def catalog() -> dict[str, tuple[float, float]]:
    req = urllib.request.Request("https://api.tokenfactory.nebius.com/v1/models?verbose=true",
                                 headers={"Authorization": "Bearer " + os.environ["NEBIUS_API_KEY"]})
    out = {}
    for m in json.load(urllib.request.urlopen(req, timeout=30))["data"]:
        p = m.get("pricing", {})
        out[m["id"]] = (float(p.get("prompt", 0)) * 1e6, float(p.get("completion", 0)) * 1e6)
    return out


def router_for(model: str, pin: float, pout: float) -> ModelRouter:
    cfg = RouterConfig.load("config/profiles/web-quality.toml")
    b = cfg.slots[Slot.B]
    nemotron = model.startswith("nvidia/")
    # Assignment after load skips the config validator, so a banned model can be MEASURED here; AgentForge itself
    # still refuses to load a config that names one.
    cfg.slots[Slot.B] = b.model_copy(update=dict(
        model_id=model, price_input_per_1m=pin, price_output_per_1m=pout, price_verified=True,
        price_source="Token Factory /v1/models", extra_body=b.extra_body if nemotron else {},
        reasoning_overhead=1.0 if nemotron else 2.0, timeout_s=420.0, max_output_tokens=32768))
    cfg.policy.escalate_to_premium_after_failures_by_agent = {"frontend": 99}  # measure the model alone
    cfg.budget.run_cost_limit_usd = 1.5
    cfg.budget.run_token_limit = 1_500_000
    return ModelRouter(cfg, run_id=f"bench-{model.split('/')[-1]}")


def judge(shots: list[Path]) -> dict:
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["NEBIUS_API_KEY"], base_url="https://api.tokenfactory.nebius.com/v1")
    content: list[dict] = [{"type": "text", "text": RUBRIC}]
    for s in shots[:4]:
        content.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(s.read_bytes()).decode()}})
    t0 = time.perf_counter()
    r = client.chat.completions.create(model=JUDGE_MODEL, max_tokens=700, temperature=0,
                                       extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                                       messages=[{"role": "user", "content": content}])
    text = r.choices[0].message.content or ""
    m = re.search(r"\{.*\}", text, re.S)
    scores = json.loads(m.group(0)) if m else {}
    scores["judge_cost_usd"] = round((r.usage.prompt_tokens * 0.3 + r.usage.completion_tokens * 0.9) / 1e6, 5)
    scores["judge_seconds"] = round(time.perf_counter() - t0, 1)
    return scores


def pick_shots(pages) -> list[Path]:
    """Home on desktop + phone, and the most content-rich other screen on desktop + phone."""
    by = {}
    for p in pages:
        by.setdefault(p.route, {})[p.viewport] = p
    home = by.get("/", {})
    other = max((v for k, v in by.items() if k != "/"), key=lambda v: v.get("desktop").text_chars if v.get("desktop") else 0,
                default={})
    return [Path(x.screenshot) for x in (home.get("desktop"), home.get("mobile"), other.get("desktop"), other.get("mobile")) if x]


async def one(model: str, pin: float, pout: float, app_name: str, app: Path, out: Path, iterations: int,
              visual_rounds: int, spend: dict, budget: float) -> dict:
    row: dict = {"model": model, "app": app_name, "bench_only": is_banned_model(model)}
    if spend["usd"] >= budget:
        return {**row, "status": "skipped", "error": "benchmark budget reached"}
    ws = out / model.replace("/", "_") / app_name
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True)
    shutil.copytree(app / "contract", ws / "contract")
    shutil.copytree(app / "backend", ws / "backend", ignore=shutil.ignore_patterns("__pycache__", "*.db"))
    for f in ("plan.json",):
        if (app / f).exists():
            shutil.copy2(app / f, ws / f)
    brief = ""
    if (app / "plan.json").exists():
        plan = json.loads((app / "plan.json").read_text(encoding="utf-8"))
        brief = "\n".join(f"- {t['title']}: {t['description']}" for t in plan.get("tasks", []) if t.get("owner") == "frontend")
    instructions = "Build the complete frontend for this application against the contract." + (
        f"\n\nPlanner tasks for the frontend:\n{brief}" if brief else "")
    router = router_for(model, pin, pout)
    t0 = time.perf_counter()
    try:
        agent = FrontendAgent(router, max_iterations=iterations)
        task = TaskAssignment(stage_id="frontend", agent="frontend", files_allowed=["frontend/**"],
                              contract_ref="contract/openapi.yaml", instructions=instructions)
        r = await agent.run(task, ws)
        row.update(status=r.status, iterations=r.iterations, failed_checks=[c.name for c in r.checks if not c.passed])
        if r.status == "done":
            from engine.agents.visual import inspect_workspace
            rep = await asyncio.to_thread(inspect_workspace, ws, ws / ".agentforge" / "visual" / "round1")
            row["defects_before"] = rep.defects()
            for n in range(visual_rounds):
                if not rep.defects():
                    break
                fix = TaskAssignment(stage_id="frontend", agent="frontend", files_allowed=["frontend/**"],
                                     contract_ref="contract/openapi.yaml", instructions=instructions, failed_attempts=2,
                                     reviewer_failures=["visual QA (the built app opened in Chrome at 1440px and 390px; "
                                                        "these were measured, fix each):" + "".join(f"\n- {d}" for d in rep.defects()[:15])])
                r2 = await agent.run(fix, ws)
                row["visual_repair_status"] = r2.status
                if r2.status != "done":
                    break
                rep = await asyncio.to_thread(inspect_workspace, ws, ws / ".agentforge" / "visual" / f"round{n + 2}")
            row.update(defects_after=rep.defects(), forms=f"{rep.forms_ok}/{rep.forms_tried}",
                       screenshots=[p.screenshot for p in rep.pages])
            try:
                row["judge"] = await asyncio.to_thread(judge, pick_shots(rep.pages))
            except Exception as e:  # noqa: BLE001
                row["judge"] = {"error": f"{type(e).__name__}: {e}"[:200]}
    except Exception as e:  # noqa: BLE001 - a model the provider rejects must not end the benchmark
        row.update(status="error", error=f"{type(e).__name__}: {e}"[:300])
    u = router.usage_summary()
    row.update(cost_usd=round(u["estimated_cost_usd"], 4), tokens=u["total_tokens"], seconds=round(time.perf_counter() - t0),
               workspace=str(ws))
    spend["usd"] += row["cost_usd"] + (row.get("judge") or {}).get("judge_cost_usd", 0)
    print(f"{model:40} {app_name:15} {row['status']:8} ${row['cost_usd']:.3f} {row['seconds']}s "
          f"defects {len(row.get('defects_before', []))}->{len(row.get('defects_after', []))} "
          f"judge={(row.get('judge') or {}).get('overall')} spent=${spend['usd']:.2f}", flush=True)
    return row


def quality(r: dict) -> float | None:
    """0-100: build must pass; then 50% judge overall, 30% measured defects, 20% working forms."""
    if r.get("status") != "done":
        return 0.0 if r.get("status") in ("failed", "error", "stopped") else None
    j = (r.get("judge") or {}).get("overall")
    j = float(j) / 10 if isinstance(j, (int, float)) else 0.5
    d = 1 - min(len(r.get("defects_after", [])), 10) / 10
    ok, tried = (int(x) for x in (r.get("forms") or "0/0").split("/"))
    f = ok / tried if tried else 1.0
    return round(100 * (0.5 * j + 0.3 * d + 0.2 * f), 1)


def summary(rows: list[dict]) -> str:
    lines = ["| model | builds ok | quality (mean) | judge overall | defects before -> after | forms ok | cost / app | time / app |",
             "|---|---|---|---|---|---|---|---|"]
    for m in dict.fromkeys(r["model"] for r in rows):
        rs = [r for r in rows if r["model"] == m and r.get("status") != "skipped"]
        if not rs:
            continue
        ok = [r for r in rs if r.get("status") == "done"]
        q = [quality(r) for r in rs if quality(r) is not None]
        j = [r["judge"]["overall"] for r in ok if isinstance((r.get("judge") or {}).get("overall"), (int, float))]
        db = [len(r.get("defects_before", [])) for r in ok]
        da = [len(r.get("defects_after", [])) for r in ok]
        forms = [r.get("forms", "0/0") for r in ok]
        fo = sum(int(x.split("/")[0]) for x in forms), sum(int(x.split("/")[1]) for x in forms)
        tag = " (bench only)" if rs[0].get("bench_only") else ""
        lines.append(f"| {m}{tag} | {len(ok)}/{len(rs)} | {statistics.mean(q):.1f} | "
                     f"{statistics.mean(j):.1f} | {statistics.mean(db) if db else 0:.1f} -> {statistics.mean(da) if da else 0:.1f} | "
                     f"{fo[0]}/{fo[1]} | ${statistics.mean(r['cost_usd'] for r in rs):.3f} | "
                     f"{statistics.mean(r['seconds'] for r in rs):.0f}s |" if j else
                     f"| {m}{tag} | {len(ok)}/{len(rs)} | {statistics.mean(q) if q else 0:.1f} | - | - | - | "
                     f"${statistics.mean(r['cost_usd'] for r in rs):.3f} | {statistics.mean(r['seconds'] for r in rs):.0f}s |")
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures")
    ap.add_argument("--app", action="append", default=[])
    ap.add_argument("--only", action="append", default=[], help="fixture names to include")
    ap.add_argument("--model", action="append", required=True, help="model id, or id:price_in:price_out")
    ap.add_argument("--iterations", type=int, default=4)
    ap.add_argument("--visual-rounds", type=int, default=1)
    ap.add_argument("--budget", type=float, default=12.0, help="stop launching jobs at this estimated spend (USD)")
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()
    load_dotenv()
    prices = catalog()
    models = []
    for m in args.model:
        mid, *p = m.rsplit(":", 2) if m.count(":") >= 2 else (m,)
        pin, pout = (float(p[0]), float(p[1])) if p else prices[mid]
        models.append((mid, pin, pout))
    apps: list[tuple[str, Path]] = [(Path(a).parent.name, Path(a)) for a in args.app]
    if args.fixtures:
        fx = json.loads(Path(args.fixtures).read_text(encoding="utf-8"))
        apps += [(k, Path(v["project"])) for k, v in fx.items() if v.get("status") == "done" and (not args.only or k in args.only)]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = ROOT / "data" / "frontend_bench" / stamp
    spend = {"usd": 0.0}
    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(*a):
        async with sem:
            return await one(*a)

    rows = await asyncio.gather(*(guarded(m, i, o, name, path, out, args.iterations, args.visual_rounds, spend, args.budget)
                                  for name, path in apps for m, i, o in models))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps({"models": models, "apps": [a for a, _ in apps], "rows": rows,
                                                    "spend_usd": round(spend["usd"], 4)}, indent=1), encoding="utf-8")
    md = summary(rows)
    out.with_suffix(".md").write_text(md + f"\n\nEstimated spend: ${spend['usd']:.2f}\n", encoding="utf-8")
    print(md)
    print(f"estimated spend ${spend['usd']:.2f}; report {out.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
