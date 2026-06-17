"""Policy expiration models — results of sweeping expired approvals and gate requirements.

Phase 44: Notification Hooks and Expiration Workers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class PolicyExpirationTargetType(StrEnum):
    """What kind of target is being expired."""
    ROLLOUT_APPROVAL = "rollout_approval"
    PROMOTION_GATE_REQUIREMENT = "promotion_gate_requirement"
    ROLLOUT_GATE_REQUIREMENT = "rollout_gate_requirement"


class PolicyExpirationAction(StrEnum):
    """Action taken during expiration sweep."""
    EXPIRED = "expired"
    SKIPPED = "skipped"
    ERROR = "error"


class PolicyExpirationResult(BaseModel):
    """Result of expiring a single target."""
    result_id: str = Field(..., description="Unique result ID (per_ prefix)")
    target_type: PolicyExpirationTargetType = Field(..., description="Type of expired target")
    target_id: str = Field(..., description="ID of the expired target")
    action: PolicyExpirationAction = Field(..., description="Action taken")
    reason: str | None = Field(default=None, description="Human-readable reason")
    error: dict[str, Any] | None = Field(default=None, description="Error details if action=ERROR")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")

    @field_validator("result_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("per_"):
            raise ValueError("result_id must use per_ prefix")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class PolicyExpirationSweepReport(BaseModel):
    """Report from an expiration sweep run."""
    sweep_id: str = Field(..., description="Unique sweep ID (pes_ prefix)")
    started_at: datetime = Field(..., description="Timezone-aware sweep start timestamp")
    completed_at: datetime | None = Field(default=None, description="Timezone-aware sweep completion timestamp")
    results: list[PolicyExpirationResult] = Field(default_factory=list, description="Individual expiration results")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Sweep metadata")

    @field_validator("sweep_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("pes_"):
            raise ValueError("sweep_id must use pes_ prefix")
        return v
