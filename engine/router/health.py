"""Per-model health: circuit breaker, rate-limit cooldown, latency tracking.

Shared across runs, because provider outages and rate limits are not per run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .config import HealthConfig, Slot


class ErrorKind(str, Enum):
    RATE_LIMIT = "rate_limit"          # 429: switch slot, cool down this one
    TIMEOUT = "timeout"                # transient
    SERVER_ERROR = "server_error"      # 5xx, transient
    CONNECTION = "connection"          # transient
    MODEL_NOT_FOUND = "model_not_found"  # 404: misconfigured id, disable slot
    CONTEXT_LENGTH = "context_length"  # request too large for this model
    BAD_REQUEST = "bad_request"        # 400/422: request-specific, not a health signal
    AUTH = "auth"                      # 401/403: shared key, stop everything
    PAYMENT = "payment_required"       # 402: provider account/key out of credit, stop everything
    INVALID_OUTPUT = "invalid_output"  # response failed schema validation

    @property
    def transient(self) -> bool:
        return self in (ErrorKind.TIMEOUT, ErrorKind.SERVER_ERROR, ErrorKind.CONNECTION)


@dataclass
class SlotHealth:
    consecutive_failures: int = 0
    open_until: float = 0.0
    half_open: bool = False
    rate_limited_until: float = 0.0
    disabled_reason: str | None = None
    latency_ewma_s: float | None = None
    s_per_1k_ewma: float | None = None  # seconds per 1,000 output tokens (long answers are not "slow")
    successes: int = 0
    failures: int = 0


@dataclass
class Availability:
    available: bool
    reason: str = "ok"
    retry_in_s: float = 0.0
    degraded: bool = False


class HealthTracker:
    def __init__(self, cfg: HealthConfig, clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg
        self.clock = clock
        self.slots: dict[Slot, SlotHealth] = {s: SlotHealth() for s in Slot}

    def availability(self, slot: Slot) -> Availability:
        h, now = self.slots[slot], self.clock()
        if h.disabled_reason:
            return Availability(False, f"disabled: {h.disabled_reason}")
        if h.rate_limited_until > now:
            return Availability(False, "rate_limited", h.rate_limited_until - now)
        if h.open_until > now:
            return Availability(False, "circuit_open", h.open_until - now)
        degraded = h.s_per_1k_ewma is not None and h.s_per_1k_ewma > self.cfg.slow_s_per_1k_tokens
        if h.open_until and not h.half_open:
            h.half_open = True  # cooldown elapsed: allow one probe call
        return Availability(True, "half_open_probe" if h.half_open else ("slow" if degraded else "ok"),
                            degraded=degraded)

    def record_success(self, slot: Slot, latency_s: float, output_tokens: int | None = None) -> None:
        h = self.slots[slot]
        h.successes += 1
        h.consecutive_failures = 0
        h.open_until = 0.0
        h.half_open = False
        h.latency_ewma_s = _ewma(h.latency_ewma_s, latency_s)
        h.s_per_1k_ewma = _ewma(h.s_per_1k_ewma, latency_s * 1000 / max(output_tokens or 1000, 200))

    def record_failure(self, slot: Slot, kind: ErrorKind, retry_after_s: float | None = None,
                       latency_s: float | None = None) -> None:
        h, now = self.slots[slot], self.clock()
        h.failures += 1
        if kind is ErrorKind.RATE_LIMIT:
            h.rate_limited_until = now + (retry_after_s or self.cfg.default_rate_limit_cooldown_s)
        elif kind is ErrorKind.MODEL_NOT_FOUND:
            h.disabled_reason = "model id not found at provider"
        elif kind.transient:
            if kind is ErrorKind.TIMEOUT and latency_s is not None:
                h.latency_ewma_s = _ewma(h.latency_ewma_s, latency_s)
                h.s_per_1k_ewma = _ewma(h.s_per_1k_ewma, latency_s)  # nothing came back: count it as 1k tokens
            h.consecutive_failures += 1
            if h.half_open or h.consecutive_failures >= self.cfg.failure_threshold:
                h.open_until = now + self.cfg.cooldown_s
                h.half_open = False
        # CONTEXT_LENGTH / BAD_REQUEST / INVALID_OUTPUT are about the request, not the model's health.

    def snapshot(self) -> dict:
        out = {}
        for s, h in self.slots.items():
            a = self.availability(s)
            out[s.value] = {"available": a.available, "reason": a.reason, "retry_in_s": round(a.retry_in_s, 1),
                            "latency_ewma_s": h.latency_ewma_s, "successes": h.successes, "failures": h.failures}
        return out


def _ewma(prev: float | None, x: float) -> float:
    return x if prev is None else 0.7 * prev + 0.3 * x
