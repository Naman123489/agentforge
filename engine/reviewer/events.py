
"""Stable event emission for the AI2 Reviewer."""

from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable
import json


EventCallback = Callable[[dict[str, Any]], None]


class EventEmitter:
    """Emit structured Reviewer events through a callback."""

    ALLOWED_EVENTS = {
        "review.started",
        "check.started",
        "check.result",
        "review.verdict",
        "checkpoint.created",
        "rollback.started",
        "rollback.finished",
        "run.escalated",
    }

    def __init__(
        self,
        run_id: str,
        callback: EventCallback | None = None,
        agent: str = "reviewer",
        *,
        strict_callback: bool = True,
    ) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must not be empty.")

        if not isinstance(agent, str) or not agent.strip():
            raise ValueError("agent must not be empty.")

        self.run_id = run_id
        self.callback = callback
        self.agent = agent
        self.strict_callback = strict_callback
        self._seq = 0
        self._lock = Lock()

    def emit(self, event: str, **fields: Any) -> dict[str, Any]:
        """Build an event, invoke the callback, and return the event."""
        if event not in self.ALLOWED_EVENTS:
            raise ValueError(f"Unsupported Reviewer event: {event}")

        reserved = {"run_id", "seq", "ts", "agent", "event"}
        conflicting = reserved.intersection(fields)

        if conflicting:
            raise ValueError(
                "Cannot override reserved event fields: "
                f"{sorted(conflicting)}"
            )

        with self._lock:
            self._seq += 1
            payload = {
                "event": event,
                "run_id": self.run_id,
                "seq": self._seq,
                "ts": datetime.now(timezone.utc).isoformat(),
                "agent": self.agent,
                **fields,
            }

        if self.callback is not None:
            try:
                self.callback(payload)
            except Exception:
                if self.strict_callback:
                    raise

        return payload


def json_line_callback(stream=None) -> EventCallback:
    """Return a callback that writes one JSON event per line."""
    import sys

    output = stream if stream is not None else sys.stdout

    def write_event(event: dict[str, Any]) -> None:
        output.write(json.dumps(event, default=str) + "\n")
        output.flush()

    return write_event

