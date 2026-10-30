"""Run the Backend agent for real: OpenAPI contract in, tested FastAPI backend out.

    python scripts/run_backend_agent.py                                    # arcade contract, web-light models
    python scripts/run_backend_agent.py --contract my_api.yaml --out runs/notes --instructions "Notes app API"

Uses AF_ROUTER_CONFIG from .env (web-light on Token Factory, or a Spark profile for $0).
The generated project lands in --out (default data/backend_runs/<timestamp>); run it with
    cd <out> && python -m uvicorn backend.app:app --reload
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.agents.backend import BackendAgent  # noqa: E402
from engine.router import AccountLedger, ModelRouter, RouterConfig  # noqa: E402
from engine.schemas import TaskAssignment  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", default=str(ROOT / "examples" / "arcade_openapi.yaml"))
    ap.add_argument("--out", default=str(ROOT / "data" / "backend_runs" / time.strftime("%Y%m%d-%H%M%S")))
    ap.add_argument("--instructions", default="Implement the Mini Arcade backend: accounts, score submission "
                                              "and per-game leaderboards, exactly as the contract says.")
    ap.add_argument("--max-iterations", type=int, default=4)
    args = ap.parse_args()

    cfg = RouterConfig.load()
    account = AccountLedger(cfg, ROOT / "data" / "ledger.json")
    out = Path(args.out)

    events: list[dict] = []

    def on_event(e: dict) -> None:
        events.append(e)
        e = {**e["data"], "type": e["type"]}  # contract RunEvent: details live under "data"
        if e["type"] == "route_decision":
            print(f"  router: {e['task']['task_type']} -> slot {e['slot']}  ({e['reasons'][-1]})")
        elif e["type"] == "model_call_ok":
            print(f"  model:  {e['model_id']} {e['input_tokens']}+{e['output_tokens']} tok, "
                  f"${e['cost_usd'] or 0:.5f}, {e['latency_s']}s")
        elif e["type"] == "model_call_failed":
            print(f"  model call failed: {e['error_kind']}")

    router = ModelRouter(cfg, run_id=f"backend-{out.name}", account=account, on_event=on_event)
    agent = BackendAgent(router, max_iterations=args.max_iterations)
    print(f"Backend agent | contract {args.contract} | sandbox {agent.runner.name} | out {out}")
    t0 = time.time()
    result = await agent.run(TaskAssignment(stage_id="backend", agent="backend", instructions=args.instructions,
                                            contract_ref=str(Path(args.contract).resolve())), out)
    print(f"\nstatus: {result.status} after {result.iterations} iteration(s), {time.time() - t0:.0f}s")
    for c in result.checks:
        flag = "SKIP" if c.skipped else ("PASS" if c.passed else "FAIL")
        print(f"  {flag} {c.name}: {c.log.strip().splitlines()[-1][:120] if c.log.strip() else ''}")
    print(f"files: {result.files_changed}")
    print(f"models: {result.models_used} | tokens: {result.tokens} | cost: ${result.cost_usd or 0:.5f} | "
          f"credits left: {account.credit_remaining_usd}")
    if result.notes:
        print(f"notes: {result.notes}")
    if result.contract_change:
        print("contract change requested:", result.contract_change.model_dump_json(indent=1))
    # platform-contract artifacts: BuildResult, the RunEvents, UsageSummary
    (out / "build_result.json").write_text(json.dumps(result.to_contract(), indent=2), encoding="utf-8")
    (out / "events.jsonl").write_text("".join(json.dumps(e, default=str) + "\n" for e in events), encoding="utf-8")
    (out / "usage_summary.json").write_text(json.dumps(router.usage_summary(), indent=2), encoding="utf-8")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
