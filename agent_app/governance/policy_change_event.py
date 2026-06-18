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
    SIMULATION_GATE_RUN = "policy.simulation.gate_run"
    SIMULATION_GATE_PASSED = "policy.simulation.gate_passed"
    SIMULATION_GATE_FAILED = "policy.simulation.gate_failed"
    SIMULATION_GATE_PERMISSION_DENIED = "policy.simulation.gate_permission_denied"
    PROMOTION_GATE_REQUIRED = "policy.promotion.gate.required"
    PROMOTION_GATE_RUN = "policy.promotion.gate.run"
    PROMOTION_GATE_ATTACHED = "policy.promotion.gate.attached"
    PROMOTION_GATE_SATISFIED = "policy.promotion.gate.satisfied"
    PROMOTION_GATE_FAILED = "policy.promotion.gate.failed"
    PROMOTION_GATE_EXPIRED = "policy.promotion.gate.expired"
    PROMOTION_GATE_EXECUTION_BLOCKED = "policy.promotion.gate.execution_blocked"
    PROMOTION_GATE_PERMISSION_DENIED = "policy.promotion.gate.permission_denied"
    ROLLOUT_GATE_RUN = "policy.rollout.gate.run"
    ROLLOUT_GATE_SATISFIED = "policy.rollout.gate.satisfied"
    ROLLOUT_GATE_BLOCKED = "policy.rollout.gate.blocked"
    ROLLOUT_GATE_FAILED = "policy.rollout.gate.failed"
    ROLLOUT_GATE_SKIPPED = "policy.rollout.gate.skipped"
    ROLLOUT_GATE_ATTACHED = "policy.rollout.gate.attached"
    ROLLOUT_GATE_PERMISSION_DENIED = "policy.rollout.gate.permission_denied"
    NOTIFICATION_CREATED = "policy.notification.created"
    NOTIFICATION_SENT = "policy.notification.sent"
    NOTIFICATION_FAILED = "policy.notification.failed"
    NOTIFICATION_RULE_ENABLED = "policy.notification.rule.enabled"
    NOTIFICATION_RULE_DISABLED = "policy.notification.rule.disabled"
    EXPIRATION_SWEEP_STARTED = "policy.expiration.sweep_started"
    EXPIRATION_SWEEP_COMPLETED = "policy.expiration.sweep_completed"
    EXPIRATION_SWEEP_FAILED = "policy.expiration.sweep_failed"
    EXPIRATION_TARGET_EXPIRED = "policy.expiration.target_expired"
    EXPIRATION_PERMISSION_DENIED = "policy.expiration.permission_denied"
    ROLLOUT_HISTORY_RECORDED = "policy.rollout.history.recorded"
    ROLLOUT_HISTORY_VIEWED = "policy.rollout.history.viewed"
    ROLLOUT_TIMELINE_GENERATED = "policy.rollout.timeline.generated"
    ROLLOUT_ANALYTICS_GENERATED = "policy.rollout.analytics.generated"
    ROLLOUT_ANALYTICS_EXPORT_GENERATED = "policy.rollout.analytics.export_generated"
    ROLLOUT_ANALYTICS_EXPORT_FAILED = "policy.rollout.analytics.export_failed"
    ROLLOUT_ANALYTICS_PERMISSION_DENIED = "policy.rollout.analytics.permission_denied"
    FEDERATION_TARGET_CREATED = "policy.federation.target.created"
    FEDERATION_TARGET_ENABLED = "policy.federation.target.enabled"
    FEDERATION_TARGET_DISABLED = "policy.federation.target.disabled"
    FEDERATION_PLAN_CREATED = "policy.federation.plan.created"
    FEDERATION_PLAN_STARTED = "policy.federation.plan.started"
    FEDERATION_PLAN_COMPLETED = "policy.federation.plan.completed"
    FEDERATION_PLAN_FAILED = "policy.federation.plan.failed"
    FEDERATION_PLAN_CANCELLED = "policy.federation.plan.cancelled"
    FEDERATION_CONFLICT_DETECTED = "policy.federation.conflict.detected"


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
