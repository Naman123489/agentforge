"""Task metadata the router decides on.

The orchestrator should build a TaskProfile with deterministic metadata it
already knows (agent, task type, stage retry count). `TaskProfile.for_task`
fills in sensible defaults per task type. No LLM is used to classify tasks.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .config import Slot


class AgentRole(str, Enum):
    PLANNER = "planner"
    BACKEND = "backend"
    FRONTEND = "frontend"
    REVIEWER = "reviewer"
    DEPLOY = "deploy"
    ORCHESTRATOR = "orchestrator"


class TaskType(str, Enum):
    PLAN = "plan"
    API_CONTRACT = "api_contract"
    REVIEW_JUDGMENT = "review_judgment"
    COMPLEX_DEBUG = "complex_debug"
    CODE_BACKEND = "code_backend"
    CODE_FRONTEND = "code_frontend"
    DEBUG = "debug"
    TEST_REPAIR = "test_repair"
    INTEGRATION = "integration"
    SUMMARIZE_LOG = "summarize_log"
    CLASSIFY_ERROR = "classify_error"
    EXTRACT_CONTEXT = "extract_context"
    COMMIT_MESSAGE = "commit_message"
    SIMPLE_EDIT = "simple_edit"
    DEPLOY_SCRIPT = "deploy_script"


class Level(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def n(self) -> int:
        return ["low", "medium", "high", "critical"].index(self.value)


class Need(str, Enum):
    REASONING = "reasoning"
    CODING = "coding"


# task type -> (preferred slot, default complexity, default risk, dominant capability)
TASK_DEFAULTS: dict[TaskType, tuple[Slot, Level, Level, Need]] = {
    TaskType.PLAN: (Slot.A, Level.HIGH, Level.CRITICAL, Need.REASONING),
    TaskType.API_CONTRACT: (Slot.A, Level.HIGH, Level.CRITICAL, Need.REASONING),
    TaskType.REVIEW_JUDGMENT: (Slot.A, Level.HIGH, Level.HIGH, Need.REASONING),
    TaskType.COMPLEX_DEBUG: (Slot.A, Level.HIGH, Level.HIGH, Need.REASONING),
    TaskType.CODE_BACKEND: (Slot.B, Level.MEDIUM, Level.MEDIUM, Need.CODING),
    TaskType.CODE_FRONTEND: (Slot.B, Level.MEDIUM, Level.MEDIUM, Need.CODING),
    TaskType.DEBUG: (Slot.B, Level.MEDIUM, Level.MEDIUM, Need.CODING),
    TaskType.TEST_REPAIR: (Slot.B, Level.MEDIUM, Level.MEDIUM, Need.CODING),
    TaskType.INTEGRATION: (Slot.B, Level.HIGH, Level.HIGH, Need.CODING),
    TaskType.SUMMARIZE_LOG: (Slot.C, Level.LOW, Level.LOW, Need.REASONING),
    TaskType.CLASSIFY_ERROR: (Slot.C, Level.LOW, Level.LOW, Need.REASONING),
    TaskType.EXTRACT_CONTEXT: (Slot.C, Level.LOW, Level.LOW, Need.REASONING),
    TaskType.COMMIT_MESSAGE: (Slot.C, Level.LOW, Level.LOW, Need.REASONING),
    TaskType.SIMPLE_EDIT: (Slot.C, Level.LOW, Level.LOW, Need.CODING),
    TaskType.DEPLOY_SCRIPT: (Slot.C, Level.LOW, Level.MEDIUM, Need.CODING),
}

# Default task when an agent calls router.call(role, ...) without a profile.
ROLE_DEFAULT_TASK: dict[AgentRole, TaskType] = {
    AgentRole.PLANNER: TaskType.PLAN,
    AgentRole.BACKEND: TaskType.CODE_BACKEND,
    AgentRole.FRONTEND: TaskType.CODE_FRONTEND,
    AgentRole.REVIEWER: TaskType.REVIEW_JUDGMENT,
    AgentRole.DEPLOY: TaskType.DEPLOY_SCRIPT,
    AgentRole.ORCHESTRATOR: TaskType.SUMMARIZE_LOG,
}

DEFAULT_OUTPUT_TOKENS: dict[Level, int] = {
    Level.LOW: 800,
    Level.MEDIUM: 4000,
    Level.HIGH: 8000,
    Level.CRITICAL: 8000,
}


class TaskProfile(BaseModel):
    agent_role: AgentRole
    task_type: TaskType
    complexity: Level
    risk: Level
    need: Need
    preferred_slot: Slot
    input_tokens_est: int = 0
    output_tokens_est: int = 0
    requires_structured_output: bool = False
    failed_attempts: int = 0  # task-level failures so far (stage retries, rejected reviews)
    is_retry: bool = False
    failed_slots: list[Slot] = Field(default_factory=list)  # slots whose output was rejected
    stage_id: str | None = None

    @classmethod
    def for_task(
        cls,
        agent_role: AgentRole | str,
        task_type: TaskType | str | None = None,
        *,
        messages: list[dict] | None = None,
        **overrides: Any,
    ) -> "TaskProfile":
        role = AgentRole(agent_role)
        ttype = TaskType(task_type) if task_type else ROLE_DEFAULT_TASK[role]
        slot, complexity, risk, need = TASK_DEFAULTS[ttype]
        complexity = Level(overrides.pop("complexity", complexity))
        data: dict[str, Any] = dict(
            agent_role=role,
            task_type=ttype,
            complexity=complexity,
            risk=risk,
            need=need,
            preferred_slot=slot,
            output_tokens_est=DEFAULT_OUTPUT_TOKENS[complexity],
        )
        if messages is not None:
            data["input_tokens_est"] = estimate_tokens(messages)
        data.update(overrides)
        data["is_retry"] = data.get("is_retry") or data.get("failed_attempts", 0) > 0
        return cls(**data)

    @property
    def total_tokens_est(self) -> int:
        return self.input_tokens_est + self.output_tokens_est


def estimate_tokens(messages: list[dict] | str, schema: dict | None = None) -> int:
    """Cheap, deterministic estimate (~4 chars per token, +10% safety margin)."""
    if isinstance(messages, str):
        chars = len(messages)
    else:
        chars = sum(len(m.get("content") or "") if isinstance(m.get("content"), str)
                    else len(json.dumps(m.get("content"))) for m in messages)
        chars += 16 * len(messages)
    if schema:
        chars += len(json.dumps(schema))
    return int(chars / 4 * 1.1) + 1
