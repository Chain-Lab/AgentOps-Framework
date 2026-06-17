"""Rollout gate execution models — results of gate evaluation per rollout step.

Phase 43: Models for tracking rollout step gate automation outcomes.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class RolloutGateExecutionStatus(StrEnum):
    """Status of a rollout step gate execution."""
    NOT_REQUIRED = "not_required"
    SATISFIED = "satisfied"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


class RolloutGateExecutionResult(BaseModel):
    """Result of evaluating a simulation gate for a rollout step.

    Captures the outcome of ensure_step_gate, run_step_gate, and
    check_step_gate operations.
    """

    execution_id: str = Field(..., description="Unique execution result ID (rge_ prefix)")
    rollout_id: str = Field(..., description="Rollout plan ID")
    step_id: str = Field(..., description="Step ID within the rollout")
    status: RolloutGateExecutionStatus = Field(..., description="Gate execution status")
    requirement_id: str | None = Field(default=None, description="Gate requirement ID")
    gate_result_id: str | None = Field(default=None, description="Gate result ID")
    simulation_id: str | None = Field(default=None, description="Simulation report ID")
    action_taken: str | None = Field(default=None, description="Action taken by automation")
    reason: str | None = Field(default=None, description="Human-readable reason")
    error: dict[str, Any] | None = Field(default=None, description="Error details if status=ERROR")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    @field_validator("execution_id")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        if not v.startswith("rge_"):
            raise ValueError("execution_id must use rge_ prefix")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return v
