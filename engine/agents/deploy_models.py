"""Contract-shaped models owned by the standalone deploy agent."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import re

from pydantic import BaseModel, Field, field_validator

_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_TAG = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")


class CheckResult(BaseModel):
    name: str
    passed: bool
    exit_code: int | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    log: str = ""


class DeployRequest(BaseModel):
    run_id: str
    stage_id: str
    checkpoint_id: str | None = None
    git_tag: str | None = None
    repo_name: str
    visibility: Literal["private", "public"] = "private"
    target: str = "disabled"
    health_check_path: str = "/health"
    rollback_on_failure: bool = True

    @field_validator("run_id", "stage_id")
    @classmethod
    def _ids(cls, v: str) -> str:
        if not _ID.fullmatch(v) or ".." in v:
            raise ValueError("must be 1-128 characters of letters, digits, '.', '_' or '-'")
        return v

    @field_validator("checkpoint_id")
    @classmethod
    def _checkpoint(cls, v: str | None) -> str | None:
        if v is not None and (not _ID.fullmatch(v) or ".." in v):
            raise ValueError("invalid checkpoint id")
        return v

    @field_validator("git_tag")
    @classmethod
    def _tag(cls, v: str | None) -> str | None:
        if v is not None and (not _TAG.fullmatch(v) or ".." in v or v.startswith(("-", "/")) or v.endswith(("/", ".lock"))):
            raise ValueError("invalid git tag")
        return v

    @field_validator("repo_name")
    @classmethod
    def _repo(cls, v: str) -> str:
        if not 1 <= len(v) <= 100 or any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
            raise ValueError("repo_name must be 1-100 printable characters")
        return v

    @field_validator("health_check_path")
    @classmethod
    def _health(cls, v: str) -> str:
        if (not v.startswith("/") or v.startswith("//") or ".." in v or len(v) > 200
                or any(ch.isspace() or ord(ch) < 32 for ch in v) or "://" in v):
            raise ValueError("health_check_path must be a plain absolute path such as /health")
        return v


class RepoInfo(BaseModel):
    html_url: str
    clone_url: str
    default_branch: str = "main"


class DeploymentStatus(BaseModel):
    id: str
    status: Literal["queued", "deploying", "healthy", "failed", "rolled_back"]
    target: str
    url: str | None = None
    health_check_passed: bool | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class DeployResult(BaseModel):
    stage_id: str
    status: Literal["done", "failed", "stopped"]
    files_changed: list[str] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    models_used: list[str] = Field(default_factory=list)
    tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    iterations: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)
    repo_url: str | None = None
    commit_sha: str | None = None
    source_commit: str | None = None
    git_tag: str | None = None
    deployment: DeploymentStatus | None = None
    files_pushed: list[str] = Field(default_factory=list)
