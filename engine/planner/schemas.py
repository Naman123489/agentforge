"""Planner output schemas (from ForgeAI ai1-planner, extended for AgentForge).

The model returns {plan, openapi_spec}. Everything here is validated before the orchestrator sees it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Owner = Literal["backend", "frontend"]


class Task(BaseModel):
    task_id: str
    title: str
    description: str
    owner: Owner
    endpoints: list[str] = Field(default_factory=list)  # "METHOD /api/path" this task implements (backend) or calls (frontend)
    dependencies: list[str] = Field(default_factory=list)
    acceptance_checks: list[str] = Field(min_length=1)
    files_allowed: list[str] = Field(default_factory=list)


class ProjectPlan(BaseModel):
    project_name: str
    summary: str
    requirements: list[str] = Field(default_factory=list)  # concrete features extracted from the request
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)  # ambiguities a human should confirm
    technology_stack: list[str] = Field(min_length=1)
    tasks: list[Task] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)


class PlannerOutput(BaseModel):
    plan: ProjectPlan
    openapi_spec: dict[str, Any]


class PlannerArtifacts(BaseModel):
    """What the Planner hands to the orchestrator (all validated)."""

    plan: ProjectPlan
    contract_yaml: str
    types_ts: str
    task_assignments: list[dict]
    parallel_groups: list[list[str]]  # task ids per wave; tasks in one wave have no dependency on each other
    problems: list[str] = Field(default_factory=list)  # empty == valid
    attempts: int = 0
    models_used: list[str] = Field(default_factory=list)
    tokens: int = 0
    cost_usd: float = 0.0
