"""AgentForge intelligent model router (three slots: A premium, B coding, C economy)."""

from .budget import AccountLedger, RunBudget
from .config import RouterConfig, Slot, SlotConfig
from .errors import RouterStopped
from .health import ErrorKind, HealthTracker
from .policy import Action, RoutingDecision, decide
from .router import ModelRouter, RouterResult
from .task import AgentRole, Level, TaskProfile, TaskType

__all__ = [
    "AccountLedger", "Action", "AgentRole", "ErrorKind", "HealthTracker", "Level", "ModelRouter",
    "RouterConfig", "RouterResult", "RouterStopped", "RoutingDecision", "RunBudget", "Slot",
    "SlotConfig", "TaskProfile", "TaskType", "decide",
]
