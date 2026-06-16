"""Policy observability models — summary and report models for governance analytics.

Phase 39: Framework-level visibility into enforcement decisions and approval flows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class PolicyDecisionCount(BaseModel):
    """Count of decisions by status."""

    status: str
    count: int


class PolicyActionSummary(BaseModel):
    """Enforcement decision summary by action type."""

    action_type: str
    allowed: int = 0
    denied: int = 0
    approval_required: int = 0
    total: int = 0


class PolicyActorSummary(BaseModel):
    """Enforcement decision summary by actor."""

    actor_id: str
    allowed: int = 0
    denied: int = 0
    approval_required: int = 0
    total: int = 0


class PolicyToolSummary(BaseModel):
    """Enforcement decision summary by tool."""

    tool_name: str
    allowed: int = 0
    denied: int = 0
    approval_required: int = 0
    total: int = 0


class ApprovalLatencySummary(BaseModel):
    """Summary of approval resolution times."""

    count: int
    average_seconds: float | None = None
    min_seconds: float | None = None
    max_seconds: float | None = None


class PolicyObservabilityReport(BaseModel):
    """Aggregated governance analytics report."""

    report_id: str  # por_ prefix
    generated_at: datetime
    window_start: datetime | None = None
    window_end: datetime | None = None
    total_decisions: int = 0
    decisions_by_status: list[PolicyDecisionCount] = Field(default_factory=list)
    actions: list[PolicyActionSummary] = Field(default_factory=list)
    actors: list[PolicyActorSummary] = Field(default_factory=list)
    tools: list[PolicyToolSummary] = Field(default_factory=list)
    approval_latency: ApprovalLatencySummary | None = None
    top_denials: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("report_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("por_"):
            raise ValueError("report_id must use por_ prefix")
        return v

    @field_validator("generated_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        return v
