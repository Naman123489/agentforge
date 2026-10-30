"""ModelRouter: the single entry point agents use for LLM calls.

    router = ModelRouter.from_env(run_id="run-42", on_event=bus.publish)
    result = await router.call("planner", messages, schema=Plan)                 # role default
    result = await router.call("backend", messages, task_type="debug",
                               failed_attempts=stage.retries)                    # with metadata

Each call: decide -> call model -> on failure classify, update health, and
re-decide with that slot excluded (or retry it once if transient). Stops with
RouterStopped instead of looping or spending past the budget.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import random
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from .budget import AccountLedger, RunBudget
from .config import RouterConfig, Slot
from .errors import InvalidOutput, RouterStopped, classify_error
from .health import ErrorKind, HealthTracker
from .policy import Action, decide
from .task import AgentRole, TaskProfile, TaskType, estimate_tokens

log = logging.getLogger("agentforge.router")

EventSink = Callable[[dict], Any | Awaitable[Any]]
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)


@dataclass
class RouterResult:
    content: str
    parsed: Any | None
    slot: Slot
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None  # None = model has no configured price
    latency_s: float
    attempts: int
    trace: list[dict] = field(default_factory=list)


class ModelRouter:
    def __init__(
        self,
        cfg: RouterConfig,
        client: Any = None,
        *,
        run_id: str = "run",
        health: HealthTracker | None = None,
        account: AccountLedger | None = None,
        on_event: EventSink | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.cfg = cfg
        self.client = client  # explicit client (tests, custom transport) is used for every slot
        self._clients: dict[tuple[str, str], Any] = {}
        self.run_id = run_id
        self.health = health or HealthTracker(cfg.health, clock)
        self.account = account or AccountLedger(cfg)
        self.budget = RunBudget(cfg, run_id)
        self.on_event = on_event
        self._sleep = sleep
        self._clock = clock
        self._event_seq = 0
        self._budget_warned = False

    @classmethod
    def from_env(cls, run_id: str = "run", on_event: EventSink | None = None, config_path: str | None = None):
        return cls(RouterConfig.load(config_path), run_id=run_id, on_event=on_event)

    def for_run(self, run_id: str, on_event: EventSink | None = None,
                budget: dict | BaseModel | None = None) -> "ModelRouter":
        """New per-run budget; shares client, model health and account ledger.

        budget: the platform contract's RunBudget (max_tokens, max_cost_usd, max_premium_cost_usd). These
        limits are enforced here, server-side. max_wall_time_seconds / max_retries_per_stage belong to the
        orchestrator and are ignored by the router.
        """
        cfg = self.cfg
        if budget is not None:
            b = budget.model_dump(exclude_none=True) if isinstance(budget, BaseModel) else dict(budget)
            over = {k: b[src] for src, k in (("max_tokens", "run_token_limit"), ("max_cost_usd", "run_cost_limit_usd"),
                                             ("max_premium_cost_usd", "premium_cost_limit_usd")) if b.get(src) is not None}
            cfg = cfg.model_copy(update={"budget": cfg.budget.model_copy(update=over)})
        return ModelRouter(cfg, self.client, run_id=run_id, health=self.health, account=self.account,
                           on_event=on_event or self.on_event, sleep=self._sleep, clock=self._clock)

    def stats(self) -> dict:
        return {**self.budget.snapshot(), "health": self.health.snapshot(),
                "credit_remaining_usd": self.account.credit_remaining_usd}

    def usage_summary(self) -> dict:
        """Contract UsageSummary for this run. Costs are estimates from configured prices; the provider does
        not confirm billing per call, so confirmed_cost_usd is always null (contract guide rule 7)."""
        b, by_model = self.budget, {}
        for slot, u in b.per_slot.items():
            mid = self.cfg.slot(slot).model_id
            m = by_model.setdefault(mid, {"model_id": mid, "input_tokens": 0, "output_tokens": 0,
                                          "estimated_cost_usd": 0.0, "calls": 0})
            m["input_tokens"] += u.input_tokens
            m["output_tokens"] += u.output_tokens
            m["estimated_cost_usd"] = round(m["estimated_cost_usd"] + u.cost_usd, 6)
            m["calls"] += u.calls
        inp = sum(u.input_tokens for u in b.per_slot.values())
        out = sum(u.output_tokens for u in b.per_slot.values())
        has_free = any(self.cfg.slot(s).billing == "free_allowance" for s in Slot)
        rem = b.cost_remaining_usd
        return {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out,
                "estimated_cost_usd": round(b.cost_usd, 6), "confirmed_cost_usd": None,
                "premium_cost_usd": round(b.premium_cost_usd, 6),
                "free_allowance_used": sum(self.account.free_used.values()) if has_free else None,
                "remaining_budget_usd": None if rem is None else round(max(0.0, rem), 6),
                "by_model": [m for m in by_model.values() if m["calls"]]}

    def model_statuses(self) -> list[dict]:
        """Contract ModelStatus list for GET /models. Never includes keys or endpoints' credentials."""
        names = {Slot.A: ("premium", "premium reasoning: planning, contracts, critical review, escalations"),
                 Slot.B: ("coding", "main coding: backend/frontend code, debugging, test repair"),
                 Slot.C: ("economy", "economy: log summaries, error classification, commit messages")}
        out = []
        for s in Slot:
            sc, h, av = self.cfg.slot(s), self.health.slots[s], self.health.availability(s)
            if not sc.enabled or h.disabled_reason or av.reason == "circuit_open":
                health = "unavailable"
            elif av.reason in ("rate_limited", "slow", "half_open_probe"):
                health = "degraded"
            else:
                health = "healthy" if h.successes else "unknown"
            if sc.billing == "local":
                price = "verified"  # self-hosted: $0 per token by definition
            else:
                price = "verified" if sc.has_price and sc.price_verified else ("estimated" if sc.has_price else "unknown")
            out.append({"slot": names[s][0], "model_id": sc.model_id, "role": names[s][1], "enabled": sc.enabled,
                        "health": health, "supports_tools": None,
                        "supports_structured_output": sc.supports_json_schema or sc.supports_json_mode,
                        "context_window": sc.context_window, "price_status": price})
        return out

    # ------------------------------------------------------------------------------
    async def call(
        self,
        role: AgentRole | str,
        messages: list[dict],
        schema: type[BaseModel] | None = None,
        *,
        task: TaskProfile | None = None,
        task_type: TaskType | str | None = None,
        max_tokens: int | None = None,
        **task_overrides: Any,
    ) -> RouterResult:
        schema_json = schema.model_json_schema() if schema else None
        if task is None:
            task = TaskProfile.for_task(role, task_type, requires_structured_output=schema is not None,
                                        **task_overrides)
        if not task.input_tokens_est:
            task = task.model_copy(update={"input_tokens_est": estimate_tokens(messages, schema_json)})

        retry = self.cfg.retry
        excluded: dict[Slot, str] = {}
        transient_retries: dict[Slot, int] = defaultdict(int)
        trace: list[dict] = []
        msgs = list(messages)
        attempts = waits = repairs = 0
        pinned: Slot | None = None

        while attempts < retry.max_attempts_per_call:
            decision = decide(task, self.cfg, self.health, self.budget, self.account, excluded,
                              allow_wait=waits < 1)  # wait at most once per call, then fall back or stop
            sid = task.stage_id
            if decision.low_budget_mode and not self._budget_warned:
                self._budget_warned = True
                await self._emit("budget_warning", sid, **decision.inputs)
            chosen = decision.slot if decision.action is Action.CALL else None
            ev = await self._emit(
                "route_decision", sid, model_id=self.cfg.slot(chosen).model_id if chosen else None,
                reason=decision.reasons[-1] if decision.reasons else "",
                estimated_cost_usd=decision.estimates[chosen].est_cost_usd if chosen else None,
                task=_task_brief(task), **decision.to_event())
            trace.append(ev)

            if decision.action is Action.STOP:
                await self._stop_event(sid, decision.stop_reason or "stop", "; ".join(decision.reasons))
                raise RouterStopped(decision.stop_reason or "stop", "; ".join(decision.reasons), trace)
            if decision.action is Action.WAIT:
                waits += 1
                await self._sleep(decision.wait_s)
                continue

            slot = pinned if pinned in decision.candidates else decision.slot
            pinned = None
            attempts += 1
            sc = self.cfg.slot(slot)
            req = self._request(slot, msgs, schema, schema_json, task, max_tokens)
            await self._emit("model_call_started", sid, slot=slot.value, model_id=sc.model_id, attempt=attempts)
            t0 = self._clock()
            try:
                resp = await asyncio.wait_for(self._client_for(slot).chat.completions.create(**req),
                                              timeout=sc.timeout_s)
                latency = self._clock() - t0
                content, in_tok, out_tok = _extract(resp, req)
                est = self.account.estimate(slot, in_tok, out_tok)
                self.account.record(slot, est)  # tokens are spent even if the output is unusable
                self.budget.record_success(slot, in_tok, out_tok, est, latency)
                self.health.record_success(slot, latency, out_tok)
                parsed = _parse(content, schema) if schema else _require_text(content)
            except Exception as exc:  # noqa: BLE001 - every provider failure is classified below
                latency = self._clock() - t0
                kind, retry_after = classify_error(exc)
                if kind is not ErrorKind.INVALID_OUTPUT:
                    self.health.record_failure(slot, kind, retry_after, latency)
                    self.budget.record_failure(slot)
                trace.append(await self._emit("model_call_failed", sid, slot=slot.value, model_id=sc.model_id,
                                              error_kind=kind.value, error=str(exc)[:300],
                                              latency_s=round(latency, 3), attempt=attempts))
                if kind is ErrorKind.AUTH:
                    await self._stop_event(sid, "auth_failed", "provider rejected the API key")
                    raise RouterStopped("auth_failed", "provider rejected the API key", trace) from exc
                if kind is ErrorKind.PAYMENT:  # every model shares the account: retrying or falling back cannot help
                    detail = f"provider account or API key has no budget left (HTTP 402): {str(exc)[:200]}"
                    await self._stop_event(sid, "provider_budget_exhausted", detail)
                    raise RouterStopped("provider_budget_exhausted", detail, trace) from exc
                if kind is ErrorKind.INVALID_OUTPUT:
                    if repairs < retry.max_structured_repairs:
                        repairs += 1
                        # The rejected reply is quoted in a user turn, not replayed as an assistant turn: reasoning
                        # models (Beam) answer 400 to an assistant turn without its reasoning (run_ebc39e4b1766).
                        msgs = msgs + [
                            {"role": "user", "content": f"Your previous reply was rejected: {exc}.\n\nIt was:\n"
                                                        f"{_truncate(getattr(exc, 'raw', ''), 4000)}\n\nReply again "
                                                        "with only the corrected output, no prose."},
                        ]
                        pinned = slot
                        continue
                    msgs, repairs = list(messages), 0
                    excluded[slot] = "invalid output after repair"
                    task = task.model_copy(update={"failed_slots": [*task.failed_slots, slot]})
                    continue
                if (kind.transient and transient_retries[slot] < retry.same_slot_transient_retries
                        and self.health.availability(slot).available):
                    transient_retries[slot] += 1
                    await self._sleep(_backoff(retry.backoff_base_s, retry.backoff_max_s, transient_retries[slot]))
                    pinned = slot
                    continue
                if kind is not ErrorKind.RATE_LIMIT:  # rate limits are handled by health cooldown (enables WAIT)
                    excluded[slot] = kind.value
                continue

            trace.append(await self._emit(
                "model_call_ok", sid, slot=slot.value, model_id=sc.model_id, input_tokens=in_tok,
                output_tokens=out_tok, reasoning_tokens=_reasoning_tokens(resp),
                cost_usd=est.cost_usd, price_verified=sc.price_verified,
                latency_s=round(latency, 3), attempt=attempts, budget=self.budget.snapshot()))
            return RouterResult(content, parsed if schema else None, slot, sc.model_id, in_tok, out_tok,
                                est.cost_usd, latency, attempts, trace)

        detail = f"{attempts} attempts failed for {task.task_type.value}"
        await self._stop_event(task.stage_id, "max_attempts", detail)
        raise RouterStopped("max_attempts", detail, trace)

    async def _stop_event(self, stage_id: str | None, reason: str, detail: str) -> None:
        """Contract has no 'router_stop' type: budget stops are budget_exhausted, everything else is error."""
        kind = "budget_exhausted" if reason in ("run_budget_exhausted", "budget_exhausted", "provider_budget_exhausted") else "error"
        await self._emit(kind, stage_id, source="model_router", reason=reason, detail=detail[:500],
                         usage=self.usage_summary())

    def _client_for(self, slot: Slot):
        if self.client is not None:
            return self.client
        key = self.cfg.endpoint(slot)
        if key not in self._clients:
            self._clients[key] = _make_client(self.cfg, slot)
        return self._clients[key]

    def _budget_output_cap(self, slot: Slot, task: TaskProfile) -> int:
        """Largest max_tokens that keeps this call inside the run's remaining token, cost and premium limits.
        The pre-call check uses estimates; this cap makes the limits hold even if the model writes more."""
        sc, b, inp = self.cfg.slot(slot), self.budget, task.input_tokens_est
        caps = [b.tokens_remaining - inp]
        if slot is Slot.A:
            caps.append(b.premium_tokens_remaining - inp)
        if sc.billing != "local" and sc.price_output_per_1m:
            in_cost = inp * (sc.price_input_per_1m or 0) / 1e6
            for rem in (b.cost_remaining_usd, b.premium_cost_remaining_usd if slot is Slot.A else None,
                        self.account.credit_remaining_usd if sc.billing == "prepaid_credits" else None):
                if rem is not None:
                    caps.append(int((rem - in_cost) / sc.price_output_per_1m * 1e6))
        return max(1, min(caps))

    # ------------------------------------------------------------------------------
    def _request(self, slot: Slot, msgs: list[dict], schema, schema_json, task: TaskProfile,
                 max_tokens: int | None) -> dict:
        sc = self.cfg.slot(slot)
        room = max(256, sc.context_window - task.input_tokens_est)
        req: dict[str, Any] = {
            "model": sc.model_id,
            "messages": msgs,
            "temperature": sc.temperature,
            **({"top_p": sc.top_p} if sc.top_p is not None else {}),
            "max_tokens": min(max_tokens or sc.max_output_tokens, sc.max_output_tokens, room,
                              self._budget_output_cap(slot, task)),
        }
        if schema is not None:
            if sc.supports_json_schema:
                req["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": schema.__name__, "schema": schema_json, "strict": True}}
            else:
                if sc.supports_json_mode:
                    req["response_format"] = {"type": "json_object"}
                req["messages"] = [{"role": "system", "content": "Respond with a single JSON object that validates "
                                    f"against this JSON Schema, and nothing else:\n{json.dumps(schema_json)}"},
                                   *msgs]
        if sc.extra_body:
            req["extra_body"] = sc.extra_body
        return req

    async def _emit(self, type_: str, stage_id: str | None = None, **data: Any) -> dict:
        """Publish a contract RunEvent: {id, run_id, stage_id, type, timestamp (UTC ISO-8601), data}."""
        self._event_seq += 1
        ev = {"id": f"evt_{self.run_id}_{self._event_seq:05d}", "run_id": self.run_id, "stage_id": stage_id,
              "type": type_, "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
              "data": data}
        log.info("%s %s", type_, json.dumps(ev, default=str)[:2000])
        if self.on_event:
            r = self.on_event(ev)
            if inspect.isawaitable(r):
                await r
        return ev


# ---- helpers --------------------------------------------------------------------
def _make_client(cfg: RouterConfig, slot: Slot = Slot.B):
    from openai import AsyncOpenAI  # imported lazily so tests need no SDK or key

    base_url, key_env = cfg.endpoint(slot)
    key = os.environ.get(key_env)
    if not key:
        if cfg.provider.requires_api_key:
            raise RuntimeError(f"set {key_env} (see .env.example)")
        key = "local"  # vLLM without --api-key accepts any value
    # max_retries=0: the router owns retries and fallback; SDK retries would double-spend.
    return AsyncOpenAI(base_url=base_url, api_key=key, max_retries=0)


def _extract(resp: Any, req: dict) -> tuple[str, int, int]:
    content = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    if usage and getattr(usage, "prompt_tokens", None) is not None:
        return content, int(usage.prompt_tokens), int(usage.completion_tokens or 0)
    # provider omitted usage: estimate so budgets still move
    return content, estimate_tokens(req["messages"]), estimate_tokens(content)


def _reasoning_tokens(resp: Any) -> int | None:
    details = getattr(getattr(resp, "usage", None), "completion_tokens_details", None)
    return getattr(details, "reasoning_tokens", None) if details is not None else None


def _require_text(content: str) -> str:
    if not _THINK_RE.sub("", content).strip():
        raise _invalid("empty response", content)
    return content


def _parse(content: str, schema: type[BaseModel]) -> BaseModel:
    text = _FENCE_RE.sub("", _THINK_RE.sub("", content).strip()).strip()
    start = text.find("{")
    if start == -1:
        raise _invalid("no JSON object found", content)
    try:  # the first complete object; trailing text (a note, a second object) used to break the parse
        data, _ = json.JSONDecoder().raw_decode(text[start:])
        return schema.model_validate(data)
    except (ValidationError, ValueError) as e:
        raise _invalid(_truncate(str(e), 1500), content) from None


def _invalid(msg: str, raw: str) -> InvalidOutput:
    e = InvalidOutput(msg)
    e.raw = raw  # type: ignore[attr-defined]
    return e


def _backoff(base: float, cap: float, n: int) -> float:
    return min(cap, base * 2 ** (n - 1)) * random.uniform(0.8, 1.2)


def _truncate(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "..."


def _task_brief(t: TaskProfile) -> dict:
    return {"agent": t.agent_role.value, "task_type": t.task_type.value, "complexity": t.complexity.value,
            "risk": t.risk.value, "failed_attempts": t.failed_attempts, "input_tokens_est": t.input_tokens_est,
            "output_tokens_est": t.output_tokens_est, "stage_id": t.stage_id}
