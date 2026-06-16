"""Policy simulation models — for testing runtime policy rule changes against historical events.

Phase 40: Offline policy validation and historical replay framework.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class PolicySimulationOutcome(StrEnum):
    """Outcome of comparing baseline vs candidate policy decision."""
    UNCHANGED = "unchanged"
    WOULD_ALLOW = "would_allow"
    WOULD_DENY = "would_deny"
    WOULD_REQUIRE_APPROVAL = "would_require_approval"
    WOULD_CHANGE = "would_change"
    ERROR = "error"


class PolicySimulationCase(BaseModel):
    """A single case extracted from audit history for simulation."""
    case_id: str  # psc_ prefix
    action_type: str
    subject: str | None = None
    tool_name: str | None = None
    risk_level: str | None = None
    actor_id: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    baseline_status: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicySimulationResult(BaseModel):
    """Result of simulating a single case against candidate rules."""
    case_id: str
    baseline_status: str | None = None
    candidate_status: str | None = None
    outcome: PolicySimulationOutcome
    reason: str | None = None
    decision_id: str | None = None
    errors: list[str] = Field(default_factory=list)


class PolicySimulationSummary(BaseModel):
    """Aggregate summary of simulation outcomes."""
    total: int = 0
    unchanged: int = 0
    would_allow: int = 0
    would_deny: int = 0
    would_require_approval: int = 0
    would_change: int = 0
    errors: int = 0


class PolicySimulationReport(BaseModel):
    """Full simulation report comparing baseline vs candidate decisions."""
    simulation_id: str  # psim_ prefix
    name: str | None = None
    generated_at: datetime
    candidate_rule_ids: list[str] = Field(default_factory=list)
    summary: PolicySimulationSummary
    results: list[PolicySimulationResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("simulation_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("psim_"):
            raise ValueError("simulation_id must use psim_ prefix")
        return v

    @field_validator("generated_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        return v
