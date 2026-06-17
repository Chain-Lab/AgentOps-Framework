"""Rollout history models — event types, history events, timelines, and analytics reports.

Phase 45: Rollout history tracking, timeline reconstruction, and analytics.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class RolloutHistoryEventType(StrEnum):
    """Types of events recorded during a policy rollout."""

    ROLLOUT_CREATED = "rollout.created"
    ROLLOUT_STARTED = "rollout.started"
    ROLLOUT_CANCELLED = "rollout.cancelled"
    ROLLOUT_COMPLETED = "rollout.completed"
    ROLLOUT_FAILED = "rollout.failed"
    STEP_STARTED = "rollout.step.started"
    STEP_SUCCEEDED = "rollout.step.succeeded"
    STEP_BLOCKED = "rollout.step.blocked"
    STEP_FAILED = "rollout.step.failed"
    STEP_SKIPPED = "rollout.step.skipped"
    APPROVAL_REQUESTED = "rollout.approval.requested"
    APPROVAL_DECISION_RECORDED = "rollout.approval.decision_recorded"
    APPROVAL_APPROVED = "rollout.approval.approved"
    APPROVAL_REJECTED = "rollout.approval.rejected"
    APPROVAL_EXPIRED = "rollout.approval.expired"
    GATE_RUN = "rollout.gate.run"
    GATE_SATISFIED = "rollout.gate.satisfied"
    GATE_BLOCKED = "rollout.gate.blocked"
    GATE_FAILED = "rollout.gate.failed"
    GATE_SKIPPED = "rollout.gate.skipped"
    GATE_EXPIRED = "rollout.gate.expired"
    NOTIFICATION_CREATED = "rollout.notification.created"
    NOTIFICATION_SENT = "rollout.notification.sent"
    NOTIFICATION_FAILED = "rollout.notification.failed"


class RolloutHistoryEvent(BaseModel):
    """A single event recorded during a policy rollout."""

    history_event_id: str = Field(..., description="Unique event identifier (rhe_ prefix)")
    rollout_id: str = Field(..., description="ID of the rollout this event belongs to")
    event_type: RolloutHistoryEventType = Field(..., description="Type of rollout history event")
    step_id: str | None = Field(default=None, description="Related step ID, if applicable")
    environment: str | None = Field(default=None, description="Affected environment")
    ring_name: str | None = Field(default=None, description="Affected ring name")
    actor_id: str | None = Field(default=None, description="ID of the actor who triggered the event")
    source_type: str | None = Field(default=None, description="Source type (e.g. rollout_step, gate, approval)")
    source_id: str | None = Field(default=None, description="Source entity ID")
    message: str | None = Field(default=None, description="Human-readable event message")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional event metadata")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")

    @field_validator("history_event_id")
    @classmethod
    def _validate_id_prefix(cls, v: str) -> str:
        if not v.startswith("rhe_"):
            raise ValueError(f"ID must start with 'rhe_', got '{v}'")
        return v

    @field_validator("created_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("created_at must be timezone-aware")
        return v


class RolloutStepTimeline(BaseModel):
    """Timeline view for a single rollout step, with associated events."""

    step_id: str = Field(..., description="Step identifier")
    step_type: str | None = Field(default=None, description="Step type")
    environment: str | None = Field(default=None, description="Step environment")
    ring_name: str | None = Field(default=None, description="Step ring name")
    status: str | None = Field(default=None, description="Step status")
    started_at: datetime | None = Field(default=None, description="Step start timestamp")
    completed_at: datetime | None = Field(default=None, description="Step completion timestamp")
    duration_seconds: float | None = Field(default=None, description="Step duration in seconds")
    gate_status: str | None = Field(default=None, description="Gate status for this step")
    approval_status: str | None = Field(default=None, description="Approval status for this step")
    error: dict[str, Any] | None = Field(default=None, description="Error details if step failed")
    events: list[RolloutHistoryEvent] = Field(default_factory=list, description="Events for this step")


class RolloutTimeline(BaseModel):
    """Timeline view for an entire rollout, with steps and events."""

    rollout_id: str = Field(..., description="Rollout identifier")
    name: str | None = Field(default=None, description="Rollout plan name")
    bundle_id: str | None = Field(default=None, description="Related policy bundle ID")
    status: str | None = Field(default=None, description="Rollout status")
    created_at: datetime | None = Field(default=None, description="Rollout creation timestamp")
    started_at: datetime | None = Field(default=None, description="Rollout start timestamp")
    completed_at: datetime | None = Field(default=None, description="Rollout completion timestamp")
    duration_seconds: float | None = Field(default=None, description="Rollout duration in seconds")
    steps: list[RolloutStepTimeline] = Field(default_factory=list, description="Step timelines")
    events: list[RolloutHistoryEvent] = Field(default_factory=list, description="Rollout-level events")


class RolloutGateOutcomeSummary(BaseModel):
    """Summary of gate outcomes across a rollout or set of rollouts."""

    total: int = 0
    satisfied: int = 0
    blocked: int = 0
    failed: int = 0
    skipped: int = 0
    expired: int = 0


class RolloutApprovalOutcomeSummary(BaseModel):
    """Summary of approval outcomes across a rollout or set of rollouts."""

    total: int = 0
    pending: int = 0
    approved: int = 0
    rejected: int = 0
    expired: int = 0
    average_latency_seconds: float | None = None


class RolloutAnalyticsReport(BaseModel):
    """Aggregated analytics report for rollout history."""

    report_id: str = Field(..., description="Unique report identifier (rar_ prefix)")
    generated_at: datetime = Field(..., description="Timezone-aware report generation timestamp")
    window_start: datetime | None = Field(default=None, description="Report window start")
    window_end: datetime | None = Field(default=None, description="Report window end")
    total_rollouts: int = 0
    completed_rollouts: int = 0
    failed_rollouts: int = 0
    cancelled_rollouts: int = 0
    blocked_rollouts: int = 0
    gate_outcomes: RolloutGateOutcomeSummary = Field(default_factory=RolloutGateOutcomeSummary)
    approval_outcomes: RolloutApprovalOutcomeSummary = Field(default_factory=RolloutApprovalOutcomeSummary)
    top_blocked_steps: list[dict[str, Any]] = Field(default_factory=list)
    top_failed_gates: list[dict[str, Any]] = Field(default_factory=list)
    environment_summary: list[dict[str, Any]] = Field(default_factory=list)
    ring_summary: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("report_id")
    @classmethod
    def _validate_id_prefix(cls, v: str) -> str:
        if not v.startswith("rar_"):
            raise ValueError(f"ID must start with 'rar_', got '{v}'")
        return v

    @field_validator("generated_at")
    @classmethod
    def _validate_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("generated_at must be timezone-aware")
        return v
