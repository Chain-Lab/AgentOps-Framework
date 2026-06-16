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
    ROLLOUT_APPROVAL_REQUESTED = "policy.rollout.approval.requested"
    ROLLOUT_APPROVAL_APPROVED = "policy.rollout.approval.approved"
    ROLLOUT_APPROVAL_REJECTED = "policy.rollout.approval.rejected"
    ROLLOUT_APPROVAL_EXPIRED = "policy.rollout.approval.expired"
    ROLLOUT_APPROVAL_DECISION_RECORDED = "policy.rollout.approval.decision_recorded"
    ROLLOUT_APPROVAL_QUORUM_REACHED = "policy.rollout.approval.quorum_reached"
    ROLLOUT_APPROVAL_POLICY_DENIED = "policy.rollout.approval.policy_denied"
    RUNTIME_POLICY_EVALUATED = "policy.runtime.evaluated"
    RUNTIME_POLICY_RULE_CREATED = "policy.runtime.rule.created"
    RUNTIME_POLICY_RULE_ENABLED = "policy.runtime.rule.enabled"
    RUNTIME_POLICY_RULE_DISABLED = "policy.runtime.rule.disabled"
    OBSERVABILITY_REPORT_GENERATED = "policy.observability.report_generated"
    OBSERVABILITY_EXPORT_GENERATED = "policy.observability.export_generated"
    OBSERVABILITY_EXPORT_FAILED = "policy.observability.export_failed"
    SIMULATION_VALIDATION_RUN = "policy.simulation.validation_run"
    SIMULATION_REPLAY_RUN = "policy.simulation.replay_run"
    SIMULATION_EXPORT_GENERATED = "policy.simulation.export_generated"
    SIMULATION_PERMISSION_DENIED = "policy.simulation.permission_denied"


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
