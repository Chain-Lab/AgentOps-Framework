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
            "policy.activation.created",
            "policy.activation.rolled_back",
            "policy.bundle.created",
            "policy.environment.disabled",
            "policy.environment.enabled",
            "policy.expiration.permission_denied",
            "policy.expiration.sweep_completed",
            "policy.expiration.sweep_failed",
            "policy.expiration.sweep_started",
            "policy.expiration.target_expired",
            "policy.federation.analytics.export_failed",
            "policy.federation.analytics.export_generated",
            "policy.federation.analytics.generated",
            "policy.federation.analytics.permission_denied",
            "policy.federation.approval.approved",
            "policy.federation.approval.cancelled",
            "policy.federation.approval.created",
            "policy.federation.approval.escalated",
            "policy.federation.approval.escalation_due",
            "policy.federation.approval.escalation_lock_skipped",
            "policy.federation.approval.escalation_worker_ticked",
            "policy.federation.approval.permission_denied",
            "policy.federation.approval.rejected",
            "policy.federation.conflict.detected",
            "policy.federation.history.recorded",
            "policy.federation.history.viewed",
            "policy.federation.notification.alert.acknowledged",
            "policy.federation.notification.alert.created",
            "policy.federation.notification.alert.dedup_processed",
            "policy.federation.notification.alert.resolved",
            "policy.federation.notification.alert_delivery.attempt_recorded",
            "policy.federation.notification.alert_delivery.dlq_created",
            "policy.federation.notification.alert_delivery.dlq_replayed",
            "policy.federation.notification.alert_delivery.priority_listed",
            "policy.federation.notification.alert_delivery.priority_updated",
            "policy.federation.notification.alert_delivery.retry_ran",
            "policy.federation.notification.alert_delivery.target_created",
            "policy.federation.notification.alert_delivery.target_disabled",
            "policy.federation.notification.alert_delivery.target_updated",
            "policy.federation.notification.alert_delivery.webhook_signed",
            "policy.federation.notification.alert_delivery.write_action_performed",
            "policy.federation.notification.archive_cleanup.completed",
            "policy.federation.notification.archive_cleanup.failed",
            "policy.federation.notification.archive_cleanup.started",
            "policy.federation.notification.created",
            "policy.federation.notification.dlq_created",
            "policy.federation.notification.dlq_purged",
            "policy.federation.notification.dlq_retried",
            "policy.federation.notification.failed",
            "policy.federation.notification.jsonl.exported",
            "policy.federation.notification.observability.event_recorded",
            "policy.federation.notification.preference_deleted",
            "policy.federation.notification.preference_set",
            "policy.federation.notification.prometheus.exported",
            "policy.federation.notification.report.exported",
            "policy.federation.notification.retention.archives_cleaned",
            "policy.federation.notification.retention.cleanup_ran",
            "policy.federation.notification.retry_daemon.run_completed",
            "policy.federation.notification.retry_daemon.run_failed",
            "policy.federation.notification.retry_daemon.started",
            "policy.federation.notification.retry_daemon.stopped",
            "policy.federation.notification.rollup.built",
            "policy.federation.notification.rollup.checkpoint_recorded",
            "policy.federation.notification.rollup.incremental_built",
            "policy.federation.notification.sent",
            "policy.federation.notification.sla.violation_detected",
            "policy.federation.notification.suppressed",
            "policy.federation.notification.template_created",
            "policy.federation.notification.template_disabled",
            "policy.federation.notification.template_failed",
            "policy.federation.notification.template_updated",
            "policy.federation.plan.cancelled",
            "policy.federation.plan.completed",
            "policy.federation.plan.created",
            "policy.federation.plan.failed",
            "policy.federation.plan.started",
            "policy.federation.target.created",
            "policy.federation.target.disabled",
            "policy.federation.target.enabled",
            "policy.federation.timeline.generated",
            "policy.federation.webhook.replay_failed",
            "policy.federation.webhook.replay_requested",
            "policy.federation.webhook.replay_succeeded",
            "policy.federation.webhook.signature_failed",
            "policy.federation.webhook.signature_verified",
            "policy.federation.worker.started",
            "policy.federation.worker.stopped",
            "policy.federation.worker.tick_failed",
            "policy.gate.completed",
            "policy.notification.created",
            "policy.notification.failed",
            "policy.notification.rule.disabled",
            "policy.notification.rule.enabled",
            "policy.notification.sent",
            "policy.observability.export_failed",
            "policy.observability.export_generated",
            "policy.observability.report_generated",
            "policy.promotion.executed",
            "policy.promotion.gate.attached",
            "policy.promotion.gate.execution_blocked",
            "policy.promotion.gate.expired",
            "policy.promotion.gate.failed",
            "policy.promotion.gate.permission_denied",
            "policy.promotion.gate.required",
            "policy.promotion.gate.run",
            "policy.promotion.gate.satisfied",
            "policy.reload.requested",
            "policy.ring.assigned",
            "policy.ring.disabled",
            "policy.ring.enabled",
            "policy.ring.promoted",
            "policy.rollout.analytics.export_failed",
            "policy.rollout.analytics.export_generated",
            "policy.rollout.analytics.generated",
            "policy.rollout.analytics.permission_denied",
            "policy.rollout.approval.approved",
            "policy.rollout.approval.decision_recorded",
            "policy.rollout.approval.expired",
            "policy.rollout.approval.policy_denied",
            "policy.rollout.approval.quorum_reached",
            "policy.rollout.approval.rejected",
            "policy.rollout.approval.requested",
            "policy.rollout.cancelled",
            "policy.rollout.completed",
            "policy.rollout.created",
            "policy.rollout.failed",
            "policy.rollout.gate.attached",
            "policy.rollout.gate.blocked",
            "policy.rollout.gate.failed",
            "policy.rollout.gate.permission_denied",
            "policy.rollout.gate.run",
            "policy.rollout.gate.satisfied",
            "policy.rollout.gate.skipped",
            "policy.rollout.history.recorded",
            "policy.rollout.history.viewed",
            "policy.rollout.started",
            "policy.rollout.step_succeeded",
            "policy.rollout.timeline.generated",
            "policy.runtime.evaluated",
            "policy.runtime.rule.created",
            "policy.runtime.rule.disabled",
            "policy.runtime.rule.enabled",
            "policy.simulation.export_generated",
            "policy.simulation.gate_failed",
            "policy.simulation.gate_passed",
            "policy.simulation.gate_permission_denied",
            "policy.simulation.gate_run",
            "policy.simulation.permission_denied",
            "policy.simulation.replay_run",
            "policy.simulation.validation_run",
        }
        actual = {member.value for member in PolicyChangeEventType}
        assert actual == expected

    def test_enum_member_count(self) -> None:
        """Exactly 150 enum members defined (133 previous + 17 added across phases)."""
        assert len(PolicyChangeEventType) == 150

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
