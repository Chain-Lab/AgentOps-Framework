"""Tests for Phase 53 Task 6 — JSONL structured export."""
from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
    AlertDeliveryAttempt,
)
from agent_app.governance.policy_rollout_federation_notification_observability import (
    NotificationAlertEvent,
    NotificationDeliveryEvent,
    NotificationDeliveryEventType,
)
from agent_app.runtime.policy_rollout_federation_notification_jsonl_export import (
    export_delivery_events_jsonl,
    export_alert_events_jsonl,
    export_delivery_attempts_jsonl,
)


class TestExportDeliveryEventsJsonl:
    def test_empty_returns_empty_string(self):
        assert export_delivery_events_jsonl([]) == ""

    def test_single_event(self):
        e = NotificationDeliveryEvent(
            event_id="nde_1", event_type=NotificationDeliveryEventType.SENT,
            channel="webhook", created_at=datetime.now(timezone.utc),
        )
        result = export_delivery_events_jsonl([e])
        assert "nde_1" in result
        assert '"event_id": "nde_1"' in result or '"event_id":"nde_1"' in result

    def test_multiple_events_multiple_lines(self):
        e1 = NotificationDeliveryEvent(
            event_id="nde_1", event_type=NotificationDeliveryEventType.SENT,
            channel="webhook", created_at=datetime.now(timezone.utc),
        )
        e2 = NotificationDeliveryEvent(
            event_id="nde_2", event_type=NotificationDeliveryEventType.FAILED,
            channel="email", created_at=datetime.now(timezone.utc),
        )
        result = export_delivery_events_jsonl([e1, e2])
        lines = result.strip().split("\n")
        assert len(lines) == 2

    def test_each_line_valid_json(self):
        e = NotificationDeliveryEvent(
            event_id="nde_1", event_type=NotificationDeliveryEventType.SENT,
            channel="webhook", created_at=datetime.now(timezone.utc),
        )
        result = export_delivery_events_jsonl([e])
        for line in result.strip().split("\n"):
            parsed = json.loads(line)
            assert "event_id" in parsed

    def test_sensitive_error_message_redacted(self):
        e = NotificationDeliveryEvent(
            event_id="nde_1", event_type=NotificationDeliveryEventType.FAILED,
            channel="webhook", error_message="auth failed: token=abc123",
            created_at=datetime.now(timezone.utc),
        )
        result = export_delivery_events_jsonl([e])
        assert "abc123" not in result

    def test_sensitive_metadata_redacted(self):
        e = NotificationDeliveryEvent(
            event_id="nde_1", event_type=NotificationDeliveryEventType.SENT,
            channel="webhook",
            metadata={"api_key": "secret", "team": "ops"},
            created_at=datetime.now(timezone.utc),
        )
        result = export_delivery_events_jsonl([e])
        assert "secret" not in result
        assert "ops" in result


class TestExportAlertEventsJsonl:
    def test_empty_returns_empty_string(self):
        assert export_alert_events_jsonl([]) == ""

    def test_single_alert(self):
        alert = NotificationAlertEvent(
            alert_id="nae_1", rule_id="nar_1", name="Test", severity="critical",
            metric="failure_rate", observed_value=0.1, threshold=0.05,
            message="Alert msg", status="open", created_at=datetime.now(timezone.utc),
        )
        result = export_alert_events_jsonl([alert])
        assert "nae_1" in result

    def test_message_redacted(self):
        alert = NotificationAlertEvent(
            alert_id="nae_1", rule_id="nar_1", name="Test", severity="critical",
            metric="failure_rate", observed_value=0.1, threshold=0.05,
            message="Auth failed: password=xyz", status="open",
            created_at=datetime.now(timezone.utc),
        )
        result = export_alert_events_jsonl([alert])
        assert "xyz" not in result


class TestExportDeliveryAttemptsJsonl:
    def test_empty_returns_empty_string(self):
        assert export_delivery_attempts_jsonl([]) == ""

    def test_single_attempt(self):
        a = AlertDeliveryAttempt(
            attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.CONSOLE,
            status=AlertDeliveryStatus.DELIVERED,
            created_at=datetime.now(timezone.utc),
        )
        result = export_delivery_attempts_jsonl([a])
        assert "nda_1" in result

    def test_channel_type_serialized(self):
        a = AlertDeliveryAttempt(
            attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.WEBHOOK,
            status=AlertDeliveryStatus.DELIVERED,
            created_at=datetime.now(timezone.utc),
        )
        result = export_delivery_attempts_jsonl([a])
        assert '"webhook"' in result or '"channel_type":"webhook"' in result

    def test_payload_preview_redacted(self):
        a = AlertDeliveryAttempt(
            attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.WEBHOOK,
            status=AlertDeliveryStatus.DELIVERED,
            payload_preview={"authorization": "Bearer secret", "summary": "ok"},
            created_at=datetime.now(timezone.utc),
        )
        result = export_delivery_attempts_jsonl([a])
        assert "secret" not in result
        assert "ok" in result

    def test_datetime_iso_format(self):
        now = datetime.now(timezone.utc)
        a = AlertDeliveryAttempt(
            attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.CONSOLE,
            status=AlertDeliveryStatus.DELIVERED,
            created_at=now,
        )
        result = export_delivery_attempts_jsonl([a])
        # Pydantic model_dump(mode="json") may use "Z" for UTC
        assert now.isoformat() in result or now.isoformat().replace("+00:00", "Z") in result
