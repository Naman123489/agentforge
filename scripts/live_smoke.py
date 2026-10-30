"""Live router smoke test: three real tasks through ModelRouter, one per slot.

    AF_ROUTER_CONFIG=config/profiles/web-light.toml python scripts/live_smoke.py      # Token Factory (~$0.005)
    AF_ROUTER_CONFIG=config/profiles/spark-lightning.toml python scripts/live_smoke.py # DGX Spark ($0)

Expect: log summary -> C, routine code -> B, planning with a schema -> A.
Credit spend is persisted in data/ledger.json (set AF_CREDIT_BALANCE_USD for the hard cap).
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.router import AccountLedger, ModelRouter, RouterConfig, RouterStopped  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class MiniPlan(BaseModel):
    title: str
    endpoints: list[str]
    risks: list[str]


LOG = """FAILED backend/test_app.py::test_submit_and_leaderboard - KeyError: 'score'
E   assert board[0] == {'username': 'carol', 'game': '2048', 'score': 512}
E   KeyError: 'score'  (leaderboard rows contain 'points' instead of 'score')
1 failed, 2 passed in 0.81s"""

TASKS = [
    ("C economy", "orchestrator", [{"role": "user", "content": f"Summarise this test failure in one line:\n{LOG}"}],
     None, {"task_type": "summarize_log"}),
    ("B coding", "backend", [{"role": "user", "content": "Write a FastAPI route GET /api/health returning "
                                                        '{"status": "ok"}. Code only, no explanation.'}],
     None, {"task_type": "code_backend", "complexity": "low"}),
    ("A planning", "planner", [{"role": "user", "content": "Plan a tiny score leaderboard API: give a title, "
                                                          "the endpoint list, and the top 2 risks."}],
     MiniPlan, {"task_type": "plan"}),
]


async def main() -> int:
    cfg = RouterConfig.load()
    account = AccountLedger(cfg, ROOT / "data" / "ledger.json")
    print(f"profile: {cfg.slot(cfg.slots.__iter__().__next__()).model_id} ... | "
          f"credits remaining: {account.credit_remaining_usd}")
    decisions: list[dict] = []
    router = ModelRouter(cfg, run_id=f"smoke-{int(time.time())}", account=account,
                         on_event=lambda e: decisions.append(e["data"]) if e["type"] == "route_decision" else None)
    ok = True
    for label, role, messages, schema, kw in TASKS:
        n = len(decisions)
        try:
            res = await router.call(role, messages, schema=schema, **kw)
        except RouterStopped as e:
            ok = False
            print(f"[STOP] {label}: {e.reason} - {e.detail[:200]}")
            continue
        d = decisions[n]
        cost = "unknown" if res.cost_usd is None else f"${res.cost_usd:.5f}"
        print(f"\n[{label}] -> slot {res.slot.value} ({res.model_id}) in {res.latency_s:.1f}s, "
              f"{res.input_tokens}+{res.output_tokens} tok, {cost}, attempts={res.attempts}")
        print("   why:", " | ".join(d["reasons"]))
        body = res.parsed.model_dump_json() if res.parsed else res.content.strip()
        print("   out:", body[:300].replace("\n", " "))
        ok &= label[0] == res.slot.value
    s = router.stats()
    print(f"\nrun total: {s['tokens_used']} tokens, ${s['cost_usd_known']:.5f}; "
          f"credits remaining: {account.credit_remaining_usd}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
