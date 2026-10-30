
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


# Internal results produced by the Reviewer check runner.
class InternalCheckResult(BaseModel):
    name: str
    passed: bool
    required: bool = True
    duration_s: float = Field(default=0.0, ge=0)
    exit_code: int | None = None
    log_tail: str = ""


class RequiredFix(BaseModel):
    owner: Literal["backend", "frontend", "planner", "shared"]
    file: str | None = None
    problem: str
    suggestion: str
    category: str | None = None  # AgentForge: set by model-assisted analysis


# Internal review report used by the Reviewer pipeline.
class InternalReviewReport(BaseModel):
    run_id: str
    stage_id: str
    attempt: int = Field(ge=1)
    verdict: Literal["approve", "reject", "blocked"]
    checks: list[InternalCheckResult] = Field(default_factory=list)
    required_fixes: list[RequiredFix] = Field(default_factory=list)
    summary: str
    models_used: dict[str, str] = Field(default_factory=dict)


# Platform API CheckResult.
class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    exit_code: int | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    log: str | None = None


# Platform API ReviewReport.
class ReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_id: str
    verdict: Literal["approve", "reject", "blocked"]
    checks: list[CheckResult]
    required_fixes: list[str] = Field(default_factory=list)
    created_at: str


# Platform API Checkpoint.
class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    run_id: str
    stage_id: str
    git_tag: str
    summary: str
    tests_passed: int = Field(ge=0)
    created_at: str


# Platform API ContractArtifact.
class ContractArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    path: str
    openapi_version: str
    valid: bool
    validation_errors: list[str] | None = None
    content: str | None = None
    updated_at: str | None = None


# Platform API BuildResult.
class BuildResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_id: str
    status: Literal["done", "failed", "contract_change_requested", "stopped"]
    files_changed: list[str]
    checks: list[CheckResult]
    models_used: dict[str, str]
    tokens: dict[str, int]
    cost_usd: float | None
    iterations: int = Field(ge=0)
    notes: str


# Platform API TaskAssignment.
class TaskAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_id: str
    agent: str
    instructions: str
    contract_ref: str | None = None
    files_allowed: list[str]
    failed_attempts: int = Field(ge=0)
    reviewer_failures: list[str]
    budget_context: dict


# Platform API ContractChangeRequest.
class ContractChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem: str
    proposed_change: str
    reason: str
