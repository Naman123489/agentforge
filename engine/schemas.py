"""Typed messages between the orchestrator and the agents (FR-10).

Field names, types and enums follow contracts/agentforge-openapi.yaml (components.schemas TaskAssignment,
CheckResult, BuildResult, ContractChangeRequest). tests/test_contract_conformance.py validates these
models' JSON against that file, so a drift from the shared contract fails the build.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

AgentRole = Literal["planner", "backend", "frontend", "reviewer", "deploy"]


class CheckResult(BaseModel):
    name: str
    passed: bool
    exit_code: int | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    log: str = ""
    skipped: bool = False  # extension: check could not run (tool missing); counted as passed

    def as_failure_text(self, limit: int = 3500) -> str:
        return f"{self.name}: {self.log[-limit:]}"


class TaskAssignment(BaseModel):
    stage_id: str
    agent: AgentRole
    instructions: str = Field(min_length=1)
    contract_ref: str | None = "contract/openapi.yaml"  # per-run generated-app contract, relative to workspace
    files_allowed: list[str] = Field(default_factory=lambda: ["backend/**"])
    failed_attempts: int = Field(default=0, ge=0)  # stage retries so far; drives model escalation
    reviewer_failures: list[str] = Field(default_factory=list)  # failing-check texts from the Reviewer
    budget_context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("reviewer_failures", mode="before")
    @classmethod
    def _checks_to_text(cls, v):
        """Accept CheckResult objects/dicts too, converting them to the contract's string form."""
        out = []
        for item in v or []:
            if isinstance(item, CheckResult):
                out.append(item.as_failure_text())
            elif isinstance(item, dict) and "name" in item:
                out.append(CheckResult.model_validate(item).as_failure_text())
            else:
                out.append(str(item))
        return out


class ContractChangeRequest(BaseModel):
    problem: str = Field(min_length=1)
    proposed_change: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    affected_paths: list[str] = Field(default_factory=list)
    breaking_change: bool = False


class BuildResult(BaseModel):
    stage_id: str
    agent: AgentRole = "backend"  # extension (not required by the contract)
    status: Literal["done", "failed", "contract_change_requested", "stopped"]
    files_changed: list[str] = Field(default_factory=list)  # workspace-relative paths
    checks: list[CheckResult] = Field(default_factory=list)
    models_used: list[str] = Field(default_factory=list)
    tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)  # router estimate from configured prices (not a billed amount)
    iterations: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)
    contract_change: ContractChangeRequest | None = None  # extension: set when status=contract_change_requested
    unpriced_calls: int = Field(default=0, ge=0)  # extension: calls with no configured price (excluded from cost)

    @property
    def ok(self) -> bool:
        return self.status == "done"

    def to_contract(self) -> dict:
        """JSON for the platform API (Stage.result). Extensions kept only when set."""
        return self.model_dump(mode="json", exclude_none=True)
