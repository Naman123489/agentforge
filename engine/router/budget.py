"""Token and cost accounting.

AccountLedger is shared across runs (credit balance, free allowances).
RunBudget is per run (FR-9 hard limits, FR-17 stats, premium cap).

Costs are only computed from configured prices. A call on a slot without a
price is recorded as tokens with cost=None, never as zero.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import RouterConfig, Slot, SlotConfig


@dataclass
class CostEstimate:
    cost_usd: float | None  # None = unknown price
    free_tokens: int = 0  # tokens covered by a free allowance
    allowance_exhausted_without_price: bool = False


class AccountLedger:
    """Account-wide usage: prepaid credit drawdown and free-tier allowances."""

    def __init__(self, cfg: RouterConfig, path: str | Path | None = None):
        """path: JSON file that keeps spend across restarts (real credits must not reset on reboot)."""
        self.cfg = cfg
        self.path = Path(path) if path else None
        self.credit_spent_usd = 0.0
        self.free_used: dict[Slot, int] = {s: 0 for s in Slot}
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text())
            self.credit_spent_usd = float(data.get("credit_spent_usd", 0.0))
            self.free_used.update({Slot(k): int(v) for k, v in data.get("free_used", {}).items()})

    def free_remaining(self, slot: Slot) -> int | None:
        sc = self.cfg.slot(slot)
        if sc.billing != "free_allowance":
            return None
        return max(0, (sc.free_allowance_tokens or 0) - self.free_used[slot])

    def estimate(self, slot: Slot, input_tokens: int, output_tokens: int) -> CostEstimate:
        sc: SlotConfig = self.cfg.slot(slot)
        total = input_tokens + output_tokens
        if sc.billing != "free_allowance":
            return CostEstimate(sc.list_price(input_tokens, output_tokens))
        free = min(total, self.free_remaining(slot) or 0)
        paid_fraction = (total - free) / total if total else 0.0
        if paid_fraction == 0:
            return CostEstimate(0.0, free)
        price = sc.list_price(input_tokens, output_tokens)
        if price is None:
            return CostEstimate(None, free, allowance_exhausted_without_price=True)
        return CostEstimate(price * paid_fraction, free)

    def record(self, slot: Slot, est: CostEstimate) -> None:
        self.free_used[slot] += est.free_tokens
        if est.cost_usd and self.cfg.slot(slot).billing == "prepaid_credits":
            self.credit_spent_usd += est.cost_usd
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"credit_spent_usd": self.credit_spent_usd,
                                       "free_used": {s.value: n for s, n in self.free_used.items()}}))
            tmp.replace(self.path)

    @property
    def credit_remaining_usd(self) -> float | None:
        bal = self.cfg.account.credit_balance_usd
        return None if bal is None else bal - self.credit_spent_usd

    @property
    def credit_low(self) -> bool:
        bal = self.cfg.account.credit_balance_usd
        rem = self.credit_remaining_usd
        return bal is not None and rem is not None and rem < bal * self.cfg.account.credit_low_fraction


@dataclass
class SlotUsage:
    calls: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    unpriced_tokens: int = 0
    latency_s_total: float = 0.0


@dataclass
class RunBudget:
    cfg: RouterConfig
    run_id: str = "run"
    tokens_used: int = 0
    cost_usd: float = 0.0  # known cost only
    unpriced_tokens: int = 0  # tokens whose cost is unknown
    premium_tokens: int = 0
    premium_cost_usd: float = 0.0
    per_slot: dict[Slot, SlotUsage] = field(default_factory=lambda: {s: SlotUsage() for s in Slot})

    # ---- limits -----------------------------------------------------------
    @property
    def tokens_remaining(self) -> int:
        return self.cfg.budget.run_token_limit - self.tokens_used

    @property
    def cost_remaining_usd(self) -> float | None:
        lim = self.cfg.budget.run_cost_limit_usd
        return None if lim is None else lim - self.cost_usd

    @property
    def premium_tokens_remaining(self) -> int:
        return self.cfg.budget.premium_token_limit - self.premium_tokens

    @property
    def premium_cost_remaining_usd(self) -> float | None:
        lim = self.cfg.budget.premium_cost_limit_usd
        return None if lim is None else lim - self.premium_cost_usd

    @property
    def remaining_fraction(self) -> float:
        """Smallest remaining share of any run-level limit (tokens, known cost)."""
        fracs = [self.tokens_remaining / self.cfg.budget.run_token_limit]
        lim = self.cfg.budget.run_cost_limit_usd
        if lim:
            fracs.append((lim - self.cost_usd) / lim)
        return max(0.0, min(fracs))

    @property
    def premium_remaining_fraction(self) -> float:
        fracs = [self.premium_tokens_remaining / self.cfg.budget.premium_token_limit]
        lim = self.cfg.budget.premium_cost_limit_usd
        if lim:
            fracs.append((lim - self.premium_cost_usd) / lim)
        return max(0.0, min(fracs))

    @property
    def exhausted(self) -> bool:
        return self.remaining_fraction <= 0.0

    # ---- recording ----------------------------------------------------------
    def record_success(self, slot: Slot, input_tokens: int, output_tokens: int,
                       est: CostEstimate, latency_s: float) -> None:
        total = input_tokens + output_tokens
        u = self.per_slot[slot]
        u.calls += 1
        u.input_tokens += input_tokens
        u.output_tokens += output_tokens
        u.latency_s_total += latency_s
        self.tokens_used += total
        if est.cost_usd is None:
            u.unpriced_tokens += total
            self.unpriced_tokens += total
        else:
            u.cost_usd += est.cost_usd
            self.cost_usd += est.cost_usd
        if slot is Slot.A:
            self.premium_tokens += total
            self.premium_cost_usd += est.cost_usd or 0.0

    def record_failure(self, slot: Slot) -> None:
        self.per_slot[slot].calls += 1
        self.per_slot[slot].failures += 1

    def snapshot(self) -> dict:
        """Dashboard-friendly stats (FR-17)."""
        return {
            "run_id": self.run_id,
            "tokens_used": self.tokens_used,
            "tokens_remaining": self.tokens_remaining,
            "cost_usd_known": round(self.cost_usd, 6),
            "unpriced_tokens": self.unpriced_tokens,
            "cost_is_complete": self.unpriced_tokens == 0,
            "premium_tokens": self.premium_tokens,
            "premium_cost_usd": round(self.premium_cost_usd, 6),
            "remaining_fraction": round(self.remaining_fraction, 4),
            "premium_remaining_fraction": round(self.premium_remaining_fraction, 4),
            "per_slot": {
                s.value: {
                    "model_id": self.cfg.slot(s).model_id,
                    "price_verified": self.cfg.slot(s).price_verified,
                    **vars(u),
                }
                for s, u in self.per_slot.items()
            },
        }
