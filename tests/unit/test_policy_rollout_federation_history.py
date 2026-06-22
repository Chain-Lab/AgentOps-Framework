"""Tests for FederationHistoryEventType enum — Phase 52 Task 10 additions."""

from __future__ import annotations

from agent_app.governance.policy_rollout_federation_history import (
    FederationHistoryEventType,
)


class TestPhase52FederationHistoryEventTypes:
    """Tests for the 6 new FederationHistoryEventType members added in Phase 52 Task 10."""

    def test_delivery_event_recorded_exists(self) -> None:
        assert FederationHistoryEventType.NOTIFICATION_DELIVERY_EVENT_RECORDED == \
            "notification.delivery.event_recorded"

    def test_sla_violation_detected_exists(self) -> None:
        assert FederationHistoryEventType.NOTIFICATION_SLA_VIOLATION_DETECTED == \
            "notification.sla.violation_detected"

    def test_alert_created_exists(self) -> None:
        assert FederationHistoryEventType.NOTIFICATION_ALERT_CREATED == \
            "notification.alert.created"

    def test_alert_acknowledged_exists(self) -> None:
        assert FederationHistoryEventType.NOTIFICATION_ALERT_ACKNOWLEDGED == \
            "notification.alert.acknowledged"

    def test_alert_resolved_exists(self) -> None:
        assert FederationHistoryEventType.NOTIFICATION_ALERT_RESOLVED == \
            "notification.alert.resolved"

    def test_observability_report_exported_exists(self) -> None:
        assert FederationHistoryEventType.NOTIFICATION_OBSERVABILITY_REPORT_EXPORTED == \
            "notification.observability.report_exported"

    def test_all_6_new_values_in_enum(self) -> None:
        """All 6 new Phase 52 values are present in the enum."""
        values = {e.value for e in FederationHistoryEventType}
        assert "notification.delivery.event_recorded" in values
        assert "notification.sla.violation_detected" in values
        assert "notification.alert.created" in values
        assert "notification.alert.acknowledged" in values
        assert "notification.alert.resolved" in values
        assert "notification.observability.report_exported" in values

    def test_enum_member_count_51(self) -> None:
        """Exactly 51 enum members defined (42 previous + 9 Phase 53)."""
        assert len(FederationHistoryEventType) == 51

    def test_enum_is_str_subclass(self) -> None:
        """Enum values behave as strings."""
        val = FederationHistoryEventType.NOTIFICATION_DELIVERY_EVENT_RECORDED
        assert isinstance(val, str)
        assert val == "notification.delivery.event_recorded"
