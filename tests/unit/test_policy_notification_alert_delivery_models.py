"""Tests for Phase 53 Task 1 — Alert delivery domain models."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent_app.governance.policy_rollout_federation_notification_alert_delivery import (
    AlertDeliveryChannelType,
    AlertDeliveryStatus,
    AlertDeliveryTarget,
    AlertDeliveryAttempt,
    AlertDeliveryRetryPolicy,
)


class TestAlertDeliveryChannelType:
    def test_channel_type_values(self):
        assert AlertDeliveryChannelType.MEMORY == "memory"
        assert AlertDeliveryChannelType.WEBHOOK == "webhook"
        assert AlertDeliveryChannelType.EMAIL == "email"
        assert AlertDeliveryChannelType.SLACK == "slack"
        assert AlertDeliveryChannelType.CONSOLE == "console"


class TestAlertDeliveryStatus:
    def test_status_values(self):
        assert AlertDeliveryStatus.PENDING == "pending"
        assert AlertDeliveryStatus.DELIVERED == "delivered"
        assert AlertDeliveryStatus.FAILED == "failed"
        assert AlertDeliveryStatus.RETRY_SCHEDULED == "retry_scheduled"
        assert AlertDeliveryStatus.DLQ == "dlq"
        assert AlertDeliveryStatus.SUPPRESSED == "suppressed"


class TestAlertDeliveryTarget:
    def test_target_defaults(self):
        t = AlertDeliveryTarget(
            target_id="ndt_1", name="Ops Console", channel_type=AlertDeliveryChannelType.CONSOLE
        )
        assert t.enabled is True
        assert t.severity_filter == []
        assert t.channel_filter == []
        assert t.federation_filter == []
        assert t.endpoint is None
        assert t.headers == {}
        assert t.metadata == {}

    def test_target_with_all_fields(self):
        t = AlertDeliveryTarget(
            target_id="ndt_1", name="Ops Webhook", channel_type=AlertDeliveryChannelType.WEBHOOK,
            enabled=True, severity_filter=["critical", "warning"],
            channel_filter=["webhook"], federation_filter=["fed_001"],
            endpoint="https://example.invalid/alerts",
            headers={"Content-Type": "application/json"},
            metadata={"team": "ops"},
        )
        assert t.endpoint == "https://example.invalid/alerts"
        assert t.severity_filter == ["critical", "warning"]

    def test_target_id_prefix_validated(self):
        with pytest.raises(ValueError, match="target_id must start with 'ndt_'"):
            AlertDeliveryTarget(target_id="bad_id", name="Ops", channel_type=AlertDeliveryChannelType.CONSOLE)

    def test_sensitive_headers_redacted(self):
        t = AlertDeliveryTarget(
            target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.WEBHOOK,
            headers={"authorization": "Bearer secret_token", "x-api-key": "key123"},
        )
        assert t.headers["authorization"] == "[REDACTED]"
        assert t.headers["x-api-key"] == "[REDACTED]"

    def test_sensitive_metadata_redacted(self):
        t = AlertDeliveryTarget(
            target_id="ndt_1", name="Ops", channel_type=AlertDeliveryChannelType.WEBHOOK,
            metadata={"api_key": "secret", "team": "ops"},
        )
        assert t.metadata["api_key"] == "[REDACTED]"
        assert t.metadata["team"] == "ops"


class TestAlertDeliveryAttempt:
    def test_attempt_defaults(self):
        now = datetime.now(timezone.utc)
        a = AlertDeliveryAttempt(
            attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.CONSOLE,
            status=AlertDeliveryStatus.PENDING, created_at=now,
        )
        assert a.attempt == 1
        assert a.next_retry_at is None
        assert a.error_code is None
        assert a.error_message is None
        assert a.payload_preview == {}
        assert a.delivered_at is None

    def test_attempt_all_fields(self):
        now = datetime.now(timezone.utc)
        a = AlertDeliveryAttempt(
            attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.WEBHOOK,
            status=AlertDeliveryStatus.FAILED, attempt=2,
            next_retry_at=now, error_code="HTTP_500", error_message="Server error",
            payload_preview={"summary": "test"}, created_at=now, delivered_at=now,
        )
        assert a.attempt == 2
        assert a.error_code == "HTTP_500"

    def test_attempt_id_prefix_validated(self):
        now = datetime.now(timezone.utc)
        with pytest.raises(ValueError, match="attempt_id must start with 'nda_'"):
            AlertDeliveryAttempt(
                attempt_id="bad_id", alert_id="nae_1", target_id="ndt_1",
                channel_type=AlertDeliveryChannelType.CONSOLE, status=AlertDeliveryStatus.PENDING,
                created_at=now,
            )

    def test_attempt_sensitive_error_message_redacted(self):
        now = datetime.now(timezone.utc)
        a = AlertDeliveryAttempt(
            attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.WEBHOOK, status=AlertDeliveryStatus.FAILED,
            error_message="auth failed: token=abc123", created_at=now,
        )
        assert "abc123" not in a.error_message
        assert "token" not in a.error_message.lower() or "REDACTED" in a.error_message

    def test_attempt_payload_preview_redacted(self):
        now = datetime.now(timezone.utc)
        a = AlertDeliveryAttempt(
            attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.WEBHOOK, status=AlertDeliveryStatus.FAILED,
            payload_preview={"authorization": "Bearer secret", "summary": "ok"},
            created_at=now,
        )
        assert a.payload_preview.get("authorization") == "[REDACTED]"
        assert a.payload_preview.get("summary") == "ok"

    def test_attempt_tz_aware(self):
        now = datetime.now(timezone.utc)
        a = AlertDeliveryAttempt(
            attempt_id="nda_1", alert_id="nae_1", target_id="ndt_1",
            channel_type=AlertDeliveryChannelType.CONSOLE, status=AlertDeliveryStatus.PENDING,
            created_at=now,
        )
        assert a.created_at.tzinfo is not None


class TestAlertDeliveryRetryPolicy:
    def test_defaults(self):
        p = AlertDeliveryRetryPolicy()
        assert p.max_attempts == 3
        assert p.base_delay_seconds == 60
        assert p.max_delay_seconds == 3600

    def test_custom_values(self):
        p = AlertDeliveryRetryPolicy(max_attempts=5, base_delay_seconds=30, max_delay_seconds=1800)
        assert p.max_attempts == 5
        assert p.base_delay_seconds == 30
        assert p.max_delay_seconds == 1800
