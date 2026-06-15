"""Policy change event model — records significant policy lifecycle transitions."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class PolicyChangeEventType(StrEnum):
    """Types of policy change events."""

    BUNDLE_CREATED = "policy.bundle.created"
    GATE_COMPLETED = "policy.gate.completed"
    PROMOTION_EXECUTED = "policy.promotion.executed"
    ACTIVATION_CREATED = "policy.activation.created"
    ACTIVATION_ROLLED_BACK = "policy.activation.rolled_back"
    ENVIRONMENT_DISABLED = "policy.environment.disabled"
    ENVIRONMENT_ENABLED = "policy.environment.enabled"
    RING_ASSIGNED = "policy.ring.assigned"
    RING_PROMOTED = "policy.ring.promoted"
    RING_DISABLED = "policy.ring.disabled"
    RING_ENABLED = "policy.ring.enabled"
    MANUAL_RELOAD_REQUESTED = "policy.reload.requested"
    ROLLOUT_CREATED = "policy.rollout.created"
    ROLLOUT_STARTED = "policy.rollout.started"
    ROLLOUT_STEP_SUCCEEDED = "policy.rollout.step_succeeded"
    ROLLOUT_COMPLETED = "policy.rollout.completed"
    ROLLOUT_FAILED = "policy.rollout.failed"
    ROLLOUT_CANCELLED = "policy.rollout.cancelled"


class PolicyChangeEvent(BaseModel):
    """Records a significant policy lifecycle transition."""

    event_id: str = Field(..., description="Unique event identifier (pce_ prefix)")
    event_type: PolicyChangeEventType = Field(..., description="Type of policy change event")
    environment: str | None = Field(default=None, description="Affected environment")
    ring_name: str | None = Field(default=None, description="Affected ring name")
    bundle_id: str | None = Field(default=None, description="Related policy bundle ID")
    activation_id: str | None = Field(default=None, description="Related activation ID")
    assignment_id: str | None = Field(default=None, description="Related ring assignment ID")
    actor_id: str | None = Field(default=None, description="ID of the actor who triggered the event")
    reason: str | None = Field(default=None, description="Human-readable reason for the change")
    data: dict[str, Any] = Field(default_factory=dict, description="Additional event data")
    created_at: datetime = Field(..., description="Timezone-aware creation timestamp")
