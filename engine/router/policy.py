"""Deterministic routing policy.

`decide()` is a pure function of (task profile, config, health, budgets). It
never calls an LLM. It returns an ordered list of eligible slots plus the
reasons for every choice and every rejection, so the dashboard can show *why*
a model was picked.

Order of reasoning:
  1. Capability floor   - the weakest slot that can do this task safely.
  2. Target slot        - task-type default, then promotion/escalation on failures.
  3. Premium gate       - is slot A affordable and justified right now?
  4. Expected cost      - when prices are known, premium is used if it is
                          cheaper *to success*, not just cheaper per call.
  5. Low-budget mode    - reserve premium for critical work; downgrade low-risk work.
  6. Eligibility        - enabled, healthy, context fits, budget fits.
  7. Outcome            - CALL (ordered candidates), WAIT (brief rate limit), or STOP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .budget import AccountLedger, RunBudget
from .config import RouterConfig, Slot
from .health import HealthTracker
from .task import Level, Need, TaskProfile

FALLBACK_ORDER: dict[Slot, list[Slot]] = {
    Slot.A: [Slot.A, Slot.B, Slot.C],
    Slot.B: [Slot.B, Slot.A, Slot.C],
    Slot.C: [Slot.C, Slot.B],  # never burn premium on economy work
}
COMPLEXITY_EXPONENT = {Level.LOW: 1, Level.MEDIUM: 2, Level.HIGH: 3, Level.CRITICAL: 4}


class Action(str, Enum):
    CALL = "call"
    WAIT = "wait"
    STOP = "stop"


@dataclass
class SlotEstimate:
    est_cost_usd: float | None
    p_success: float
    expected_cost_usd: float | None


@dataclass
class RoutingDecision:
    action: Action
    candidates: list[Slot] = field(default_factory=list)
    target: Slot | None = None
    floor: Slot = Slot.C
    reasons: list[str] = field(default_factory=list)
    rejected: dict[Slot, str] = field(default_factory=dict)
    estimates: dict[Slot, SlotEstimate] = field(default_factory=dict)
    low_budget_mode: bool = False
    wait_s: float = 0.0
    stop_reason: str | None = None
    inputs: dict = field(default_factory=dict)  # budget/allowance/health snapshot the decision was based on

    @property
    def slot(self) -> Slot | None:
        return self.candidates[0] if self.candidates else None

    def to_event(self) -> dict:
        return {
            "action": self.action.value,
            "slot": self.slot.value if self.slot and self.action is Action.CALL else None,
            "candidates": [s.value for s in self.candidates],
            "target": self.target.value if self.target else None,
            "floor": self.floor.value,
            "reasons": self.reasons,
            "rejected": {s.value: r for s, r in self.rejected.items()},
            "estimates": {s.value: vars(e) for s, e in self.estimates.items()},
            "low_budget_mode": self.low_budget_mode,
            "wait_s": round(self.wait_s, 2),
            "stop_reason": self.stop_reason,
            "inputs": self.inputs,
        }


def p_success(cfg: RouterConfig, slot: Slot, task: TaskProfile) -> float:
    """Prior probability that one attempt on `slot` succeeds. Heuristic; tune from bake-off data."""
    sc = cfg.slot(slot)
    cap = sc.reasoning if task.need is Need.REASONING else sc.coding
    p = cap ** COMPLEXITY_EXPONENT[task.complexity]
    p *= 0.6 ** task.failed_slots.count(slot)  # this model already failed this task
    return max(0.02, min(0.99, p))


def decide(
    task: TaskProfile,
    cfg: RouterConfig,
    health: HealthTracker,
    run: RunBudget,
    account: AccountLedger,
    excluded: dict[Slot, str] | None = None,
    allow_wait: bool = True,
) -> RoutingDecision:
    excluded = excluded or {}
    pol, bud = cfg.policy, cfg.budget
    d = RoutingDecision(action=Action.CALL)
    d.inputs = {
        "required_context_tokens": task.total_tokens_est,
        "run_tokens_remaining": run.tokens_remaining,
        "run_cost_remaining_usd": run.cost_remaining_usd,
        "run_remaining_fraction": round(run.remaining_fraction, 4),
        "premium_remaining_fraction": round(run.premium_remaining_fraction, 4),
        "credit_remaining_usd": account.credit_remaining_usd,
        "free_allowance_remaining": {s.value: account.free_remaining(s) for s in Slot
                                     if account.free_remaining(s) is not None},
        "health": {s.value: health.availability(s).reason for s in Slot},
        "excluded": {s.value: r for s, r in excluded.items()},
    }

    if run.exhausted:
        d.action, d.stop_reason = Action.STOP, "run_budget_exhausted"
        d.reasons.append(f"run budget exhausted ({run.tokens_used} tokens, ${run.cost_usd:.4f} known cost)")
        return d

    # ---- 1. capability floor ---------------------------------------------
    floor = Slot.C
    if task.risk.n >= Level.HIGH.n or task.complexity.n >= Level.HIGH.n:
        floor = Slot.B
        d.reasons.append(f"floor B: risk={task.risk.value}, complexity={task.complexity.value}")
    elif task.complexity.n >= Level.MEDIUM.n and task.need is Need.CODING:
        floor = Slot.B
        d.reasons.append("floor B: non-trivial code generation")
    elif task.requires_structured_output and task.complexity.n >= Level.MEDIUM.n:
        floor = Slot.B
        d.reasons.append("floor B: structured output on a non-trivial task")

    # ---- 2. target + failure-driven promotion ------------------------------
    target = task.preferred_slot
    d.reasons.append(f"default for {task.task_type.value}: slot {target.value}")
    if target is Slot.C and task.failed_attempts >= pol.promote_economy_after_failures:
        target = Slot.B
        floor = Slot.B
        d.reasons.append(f"promote C->B after {task.failed_attempts} failed attempt(s)")
    escalated = False
    escalate_after = pol.escalate_to_premium_after_failures_by_agent.get(
        task.agent_role.value, pol.escalate_to_premium_after_failures)
    if target is Slot.B and task.failed_attempts >= escalate_after:
        target, escalated = Slot.A, True
        d.reasons.append(f"escalate B->A after {task.failed_attempts} failed attempt(s)")
    critical = task.risk is Level.CRITICAL or escalated

    # ---- cost estimates per slot -------------------------------------------
    for s in Slot:
        sc = cfg.slot(s)
        out = sc.expected_output(task.output_tokens_est)
        est = account.estimate(s, task.input_tokens_est, out)
        p = p_success(cfg, s, task)
        expected = None
        if est.cost_usd is not None:
            expected = est.cost_usd / p + (1 - p) * pol.downstream_failure_cost_usd.get(task.risk.value, 0.0)
        d.estimates[s] = SlotEstimate(est.cost_usd, round(p, 4), expected)

    # ---- 5a. budget mode ----------------------------------------------------
    d.low_budget_mode = run.remaining_fraction < bud.low_budget_fraction or account.credit_low
    if d.low_budget_mode:
        d.reasons.append(
            f"low-budget mode (run remaining {run.remaining_fraction:.0%}"
            + (", account credits low" if account.credit_low else "") + ")"
        )

    # ---- 3. premium gate ----------------------------------------------------
    premium_block = _premium_block(task, cfg, run, d, critical)

    # ---- 4. expected cost: is premium worth it? ------------------------------
    # Routine work stays on B (brief 5.2): the cost comparison may move A-tasks down to B, but may only
    # move B-tasks up to A when the task is genuinely hard or high-impact.
    hard = task.complexity.n >= Level.HIGH.n or task.risk.n >= Level.HIGH.n
    if target in (Slot.A, Slot.B) and premium_block is None and not critical and (target is Slot.A or hard):
        ea, eb = d.estimates[Slot.A].expected_cost_usd, d.estimates[Slot.B].expected_cost_usd
        free = d.estimates[Slot.A].est_cost_usd == 0 and d.estimates[Slot.B].est_cost_usd == 0
        if ea is not None and eb is not None and not free:  # $0 local models: no price signal, use rules
            better = Slot.A if ea < eb else Slot.B
            if better is not target:
                d.reasons.append(f"expected cost to success: A ${ea:.4f} vs B ${eb:.4f} -> {better.value}")
                target = better
    if target is Slot.A and premium_block is not None:
        d.reasons.append(f"premium unavailable ({premium_block}) -> target B")
        target = Slot.B

    # ---- 5b. low-budget downgrade ------------------------------------------
    if d.low_budget_mode and target is Slot.B and floor is Slot.C and not critical:
        target = Slot.C
        d.reasons.append("low budget: low-risk task downgraded B->C")

    d.target, d.floor = target, floor

    # ---- 6. eligibility -----------------------------------------------------
    degraded: list[Slot] = []
    for s in FALLBACK_ORDER[target]:
        if s.rank < floor.rank:
            d.rejected[s] = f"below capability floor {floor.value}"
            continue
        reason = _ineligible(s, task, cfg, health, run, account, excluded, premium_block)
        if reason:
            d.rejected[s] = reason
            continue
        if health.availability(s).degraded:
            degraded.append(s)
        else:
            d.candidates.append(s)
    d.candidates.extend(degraded)  # slow models only as a last resort
    if degraded:
        d.reasons.append("deprioritised slow slot(s): " + ",".join(s.value for s in degraded))

    target_retry_in = health.availability(target).retry_in_s
    if (allow_wait and d.candidates and d.slot.rank > target.rank and not critical
            and d.rejected.get(target) in ("rate_limited", "circuit_open")
            and target_retry_in <= cfg.retry.max_rate_limit_wait_s):
        d.action, d.wait_s = Action.WAIT, target_retry_in
        d.reasons.append(f"{target.value} back in {target_retry_in:.1f}s: wait instead of upgrading to "
                         f"{d.slot.value}")
        return d

    if d.candidates:
        if d.slot is not target:
            d.reasons.append(f"using fallback {d.slot.value} ({d.rejected.get(target, 'degraded')})")
        return d

    # ---- 7. WAIT or STOP ---------------------------------------------------------
    waits = [health.availability(s).retry_in_s for s in d.rejected
             if d.rejected[s] in ("rate_limited", "circuit_open")]
    budget_words = ("budget", "credits", "allowance", "premium")
    if allow_wait and waits and min(waits) <= cfg.retry.max_rate_limit_wait_s:
        d.action, d.wait_s = Action.WAIT, min(waits)
        d.reasons.append(f"all eligible models cooling down; wait {d.wait_s:.1f}s")
    else:
        d.action = Action.STOP
        in_floor = [r for s, r in d.rejected.items() if s.rank >= floor.rank]
        d.stop_reason = ("budget_exhausted" if in_floor and all(any(w in r for w in budget_words) for r in in_floor)
                         else "no_eligible_model")
        d.reasons.append(f"stop: {d.stop_reason}")
    return d


def _premium_block(task: TaskProfile, cfg: RouterConfig, run: RunBudget, d: RoutingDecision,
                   critical: bool) -> str | None:
    """Return why slot A may not be used for this task, or None if it may."""
    bud = cfg.budget
    tokens = task.input_tokens_est + cfg.slot(Slot.A).expected_output(task.output_tokens_est)
    cost = d.estimates[Slot.A].est_cost_usd
    if tokens > run.premium_tokens_remaining:
        return "premium token cap reached"
    rem_cost = run.premium_cost_remaining_usd
    if cost is not None and rem_cost is not None and cost > rem_cost:
        return "premium cost cap reached"
    if critical:
        return None
    if d.low_budget_mode:
        return "low-budget mode reserves premium for critical tasks"
    after = run.premium_remaining_fraction - tokens / bud.premium_token_limit
    if after < bud.premium_reserve_fraction:
        return "remaining premium budget reserved for critical tasks"
    return None


def _ineligible(s: Slot, task: TaskProfile, cfg: RouterConfig, health: HealthTracker, run: RunBudget,
                account: AccountLedger, excluded: dict[Slot, str], premium_block: str | None) -> str | None:
    sc = cfg.slot(s)
    if s in excluded:
        return f"excluded for this call: {excluded[s]}"
    if not sc.enabled:
        return "disabled in config"
    if s is Slot.A and premium_block:
        return premium_block
    av = health.availability(s)
    if not av.available:
        return av.reason
    out = sc.expected_output(task.output_tokens_est)
    if task.input_tokens_est + out > sc.context_window:
        return f"context too large ({task.input_tokens_est}+{out} > {sc.context_window})"
    est = account.estimate(s, task.input_tokens_est, out)
    if est.allowance_exhausted_without_price:
        return "free allowance exhausted and no price configured"
    if task.input_tokens_est + out > run.tokens_remaining:
        return "run token budget insufficient"
    rem = run.cost_remaining_usd
    if est.cost_usd is not None and rem is not None and est.cost_usd > rem:
        return "run cost budget insufficient"
    credit = account.credit_remaining_usd
    if sc.billing == "prepaid_credits" and est.cost_usd is not None and credit is not None and est.cost_usd > credit:
        return "prepaid credits exhausted"
    return None
