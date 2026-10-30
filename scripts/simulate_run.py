"""Zero-token demo of the model router on an arcade-like run.

Uses a fake provider (no API key, no spend) with injected failures, and prints
every routing decision with its reason. Good for the demo video and for
checking policy changes before spending credits.

    python scripts/simulate_run.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.router import ModelRouter, RouterConfig, RouterStopped, Slot  # noqa: E402


class Err(Exception):
    def __init__(self, status: int, msg: str = "", retry_after: float = 30):
        super().__init__(msg or f"HTTP {status}")
        self.status_code, self.response = status, SimpleNamespace(headers={"retry-after": str(retry_after)})


class FakeProvider:
    def __init__(self):
        self.fail: dict[str, list[Exception]] = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, model, messages, max_tokens, **_):
        await asyncio.sleep(0.01)
        q = self.fail.get(model)
        if q:
            raise q.pop(0)
        chars = sum(len(m["content"]) for m in messages)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                               usage=SimpleNamespace(prompt_tokens=chars // 4, completion_tokens=min(max_tokens, 3000)))


class SimClock:
    """Simulated time: each build step takes a while, so cooldowns expire realistically."""

    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += s


def msgs(n_chars: int) -> list[dict]:
    return [{"role": "user", "content": "x" * n_chars}]


async def main() -> None:
    # Demo prices for slot A so expected-cost logic is visible; real config leaves it unset until verified.
    cfg = RouterConfig.load(env={"AF_MODEL_A_PRICE_IN": "0.50", "AF_MODEL_A_PRICE_OUT": "2.20",
                                 "AF_RUN_TOKEN_LIMIT": "106000"})
    provider = FakeProvider()
    rows: list[tuple] = []

    def on_event(ev: dict) -> None:
        ev = {**ev["data"], "type": ev["type"]}  # contract RunEvent: details live under "data"
        if ev["type"] == "route_decision":
            rows.append(("decide", ev["task"]["task_type"], ev["slot"] or ev["action"].upper(),
                         ev["reasons"][-1], ""))
        elif ev["type"] == "model_call_failed":
            rows.append(("  fail", "", ev["slot"], ev["error_kind"], ""))
        elif ev["type"] == "model_call_ok":
            cost = "unknown" if ev["cost_usd"] is None else f"${ev['cost_usd']:.4f}"
            rows.append(("  ok", "", ev["slot"], f"{ev['input_tokens'] + ev['output_tokens']} tok", cost))

    clock = SimClock()
    router = ModelRouter(cfg, provider, run_id="arcade-sim", on_event=on_event, sleep=clock.sleep, clock=clock)
    A, B, C = (cfg.slot(s).model_id for s in Slot)

    async def step(title: str, *args, **kw):
        clock.t += 10  # time spent in sandbox checks between LLM calls
        rows.append((f"== {title}", "", "", "", ""))
        try:
            await router.call(*args, **kw)
        except RouterStopped as e:
            rows.append(("  STOP", "", "", e.reason, ""))

    await step("Plan + contract", "planner", msgs(6000))
    await step("API contract", "planner", msgs(6000), task_type="api_contract")
    rows.append(("== Backend + Frontend in parallel", "", "", "", ""))
    await asyncio.gather(router.call("backend", msgs(8000)), router.call("frontend", msgs(8000)))
    await step("Summarise pytest log", "reviewer", msgs(20000), task_type="summarize_log")
    provider.fail[B] = [Err(429, "rate limited", retry_after=60)]
    await step("Frontend, B rate-limited for 60s", "frontend", msgs(8000))
    clock.t += 60
    provider.fail[B] = [Err(429, "rate limited", retry_after=5)]
    await step("Frontend, B rate-limited for 5s", "frontend", msgs(8000))
    for n in range(3):
        await step(f"Debug, {n} failed attempt(s) so far", "backend", msgs(8000), task_type="debug",
                   failed_attempts=n)
    provider.fail[A] = [Err(500), Err(500), Err(500)]
    await step("Review while A returns 5xx", "reviewer", msgs(8000), task_type="review_judgment")
    await step("Commit message", "reviewer", msgs(2000), task_type="commit_message")
    await step("Easy UI tweak (normal budget)", "frontend", msgs(8000), complexity="low", risk="low")
    await step("Follow-up plan, A still failing", "planner", msgs(6000))
    for i in range(3):
        await step(f"Low-risk edit {i + 1}", "frontend", msgs(8000), task_type="simple_edit")
    clock.t += 120  # A's circuit cooldown elapses
    await step("Easy UI tweak (low budget)", "frontend", msgs(8000), complexity="low", risk="low")
    await step("Backend code (low budget)", "backend", msgs(8000))
    await step("Critical plan (low budget)", "planner", msgs(6000))
    await step("Backend code", "backend", msgs(8000))
    await step("Backend code, budget gone", "backend", msgs(8000))

    w = (34, 16, 7, 62, 9)
    for r in rows:
        print("".join(str(c)[:x].ljust(x) for c, x in zip(r, w)))
    s = router.stats()
    print(f"\nTokens {s['tokens_used']}/{cfg.budget.run_token_limit}  known cost ${s['cost_usd_known']:.4f}  "
          f"premium tokens {s['premium_tokens']}")
    for slot, u in s["per_slot"].items():
        print(f"  {slot} {u['model_id']:<42} calls={u['calls']:<3} fails={u['failures']:<3} cost=${u['cost_usd']:.4f}")


if __name__ == "__main__":
    asyncio.run(main())
