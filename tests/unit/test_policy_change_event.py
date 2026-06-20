"""Tests for PolicyChangeEventType and PolicyChangeEvent models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_change_event import (
    PolicyChangeEvent,
    PolicyChangeEventType,
)


class TestPolicyChangeEventType:
    """Tests for PolicyChangeEventType enum."""

    def test_all_event_types_valid(self) -> None:
        """All enum values are valid strings with expected dot-notation."""
        expected = {
            "policy.bundle.created",
            "policy.gate.completed",
            "policy.promotion.executed",
            "policy.activation.created",
            "policy.activation.rolled_back",
            "policy.environment.disabled",
            "policy.environment.enabled",
            "policy.ring.assigned",
            "policy.ring.promoted",
            "policy.ring.disabled",
            "policy.ring.enabled",
            "policy.reload.requested",
            "policy.rollout.created",
            "policy.rollout.started",
            "policy.rollout.step_succeeded",
            "policy.rollout.completed",
            "policy.rollout.failed",
            "policy.rollout.cancelled",
            "policy.rollout.approval.requested",
            "policy.rollout.approval.approved",
            "policy.rollout.approval.rejected",
            "policy.rollout.approval.expired",
            "policy.rollout.approval.decision_recorded",
            "policy.rollout.approval.quorum_reached",
            "policy.rollout.approval.policy_denied",
            "policy.runtime.evaluated",
            "policy.runtime.rule.created",
            "policy.runtime.rule.enabled",
            "policy.runtime.rule.disabled",
            "policy.observability.report_generated",
            "policy.observability.export_generated",
            "policy.observability.export_failed",
            "policy.simulation.validation_run",
            "policy.simulation.replay_run",
            "policy.simulation.export_generated",
            "policy.simulation.permission_denied",
            "policy.simulation.gate_run",
            "policy.simulation.gate_passed",
            "policy.simulation.gate_failed",
            "policy.simulation.gate_permission_denied",
            "policy.promotion.gate.required",
            "policy.promotion.gate.run",
            "policy.promotion.gate.attached",
            "policy.promotion.gate.satisfied",
            "policy.promotion.gate.failed",
            "policy.promotion.gate.expired",
            "policy.promotion.gate.execution_blocked",
            "policy.promotion.gate.permission_denied",
            "policy.rollout.gate.run",
            "policy.rollout.gate.satisfied",
            "policy.rollout.gate.blocked",
            "policy.rollout.gate.failed",
            "policy.rollout.gate.skipped",
            "policy.rollout.gate.attached",
            "policy.rollout.gate.permission_denied",
            "policy.notification.created",
            "policy.notification.sent",
            "policy.notification.failed",
            "policy.notification.rule.enabled",
            "policy.notification.rule.disabled",
            "policy.expiration.sweep_started",
            "policy.expiration.sweep_completed",
            "policy.expiration.sweep_failed",
            "policy.expiration.target_expired",
            "policy.expiration.permission_denied",
            "policy.rollout.history.recorded",
            "policy.rollout.history.viewed",
            "policy.rollout.timeline.generated",
            "policy.rollout.analytics.generated",
            "policy.rollout.analytics.export_generated",
            "policy.rollout.analytics.export_failed",
            "policy.rollout.analytics.permission_denied",
            "policy.federation.target.created",
            "policy.federation.target.enabled",
            "policy.federation.target.disabled",
            "policy.federation.plan.created",
            "policy.federation.plan.started",
            "policy.federation.plan.completed",
            "policy.federation.plan.failed",
            "policy.federation.plan.cancelled",
            "policy.federation.conflict.detected",
            "policy.federation.history.recorded",
            "policy.federation.history.viewed",
            "policy.federation.timeline.generated",
            "policy.federation.analytics.generated",
            "policy.federation.analytics.export_generated",
            "policy.federation.analytics.export_failed",
            "policy.federation.analytics.permission_denied",
            "policy.federation.approval.created",
            "policy.federation.approval.approved",
            "policy.federation.approval.rejected",
            "policy.federation.approval.escalated",
            "policy.federation.approval.cancelled",
            "policy.federation.approval.permission_denied",
            "policy.federation.notification.created",
            "policy.federation.notification.sent",
            "policy.federation.notification.failed",
            "policy.federation.approval.escalation_due",
            "policy.federation.approval.escalation_lock_skipped",
            "policy.federation.approval.escalation_worker_ticked",
            "policy.federation.notification.dlq_created",
            "policy.federation.notification.dlq_retried",
            "policy.federation.notification.dlq_purged",
            "policy.federation.worker.started",
            "policy.federation.worker.stopped",
            "policy.federation.worker.tick_failed",
        }
        actual = {member.value for member in PolicyChangeEventType}
        assert actual == expected

    def test_enum_member_count(self) -> None:
        """Exactly 106 enum members defined (100 previous + 6 Phase 50 DLQ/worker)."""
        assert len(PolicyChangeEventType) == 106

    def test_enum_is_str_subclass(self) -> None:
        """Enum values behave as strings."""
        val = PolicyChangeEventType.BUNDLE_CREATED
        assert isinstance(val, str)
        assert val == "policy.bundle.created"


class TestPolicyChangeEvent:
    """Tests for PolicyChangeEvent model."""

    def test_create_event(self) -> None:
        """Create event with all fields populated."""
        now = datetime.now(timezone.utc)
        event = PolicyChangeEvent(
            event_id="pce_abc123",
            event_type=PolicyChangeEventType.BUNDLE_CREATED,
            environment="production",
            ring_name="canary",
            bundle_id="pb_001",
            activation_id="pa_001",
            assignment_id="raa_001",
            actor_id="user-42",
            reason="Initial deployment",
            data={"version": 2},
            created_at=now,
        )
        assert event.event_id == "pce_abc123"
        assert event.event_type == PolicyChangeEventType.BUNDLE_CREATED
        assert event.environment == "production"
        assert event.ring_name == "canary"
        assert event.bundle_id == "pb_001"
        assert event.activation_id == "pa_001"
        assert event.assignment_id == "raa_001"
        assert event.actor_id == "user-42"
        assert event.reason == "Initial deployment"
        assert event.data == {"version": 2}
        assert event.created_at == now

    def test_event_id_prefix(self) -> None:
        """event_id starts with 'pce_'."""
        event = PolicyChangeEvent(
            event_id="pce_test",
            event_type=PolicyChangeEventType.MANUAL_RELOAD_REQUESTED,
            created_at=datetime.now(timezone.utc),
        )
        assert event.event_id.startswith("pce_")

    def test_timezone_aware_datetime(self) -> None:
        """created_at is timezone-aware."""
        now = datetime.now(timezone.utc)
        event = PolicyChangeEvent(
            event_id="pce_tz",
            event_type=PolicyChangeEventType.RING_ASSIGNED,
            created_at=now,
        )
        assert event.created_at.tzinfo is not None

    def test_data_default(self) -> None:
        """data defaults to empty dict."""
        event = PolicyChangeEvent(
            event_id="pce_default",
            event_type=PolicyChangeEventType.GATE_COMPLETED,
            created_at=datetime.now(timezone.utc),
        )
        assert event.data == {}

    def test_optional_fields_default_none(self) -> None:
        """environment, ring_name, bundle_id, activation_id, assignment_id, actor_id, reason default to None."""
        event = PolicyChangeEvent(
            event_id="pce_minimal",
            event_type=PolicyChangeEventType.ENVIRONMENT_ENABLED,
            created_at=datetime.now(timezone.utc),
        )
        assert event.environment is None
        assert event.ring_name is None
        assert event.bundle_id is None
        assert event.activation_id is None
        assert event.assignment_id is None
        assert event.actor_id is None
        assert event.reason is None

    def test_data_default_is_independent(self) -> None:
        """Each instance gets its own default dict (no shared mutable default)."""
        event_a = PolicyChangeEvent(
            event_id="pce_a",
            event_type=PolicyChangeEventType.RING_PROMOTED,
            created_at=datetime.now(timezone.utc),
        )
        event_b = PolicyChangeEvent(
            event_id="pce_b",
            event_type=PolicyChangeEventType.RING_DISABLED,
            created_at=datetime.now(timezone.utc),
        )
        event_a.data["key"] = "value"
        assert "key" not in event_b.data


class TestRolloutEventTypesPhase35:
    """Tests for the six rollout event types added in Phase 35."""

    def test_rollout_event_types_exist(self) -> None:
        """All six rollout event types exist with correct values."""
        assert PolicyChangeEventType.ROLLOUT_CREATED == "policy.rollout.created"
        assert PolicyChangeEventType.ROLLOUT_STARTED == "policy.rollout.started"
        assert PolicyChangeEventType.ROLLOUT_STEP_SUCCEEDED == "policy.rollout.step_succeeded"
        assert PolicyChangeEventType.ROLLOUT_COMPLETED == "policy.rollout.completed"
        assert PolicyChangeEventType.ROLLOUT_FAILED == "policy.rollout.failed"
        assert PolicyChangeEventType.ROLLOUT_CANCELLED == "policy.rollout.cancelled"

    def test_rollout_event_creation(self) -> None:
        """Create a PolicyChangeEvent with ROLLOUT_CREATED type."""
        now = datetime.now(timezone.utc)
        event = PolicyChangeEvent(
            event_id="pce_rollout_001",
            event_type=PolicyChangeEventType.ROLLOUT_CREATED,
            environment="production",
            ring_name="canary",
            bundle_id="pb_042",
            actor_id="user-7",
            reason="Scheduled rollout",
            data={"steps": 5},
            created_at=now,
        )
        assert event.event_type == PolicyChangeEventType.ROLLOUT_CREATED
        assert event.event_type == "policy.rollout.created"
        assert event.environment == "production"
        assert event.ring_name == "canary"
        assert event.bundle_id == "pb_042"
        assert event.data == {"steps": 5}


class TestRolloutApprovalEventTypesPhase36:
    """Tests for the three rollout approval event types added in Phase 36."""

    def test_rollout_approval_event_types_exist(self) -> None:
        """All three rollout approval event types exist as enum members."""
        assert hasattr(PolicyChangeEventType, "ROLLOUT_APPROVAL_REQUESTED")
        assert hasattr(PolicyChangeEventType, "ROLLOUT_APPROVAL_APPROVED")
        assert hasattr(PolicyChangeEventType, "ROLLOUT_APPROVAL_REJECTED")

    def test_rollout_approval_event_types_have_correct_values(self) -> None:
        """Rollout approval event types have the correct string values."""
        assert PolicyChangeEventType.ROLLOUT_APPROVAL_REQUESTED == "policy.rollout.approval.requested"
        assert PolicyChangeEventType.ROLLOUT_APPROVAL_APPROVED == "policy.rollout.approval.approved"
        assert PolicyChangeEventType.ROLLOUT_APPROVAL_REJECTED == "policy.rollout.approval.rejected"
