"""Provider error classification and the router's stop signal."""

from __future__ import annotations

import asyncio

from .health import ErrorKind


class RouterStopped(Exception):
    """Raised when the router decides not to spend more tokens.

    The orchestrator should treat this as ESCALATE (stop and report), not retry.
    `reason` is a stable code: run_budget_exhausted, budget_exhausted,
    no_eligible_model, auth_failed, max_attempts.
    """

    def __init__(self, reason: str, detail: str = "", trace: list[dict] | None = None):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail
        self.trace = trace or []


class InvalidOutput(Exception):
    pass


def classify_error(exc: BaseException) -> tuple[ErrorKind, float | None]:
    """Map an exception (openai SDK or otherwise) to an ErrorKind and optional Retry-After seconds."""
    if isinstance(exc, InvalidOutput):
        return ErrorKind.INVALID_OUTPUT, None
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ErrorKind.TIMEOUT, None
    name = type(exc).__name__
    if name == "APITimeoutError":
        return ErrorKind.TIMEOUT, None
    if name == "APIConnectionError" or isinstance(exc, ConnectionError):
        return ErrorKind.CONNECTION, None

    status = getattr(exc, "status_code", None)
    msg = str(exc).lower()
    retry_after = _retry_after(exc)
    if status == 429:
        return ErrorKind.RATE_LIMIT, retry_after
    if status == 402:
        return ErrorKind.PAYMENT, None
    if status in (401, 403):
        return ErrorKind.AUTH, None
    if status == 404:
        return ErrorKind.MODEL_NOT_FOUND, None
    if status in (400, 413, 422):
        if any(k in msg for k in ("context length", "context_length", "maximum context", "too many tokens", "too long")):
            return ErrorKind.CONTEXT_LENGTH, None
        return ErrorKind.BAD_REQUEST, None
    if status is not None and status >= 500:
        return ErrorKind.SERVER_ERROR, retry_after
    return ErrorKind.SERVER_ERROR, None  # unknown failure: treat as transient, health tracker caps it


def _retry_after(exc: BaseException) -> float | None:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None
