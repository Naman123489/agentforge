"""Typed messages between agents and the orchestrator (FR-10)."""

from __future__ import annotations

import time
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---- planner output ---------------------------------------------------------------
class Field_(BaseModel):
    name: str
    type: Literal["string", "integer", "number", "boolean", "array", "object"] = "string"
    required: bool = True


class Endpoint(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str = Field(description="starts with /api/, e.g. /api/scores")
    summary: str = ""
    auth: bool = False
    request: list[Field_] = Field(default_factory=list, description="JSON body fields (POST/PUT/PATCH)")
    query: list[Field_] = Field(default_factory=list, description="query string parameters (GET)")
    response: list[Field_] = Field(default_factory=list, description="fields of the JSON object returned "
                                   "(or of each item when response_is_list is true)")
    response_is_list: bool = False


class Contract(BaseModel):
    """Simplified API contract both builders must follow (stands in for OpenAPI 3.1)."""
    endpoints: list[Endpoint]
    notes: str = ""


class Plan(BaseModel):
    title: str
    summary: str
    features: list[str]
    backend_files: list[str] = Field(description="paths under backend/, must include backend/app.py "
                                     "and backend/test_app.py")
    frontend_files: list[str] = Field(description="paths under frontend/, must include frontend/index.html")
    acceptance: list[str] = Field(description="machine-checkable acceptance checks")
    contract: Contract


# ---- builder output --------------------------------------------------------------
class FileOut(BaseModel):
    path: str
    content: str


class BuildResult(BaseModel):
    agent: Literal["backend", "frontend"]
    files: list[FileOut]
    notes: str = ""


# ---- reviewer output ---------------------------------------------------------------
class Check(BaseModel):
    name: str
    owner: Literal["backend", "frontend", "integration"]
    passed: bool
    log: str = ""


class ReviewReport(BaseModel):
    checks: list[Check]
    verdict: Literal["approve", "reject"]
    summary: str = ""
    required_fixes: dict[str, list[str]] = Field(default_factory=dict)  # owner -> fixes

    def failing(self, owner: str) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.owner in (owner, "integration")]


class Checkpoint(BaseModel):
    id: str
    stage: str
    git_tag: str
    summary: str
    tests_passed: list[str]
    created_at: float = Field(default_factory=time.time)


# ---- run state ---------------------------------------------------------------------
class RunState(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    BUILDING = "building"
    REVIEWING = "reviewing"
    FIXING = "fixing"
    ROLLED_BACK = "rolled_back"
    DONE = "done"
    FAILED = "failed"
    STOPPED = "stopped"


class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
