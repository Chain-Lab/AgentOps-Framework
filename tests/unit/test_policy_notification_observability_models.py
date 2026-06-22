"""Tests for policy_rollout_federation_notification_observability models."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

from agent_app.governance.policy_rollout_federation_notification_observability import (
    ChannelHealthSnapshot,
    ChannelHealthStatus,
    NotificationAlertEvent,
    NotificationAlertRule,
    NotificationChannelSlaOverride,
    NotificationDeliveryEvent,
    NotificationDeliveryEventType,
    NotificationMetricWindow,
    NotificationSlaPolicy,
    NotificationSlaViolation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Return a timezone-aware UTC datetime for use in required fields."""
    return datetime.now(timezone.utc)


# ===========================================================================
# NotificationDeliveryEventType
# ===========================================================================


class TestNotificationDeliveryEventType:
    """Tests for the NotificationDeliveryEventType enum."""

    def test_all_12_event_types_exist(self) -> None:
        expected = [
            "created",
            "queued",
            "rendered",
            "suppressed",
            "send_attempted",
            "sent",
            "failed",
            "retry_scheduled",
            "dlq_created",
            "dlq_replayed",
            "webhook_signature_failed",
            "template_failed",
        ]
        assert len(NotificationDeliveryEventType) == 12
        for value in expected:
            assert value in [e.value for e in NotificationDeliveryEventType]

    def test_specific_enum_values(self) -> None:
        assert NotificationDeliveryEventType.CREATED.value == "created"
        assert NotificationDeliveryEventType.QUEUED.value == "queued"
        assert NotificationDeliveryEventType.SENT.value == "sent"
        assert NotificationDeliveryEventType.FAILED.value == "failed"
        assert NotificationDeliveryEventType.DLQ_CREATED.value == "dlq_created"
        assert NotificationDeliveryEventType.WEBHOOK_SIGNATURE_FAILED.value == "webhook_signature_failed"

    def test_is_str_enum(self) -> None:
        assert isinstance(NotificationDeliveryEventType.CREATED, str)
        assert NotificationDeliveryEventType.CREATED == "created"


# ===========================================================================
# ChannelHealthStatus
# ===========================================================================


class TestChannelHealthStatus:
    """Tests for the ChannelHealthStatus enum."""

    def test_all_4_statuses_exist(self) -> None:
        expected = ["healthy", "degraded", "unhealthy", "unknown"]
        assert len(ChannelHealthStatus) == 4
        for value in expected:
            assert value in [e.value for e in ChannelHealthStatus]

    def test_specific_enum_values(self) -> None:
        assert ChannelHealthStatus.HEALTHY.value == "healthy"
        assert ChannelHealthStatus.DEGRADED.value == "degraded"
        assert ChannelHealthStatus.UNHEALTHY.value == "unhealthy"
        assert ChannelHealthStatus.UNKNOWN.value == "unknown"

    def test_is_str_enum(self) -> None:
        assert isinstance(ChannelHealthStatus.HEALTHY, str)
        assert ChannelHealthStatus.HEALTHY == "healthy"


# ===========================================================================
# NotificationDeliveryEvent
# ===========================================================================


class TestNotificationDeliveryEvent:
    """Tests for the NotificationDeliveryEvent model."""

    def test_event_id_with_correct_prefix(self) -> None:
        evt = NotificationDeliveryEvent(
            event_id="nde_abc123",
            event_type=NotificationDeliveryEventType.CREATED,
            created_at=_now(),
        )
        assert evt.event_id == "nde_abc123"

    def test_event_id_with_wrong_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError, match="nde_"):
            NotificationDeliveryEvent(
                event_id="fn_001",
                event_type=NotificationDeliveryEventType.CREATED,
                created_at=_now(),
            )

    def test_notification_id_with_correct_prefix(self) -> None:
        evt = NotificationDeliveryEvent(
            event_id="nde_001",
            notification_id="fn_abc123",
            event_type=NotificationDeliveryEventType.CREATED,
            created_at=_now(),
        )
        assert evt.notification_id == "fn_abc123"

    def test_notification_id_with_wrong_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError, match="fn_"):
            NotificationDeliveryEvent(
                event_id="nde_001",
                notification_id="bad_id",
                event_type=NotificationDeliveryEventType.CREATED,
                created_at=_now(),
            )

    def test_notification_id_none_is_accepted(self) -> None:
        evt = NotificationDeliveryEvent(
            event_id="nde_001",
            notification_id=None,
            event_type=NotificationDeliveryEventType.CREATED,
            created_at=_now(),
        )
        assert evt.notification_id is None

    def test_created_at_must_be_timezone_aware(self) -> None:
        naive_dt = datetime(2026, 1, 1, 12, 0, 0)
        with pytest.raises(ValidationError, match="timezone-aware"):
            NotificationDeliveryEvent(
                event_id="nde_001",
                event_type=NotificationDeliveryEventType.CREATED,
                created_at=naive_dt,
            )

    def test_created_at_tz_aware_accepted(self) -> None:
        tz_aware_dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        evt = NotificationDeliveryEvent(
            event_id="nde_001",
            event_type=NotificationDeliveryEventType.CREATED,
            created_at=tz_aware_dt,
        )
        assert evt.created_at.tzinfo is not None

    def test_sanitizes_sensitive_metadata(self) -> None:
        evt = NotificationDeliveryEvent(
            event_id="nde_001",
            event_type=NotificationDeliveryEventType.CREATED,
            metadata={
                "source": "unit-test",
                "api_key": "secret-key-123",
                "authorization": "Bearer token-abc",
            },
            created_at=_now(),
        )
        assert evt.metadata["source"] == "unit-test"
        assert evt.metadata["api_key"] == "[REDACTED]"
        assert evt.metadata["authorization"] == "[REDACTED]"

    def test_sanitizes_sensitive_error_message(self) -> None:
        error_msg = "Failed with api_key=secret123 and token=xyz"
        evt = NotificationDeliveryEvent(
            event_id="nde_001",
            event_type=NotificationDeliveryEventType.FAILED,
            error_message=error_msg,
            created_at=_now(),
        )
        assert "secret123" not in evt.error_message
        assert "xyz" not in evt.error_message
        assert "[REDACTED]" in evt.error_message

    def test_defaults(self) -> None:
        evt = NotificationDeliveryEvent(
            event_id="nde_001",
            event_type=NotificationDeliveryEventType.CREATED,
            created_at=_now(),
        )
        assert evt.notification_id is None
        assert evt.approval_id is None
        assert evt.federation_id is None
        assert evt.channel is None
        assert evt.status is None
        assert evt.attempt is None
        assert evt.latency_ms is None
        assert evt.error_code is None
        assert evt.error_message is None
        assert evt.adapter_name is None
        assert evt.template_id is None
        assert evt.preference_decision is None
        assert evt.metadata == {}


# ===========================================================================
# NotificationMetricWindow
# ===========================================================================


class TestNotificationMetricWindow:
    """Tests for the NotificationMetricWindow model."""

    def test_defaults_are_correct(self) -> None:
        window = NotificationMetricWindow(
            window_start=_now(),
            window_end=_now() + timedelta(hours=1),
        )
        assert window.total == 0
        assert window.sent == 0
        assert window.failed == 0
        assert window.suppressed == 0
        assert window.dlq == 0
        assert window.retry_scheduled == 0
        assert window.success_rate == 0.0
        assert window.failure_rate == 0.0
        assert window.dlq_rate == 0.0
        assert window.avg_latency_ms is None
        assert window.p95_latency_ms is None
        assert window.federation_id is None
        assert window.channel is None

    def test_window_start_tz_aware(self) -> None:
        window = NotificationMetricWindow(
            window_start=_now(),
            window_end=_now() + timedelta(hours=1),
        )
        assert window.window_start.tzinfo is not None

    def test_window_start_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            NotificationMetricWindow(
                window_start=datetime(2026, 1, 1, 12, 0, 0),
                window_end=datetime(2026, 1, 2, 12, 0, 0),
            )

    def test_window_end_tz_aware(self) -> None:
        window = NotificationMetricWindow(
            window_start=_now(),
            window_end=_now() + timedelta(hours=1),
        )
        assert window.window_end.tzinfo is not None

    def test_window_end_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            NotificationMetricWindow(
                window_start=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                window_end=datetime(2026, 1, 2, 12, 0, 0),
            )

    def test_with_values(self) -> None:
        window = NotificationMetricWindow(
            window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            window_end=datetime(2026, 1, 2, tzinfo=timezone.utc),
            federation_id="fed_001",
            channel="email",
            total=100,
            sent=95,
            failed=3,
            suppressed=2,
            dlq=0,
            retry_scheduled=0,
            success_rate=0.95,
            failure_rate=0.03,
            dlq_rate=0.0,
            avg_latency_ms=250.5,
            p95_latency_ms=450.0,
        )
        assert window.total == 100
        assert window.sent == 95
        assert window.channel == "email"
        assert window.federation_id == "fed_001"


# ===========================================================================
# ChannelHealthSnapshot
# ===========================================================================


class TestChannelHealthSnapshot:
    """Tests for the ChannelHealthSnapshot model."""

    def test_defaults_are_correct(self) -> None:
        snapshot = ChannelHealthSnapshot(
            channel="email",
            status=ChannelHealthStatus.HEALTHY,
            window_start=_now(),
            window_end=_now() + timedelta(hours=1),
            created_at=_now(),
        )
        assert snapshot.total == 0
        assert snapshot.success_rate == 0.0
        assert snapshot.failure_rate == 0.0
        assert snapshot.dlq_rate == 0.0
        assert snapshot.avg_latency_ms is None
        assert snapshot.reason is None

    def test_created_at_tz_aware(self) -> None:
        snapshot = ChannelHealthSnapshot(
            channel="email",
            status=ChannelHealthStatus.HEALTHY,
            window_start=_now(),
            window_end=_now() + timedelta(hours=1),
            created_at=_now(),
        )
        assert snapshot.created_at.tzinfo is not None

    def test_created_at_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            ChannelHealthSnapshot(
                channel="email",
                status=ChannelHealthStatus.HEALTHY,
                window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                window_end=datetime(2026, 1, 2, tzinfo=timezone.utc),
                created_at=datetime(2026, 1, 1, 12, 0, 0),
            )

    def test_window_start_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            ChannelHealthSnapshot(
                channel="email",
                status=ChannelHealthStatus.HEALTHY,
                window_start=datetime(2026, 1, 1, 12, 0, 0),
                window_end=datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc),
                created_at=_now(),
            )

    def test_window_end_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            ChannelHealthSnapshot(
                channel="email",
                status=ChannelHealthStatus.HEALTHY,
                window_start=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                window_end=datetime(2026, 1, 2, 12, 0, 0),
                created_at=_now(),
            )

    def test_with_values(self) -> None:
        snapshot = ChannelHealthSnapshot(
            channel="slack",
            status=ChannelHealthStatus.DEGRADED,
            window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            window_end=datetime(2026, 1, 2, tzinfo=timezone.utc),
            created_at=_now(),
            total=500,
            success_rate=0.82,
            failure_rate=0.15,
            dlq_rate=0.03,
            avg_latency_ms=1200.0,
            reason="Elevated failure rate detected",
        )
        assert snapshot.channel == "slack"
        assert snapshot.status == ChannelHealthStatus.DEGRADED
        assert snapshot.total == 500
        assert snapshot.reason == "Elevated failure rate detected"


# ===========================================================================
# NotificationSlaPolicy
# ===========================================================================


class TestNotificationSlaPolicy:
    """Tests for the NotificationSlaPolicy model."""

    def test_defaults(self) -> None:
        policy = NotificationSlaPolicy()
        assert policy.enabled is True
        assert policy.max_delivery_latency_ms == 30000
        assert policy.min_success_rate == 0.95
        assert policy.max_failure_rate == 0.05
        assert policy.max_dlq_rate == 0.01
        assert policy.window_minutes == 60
        assert policy.channels == {}

    def test_with_values(self) -> None:
        policy = NotificationSlaPolicy(
            enabled=False,
            max_delivery_latency_ms=5000,
            min_success_rate=0.99,
            max_failure_rate=0.01,
            max_dlq_rate=0.005,
            window_minutes=30,
            channels={
                "email": NotificationChannelSlaOverride(
                    max_delivery_latency_ms=3000,
                    min_success_rate=0.98,
                ),
                "slack": NotificationChannelSlaOverride(
                    max_delivery_latency_ms=10000,
                ),
            },
        )
        assert policy.enabled is False
        assert policy.max_delivery_latency_ms == 5000
        assert policy.channels["email"].max_delivery_latency_ms == 3000
        assert policy.channels["email"].min_success_rate == 0.98
        assert policy.channels["slack"].max_delivery_latency_ms == 10000
        assert policy.channels["slack"].min_success_rate is None


# ===========================================================================
# NotificationChannelSlaOverride
# ===========================================================================


class TestNotificationChannelSlaOverride:
    """Tests for the NotificationChannelSlaOverride model."""

    def test_defaults(self) -> None:
        override = NotificationChannelSlaOverride()
        assert override.max_delivery_latency_ms is None
        assert override.min_success_rate is None
        assert override.max_failure_rate is None
        assert override.max_dlq_rate is None
        assert override.window_minutes is None

    def test_with_values(self) -> None:
        override = NotificationChannelSlaOverride(
            max_delivery_latency_ms=5000,
            min_success_rate=0.98,
            max_failure_rate=0.02,
            max_dlq_rate=0.005,
            window_minutes=30,
        )
        assert override.max_delivery_latency_ms == 5000
        assert override.min_success_rate == 0.98
        assert override.max_failure_rate == 0.02
        assert override.max_dlq_rate == 0.005
        assert override.window_minutes == 30


# ===========================================================================
# NotificationSlaViolation
# ===========================================================================


class TestNotificationSlaViolation:
    """Tests for the NotificationSlaViolation model."""

    def test_violation_id_with_correct_prefix(self) -> None:
        v = NotificationSlaViolation(
            violation_id="nsv_001",
            metric="success_rate",
            observed_value=0.90,
            threshold=0.95,
            severity="warning",
            window_start=_now(),
            window_end=_now() + timedelta(hours=1),
            message="Success rate dropped below threshold",
            created_at=_now(),
        )
        assert v.violation_id == "nsv_001"

    def test_violation_id_with_wrong_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError, match="nsv_"):
            NotificationSlaViolation(
                violation_id="bad_id",
                metric="success_rate",
                observed_value=0.90,
                threshold=0.95,
                severity="warning",
                window_start=_now(),
                window_end=_now() + timedelta(hours=1),
                message="Test violation",
                created_at=_now(),
            )

    def test_severity_warning_accepted(self) -> None:
        v = NotificationSlaViolation(
            violation_id="nsv_001",
            metric="failure_rate",
            observed_value=0.10,
            threshold=0.05,
            severity="warning",
            window_start=_now(),
            window_end=_now() + timedelta(hours=1),
            message="Failure rate exceeded threshold",
            created_at=_now(),
        )
        assert v.severity == "warning"

    def test_severity_critical_accepted(self) -> None:
        v = NotificationSlaViolation(
            violation_id="nsv_002",
            metric="dlq_rate",
            observed_value=0.05,
            threshold=0.01,
            severity="critical",
            window_start=_now(),
            window_end=_now() + timedelta(hours=1),
            message="DLQ rate critically high",
            created_at=_now(),
        )
        assert v.severity == "critical"

    def test_severity_invalid_rejected(self) -> None:
        with pytest.raises(ValidationError, match="warning.*critical"):
            NotificationSlaViolation(
                violation_id="nsv_003",
                metric="latency",
                observed_value=50000,
                threshold=30000,
                severity="info",
                window_start=_now(),
                window_end=_now() + timedelta(hours=1),
                message="Invalid severity",
                created_at=_now(),
            )

    def test_window_start_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            NotificationSlaViolation(
                violation_id="nsv_001",
                metric="success_rate",
                observed_value=0.90,
                threshold=0.95,
                severity="warning",
                window_start=datetime(2026, 1, 1, 12, 0, 0),
                window_end=datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc),
                message="Test",
                created_at=_now(),
            )

    def test_window_end_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            NotificationSlaViolation(
                violation_id="nsv_001",
                metric="success_rate",
                observed_value=0.90,
                threshold=0.95,
                severity="warning",
                window_start=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                window_end=datetime(2026, 1, 2, 12, 0, 0),
                message="Test",
                created_at=_now(),
            )

    def test_created_at_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            NotificationSlaViolation(
                violation_id="nsv_001",
                metric="success_rate",
                observed_value=0.90,
                threshold=0.95,
                severity="warning",
                window_start=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                window_end=datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc),
                message="Test",
                created_at=datetime(2026, 1, 1, 12, 0, 0),
            )

    def test_all_tz_aware_accepted(self) -> None:
        v = NotificationSlaViolation(
            violation_id="nsv_001",
            metric="success_rate",
            observed_value=0.90,
            threshold=0.95,
            severity="critical",
            window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            window_end=datetime(2026, 1, 2, tzinfo=timezone.utc),
            message="All fields tz-aware",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        assert v.window_start.tzinfo is not None
        assert v.window_end.tzinfo is not None
        assert v.created_at.tzinfo is not None

    def test_optional_fields_default_none(self) -> None:
        v = NotificationSlaViolation(
            violation_id="nsv_001",
            metric="success_rate",
            observed_value=0.90,
            threshold=0.95,
            severity="warning",
            window_start=_now(),
            window_end=_now() + timedelta(hours=1),
            message="Test",
            created_at=_now(),
        )
        assert v.federation_id is None
        assert v.channel is None


# ===========================================================================
# NotificationAlertRule
# ===========================================================================


class TestNotificationAlertRule:
    """Tests for the NotificationAlertRule model."""

    def test_rule_id_with_correct_prefix(self) -> None:
        rule = NotificationAlertRule(
            rule_id="nar_001",
            name="High failure rate",
            metric="failure_rate",
            operator=">",
            threshold=0.10,
        )
        assert rule.rule_id == "nar_001"

    def test_rule_id_with_wrong_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError, match="nar_"):
            NotificationAlertRule(
                rule_id="bad_id",
                name="Test rule",
                metric="failure_rate",
                operator=">",
                threshold=0.10,
            )

    def test_operator_gt(self) -> None:
        rule = NotificationAlertRule(
            rule_id="nar_001",
            name="Test",
            metric="failure_rate",
            operator=">",
            threshold=0.10,
        )
        assert rule.operator == ">"

    def test_operator_gte(self) -> None:
        rule = NotificationAlertRule(
            rule_id="nar_001",
            name="Test",
            metric="failure_rate",
            operator=">=",
            threshold=0.10,
        )
        assert rule.operator == ">="

    def test_operator_lt(self) -> None:
        rule = NotificationAlertRule(
            rule_id="nar_001",
            name="Test",
            metric="success_rate",
            operator="<",
            threshold=0.90,
        )
        assert rule.operator == "<"

    def test_operator_lte(self) -> None:
        rule = NotificationAlertRule(
            rule_id="nar_001",
            name="Test",
            metric="success_rate",
            operator="<=",
            threshold=0.90,
        )
        assert rule.operator == "<="

    def test_operator_eq(self) -> None:
        rule = NotificationAlertRule(
            rule_id="nar_001",
            name="Test",
            metric="dlq_rate",
            operator="==",
            threshold=0.0,
        )
        assert rule.operator == "=="

    def test_operator_invalid_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NotificationAlertRule(
                rule_id="nar_001",
                name="Test",
                metric="failure_rate",
                operator="!=",
                threshold=0.10,
            )

    def test_severity_info(self) -> None:
        rule = NotificationAlertRule(
            rule_id="nar_001",
            name="Test",
            metric="latency",
            operator=">",
            threshold=5000,
            severity="info",
        )
        assert rule.severity == "info"

    def test_severity_warning_default(self) -> None:
        rule = NotificationAlertRule(
            rule_id="nar_001",
            name="Test",
            metric="failure_rate",
            operator=">",
            threshold=0.10,
        )
        assert rule.severity == "warning"

    def test_severity_critical(self) -> None:
        rule = NotificationAlertRule(
            rule_id="nar_001",
            name="Test",
            metric="dlq_rate",
            operator=">",
            threshold=0.01,
            severity="critical",
        )
        assert rule.severity == "critical"

    def test_severity_invalid_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NotificationAlertRule(
                rule_id="nar_001",
                name="Test",
                metric="failure_rate",
                operator=">",
                threshold=0.10,
                severity="debug",
            )

    def test_defaults(self) -> None:
        rule = NotificationAlertRule(
            rule_id="nar_001",
            name="Test rule",
            metric="failure_rate",
            operator=">",
            threshold=0.10,
        )
        assert rule.enabled is True
        assert rule.severity == "warning"
        assert rule.channel is None
        assert rule.federation_id is None
        assert rule.window_minutes == 60
        assert rule.cooldown_minutes == 30

    def test_with_values(self) -> None:
        rule = NotificationAlertRule(
            rule_id="nar_001",
            name="High failure rate",
            enabled=True,
            metric="failure_rate",
            operator=">=",
            threshold=0.10,
            severity="critical",
            channel="email",
            federation_id="fed_001",
            window_minutes=30,
            cooldown_minutes=15,
        )
        assert rule.name == "High failure rate"
        assert rule.enabled is True
        assert rule.channel == "email"
        assert rule.federation_id == "fed_001"
        assert rule.window_minutes == 30
        assert rule.cooldown_minutes == 15


# ===========================================================================
# NotificationAlertEvent
# ===========================================================================


class TestNotificationAlertEvent:
    """Tests for the NotificationAlertEvent model."""

    def test_alert_id_with_correct_prefix(self) -> None:
        evt = NotificationAlertEvent(
            alert_id="nae_001",
            rule_id="nar_001",
            name="High failure rate",
            severity="warning",
            metric="failure_rate",
            observed_value=0.15,
            threshold=0.10,
            message="Failure rate exceeded",
            created_at=_now(),
        )
        assert evt.alert_id == "nae_001"

    def test_alert_id_with_wrong_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError, match="nae_"):
            NotificationAlertEvent(
                alert_id="bad_id",
                rule_id="nar_001",
                name="Test alert",
                severity="warning",
                metric="failure_rate",
                observed_value=0.15,
                threshold=0.10,
                message="Test",
                created_at=_now(),
            )

    def test_status_open_default(self) -> None:
        evt = NotificationAlertEvent(
            alert_id="nae_001",
            rule_id="nar_001",
            name="Test alert",
            severity="warning",
            metric="failure_rate",
            observed_value=0.15,
            threshold=0.10,
            message="Test",
            created_at=_now(),
        )
        assert evt.status == "open"

    def test_status_acknowledged(self) -> None:
        now = _now()
        evt = NotificationAlertEvent(
            alert_id="nae_001",
            rule_id="nar_001",
            name="Test alert",
            severity="warning",
            metric="failure_rate",
            observed_value=0.15,
            threshold=0.10,
            message="Test",
            status="acknowledged",
            created_at=now,
            acknowledged_at=now,
            acknowledged_by="admin-1",
        )
        assert evt.status == "acknowledged"
        assert evt.acknowledged_at == now
        assert evt.acknowledged_by == "admin-1"

    def test_status_resolved(self) -> None:
        now = _now()
        evt = NotificationAlertEvent(
            alert_id="nae_001",
            rule_id="nar_001",
            name="Test alert",
            severity="critical",
            metric="dlq_rate",
            observed_value=0.05,
            threshold=0.01,
            message="DLQ rate critical",
            status="resolved",
            created_at=now,
            resolved_at=now,
            resolved_by="admin-1",
        )
        assert evt.status == "resolved"
        assert evt.resolved_at == now
        assert evt.resolved_by == "admin-1"

    def test_status_transition_open_to_acknowledged(self) -> None:
        """Simulate an alert being acknowledged after being open."""
        now = _now()
        evt = NotificationAlertEvent(
            alert_id="nae_001",
            rule_id="nar_001",
            name="Test alert",
            severity="warning",
            metric="failure_rate",
            observed_value=0.15,
            threshold=0.10,
            message="Test",
            status="open",
            created_at=now,
        )
        assert evt.status == "open"

        # Simulate acknowledgement
        evt.status = "acknowledged"
        evt.acknowledged_at = now + timedelta(minutes=5)
        evt.acknowledged_by = "admin-1"

        assert evt.status == "acknowledged"
        assert evt.acknowledged_by == "admin-1"

    def test_status_transition_acknowledged_to_resolved(self) -> None:
        """Simulate an alert being resolved after acknowledgement."""
        now = _now()
        evt = NotificationAlertEvent(
            alert_id="nae_001",
            rule_id="nar_001",
            name="Test alert",
            severity="critical",
            metric="dlq_rate",
            observed_value=0.05,
            threshold=0.01,
            message="DLQ rate critical",
            status="acknowledged",
            created_at=now,
            acknowledged_at=now + timedelta(minutes=5),
            acknowledged_by="admin-1",
        )
        assert evt.status == "acknowledged"

        # Simulate resolution
        evt.status = "resolved"
        evt.resolved_at = now + timedelta(minutes=15)
        evt.resolved_by = "admin-1"

        assert evt.status == "resolved"
        assert evt.resolved_by == "admin-1"

    def test_status_invalid_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NotificationAlertEvent(
                alert_id="nae_001",
                rule_id="nar_001",
                name="Test alert",
                severity="warning",
                metric="failure_rate",
                observed_value=0.15,
                threshold=0.10,
                message="Test",
                status="closed",
                created_at=_now(),
            )

    def test_created_at_tz_aware(self) -> None:
        evt = NotificationAlertEvent(
            alert_id="nae_001",
            rule_id="nar_001",
            name="Test alert",
            severity="warning",
            metric="failure_rate",
            observed_value=0.15,
            threshold=0.10,
            message="Test",
            created_at=_now(),
        )
        assert evt.created_at.tzinfo is not None

    def test_created_at_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            NotificationAlertEvent(
                alert_id="nae_001",
                rule_id="nar_001",
                name="Test alert",
                severity="warning",
                metric="failure_rate",
                observed_value=0.15,
                threshold=0.10,
                message="Test",
                created_at=datetime(2026, 1, 1, 12, 0, 0),
            )

    def test_acknowledged_at_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            NotificationAlertEvent(
                alert_id="nae_001",
                rule_id="nar_001",
                name="Test alert",
                severity="warning",
                metric="failure_rate",
                observed_value=0.15,
                threshold=0.10,
                message="Test",
                created_at=_now(),
                acknowledged_at=datetime(2026, 1, 1, 12, 0, 0),
            )

    def test_acknowledged_at_tz_aware_accepted(self) -> None:
        evt = NotificationAlertEvent(
            alert_id="nae_001",
            rule_id="nar_001",
            name="Test alert",
            severity="warning",
            metric="failure_rate",
            observed_value=0.15,
            threshold=0.10,
            message="Test",
            created_at=_now(),
            acknowledged_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert evt.acknowledged_at.tzinfo is not None

    def test_resolved_at_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            NotificationAlertEvent(
                alert_id="nae_001",
                rule_id="nar_001",
                name="Test alert",
                severity="warning",
                metric="failure_rate",
                observed_value=0.15,
                threshold=0.10,
                message="Test",
                created_at=_now(),
                resolved_at=datetime(2026, 1, 1, 12, 0, 0),
            )

    def test_resolved_at_tz_aware_accepted(self) -> None:
        evt = NotificationAlertEvent(
            alert_id="nae_001",
            rule_id="nar_001",
            name="Test alert",
            severity="warning",
            metric="failure_rate",
            observed_value=0.15,
            threshold=0.10,
            message="Test",
            created_at=_now(),
            resolved_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        )
        assert evt.resolved_at.tzinfo is not None

    def test_defaults(self) -> None:
        evt = NotificationAlertEvent(
            alert_id="nae_001",
            rule_id="nar_001",
            name="Test alert",
            severity="warning",
            metric="failure_rate",
            observed_value=0.15,
            threshold=0.10,
            message="Test",
            created_at=_now(),
        )
        assert evt.status == "open"
        assert evt.federation_id is None
        assert evt.channel is None
        assert evt.acknowledged_at is None
        assert evt.acknowledged_by is None
        assert evt.resolved_at is None
        assert evt.resolved_by is None
