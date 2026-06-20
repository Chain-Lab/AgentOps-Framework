"""Tests for policy_rollout_federation_notification models."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationChannel,
    FederationNotificationStatus,
    FederationNotificationEventType,
    FederationNotificationMessage,
    FederationNotificationDelivery,
    FederationNotificationPolicy,
    FederationNotificationTarget,
    FederationNotificationDispatchResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """Return a timezone-aware UTC datetime for use in required fields."""
    return datetime.now(timezone.utc)


# ===========================================================================
# FederationNotificationChannel
# ===========================================================================

class TestFederationNotificationChannel:
    """Tests for the FederationNotificationChannel enum."""

    def test_all_5_channels_exist(self) -> None:
        expected = ["email", "slack", "webhook", "console", "noop"]
        assert len(FederationNotificationChannel) == 5
        for value in expected:
            assert value in [e.value for e in FederationNotificationChannel]

    def test_specific_enum_values(self) -> None:
        assert FederationNotificationChannel.EMAIL.value == "email"
        assert FederationNotificationChannel.SLACK.value == "slack"
        assert FederationNotificationChannel.WEBHOOK.value == "webhook"
        assert FederationNotificationChannel.CONSOLE.value == "console"
        assert FederationNotificationChannel.NOOP.value == "noop"

    def test_is_str_enum(self) -> None:
        assert isinstance(FederationNotificationChannel.EMAIL, str)
        assert FederationNotificationChannel.EMAIL == "email"


# ===========================================================================
# FederationNotificationStatus
# ===========================================================================

class TestFederationNotificationStatus:
    """Tests for the FederationNotificationStatus enum."""

    def test_all_5_statuses_exist(self) -> None:
        expected = ["pending", "sent", "failed", "cancelled", "skipped"]
        assert len(FederationNotificationStatus) == 5
        for value in expected:
            assert value in [e.value for e in FederationNotificationStatus]

    def test_specific_enum_values(self) -> None:
        assert FederationNotificationStatus.PENDING.value == "pending"
        assert FederationNotificationStatus.SENT.value == "sent"
        assert FederationNotificationStatus.FAILED.value == "failed"
        assert FederationNotificationStatus.CANCELLED.value == "cancelled"
        assert FederationNotificationStatus.SKIPPED.value == "skipped"

    def test_is_str_enum(self) -> None:
        assert isinstance(FederationNotificationStatus.PENDING, str)
        assert FederationNotificationStatus.PENDING == "pending"


# ===========================================================================
# FederationNotificationEventType
# ===========================================================================

class TestFederationNotificationEventType:
    """Tests for the FederationNotificationEventType enum."""

    def test_all_6_event_types_exist(self) -> None:
        expected = [
            "approval.created",
            "approval.approved",
            "approval.rejected",
            "approval.escalated",
            "approval.cancelled",
            "approval.expired",
        ]
        assert len(FederationNotificationEventType) == 6
        for value in expected:
            assert value in [e.value for e in FederationNotificationEventType]

    def test_specific_enum_values(self) -> None:
        assert FederationNotificationEventType.APPROVAL_CREATED.value == "approval.created"
        assert FederationNotificationEventType.APPROVAL_APPROVED.value == "approval.approved"
        assert FederationNotificationEventType.APPROVAL_REJECTED.value == "approval.rejected"
        assert FederationNotificationEventType.APPROVAL_ESCALATED.value == "approval.escalated"
        assert FederationNotificationEventType.APPROVAL_CANCELLED.value == "approval.cancelled"
        assert FederationNotificationEventType.APPROVAL_EXPIRED.value == "approval.expired"

    def test_is_str_enum(self) -> None:
        assert isinstance(FederationNotificationEventType.APPROVAL_CREATED, str)
        assert FederationNotificationEventType.APPROVAL_CREATED == "approval.created"


# ===========================================================================
# FederationNotificationMessage
# ===========================================================================

class TestFederationNotificationMessage:
    """Tests for the FederationNotificationMessage model."""

    def test_valid_creation_minimal(self) -> None:
        msg = FederationNotificationMessage(
            notification_id="fn_001",
            approval_id="fap_001",
            event_type=FederationNotificationEventType.APPROVAL_CREATED,
            channel=FederationNotificationChannel.EMAIL,
            body="Approval request created",
            created_at=_now(),
        )
        assert msg.notification_id == "fn_001"
        assert msg.approval_id == "fap_001"
        assert msg.federation_id is None
        assert msg.event_type == FederationNotificationEventType.APPROVAL_CREATED
        assert msg.channel == FederationNotificationChannel.EMAIL
        assert msg.recipients == []
        assert msg.subject is None
        assert msg.body == "Approval request created"
        assert msg.payload == {}
        assert msg.status == FederationNotificationStatus.PENDING
        assert msg.attempt_count == 0
        assert msg.max_attempts == 3
        assert msg.last_error is None
        assert msg.sent_at is None
        assert msg.next_attempt_at is None

    def test_valid_creation_all_fields(self) -> None:
        now = _now()
        msg = FederationNotificationMessage(
            notification_id="fn_abc123",
            approval_id="fap_001",
            federation_id="frp_plan1",
            event_type=FederationNotificationEventType.APPROVAL_ESCALATED,
            channel=FederationNotificationChannel.SLACK,
            recipients=["admin-1", "admin-2"],
            subject="Approval Escalated",
            body="The approval request has been escalated",
            payload={"priority": "high", "escalation_level": 2},
            status=FederationNotificationStatus.SENT,
            attempt_count=1,
            max_attempts=5,
            last_error=None,
            created_at=now,
            sent_at=now + timedelta(seconds=2),
            next_attempt_at=None,
        )
        assert msg.federation_id == "frp_plan1"
        assert msg.event_type == FederationNotificationEventType.APPROVAL_ESCALATED
        assert msg.channel == FederationNotificationChannel.SLACK
        assert msg.recipients == ["admin-1", "admin-2"]
        assert msg.subject == "Approval Escalated"
        assert msg.payload == {"priority": "high", "escalation_level": 2}
        assert msg.status == FederationNotificationStatus.SENT
        assert msg.attempt_count == 1
        assert msg.max_attempts == 5
        assert msg.sent_at is not None

    def test_notification_id_must_start_with_fn_prefix(self) -> None:
        with pytest.raises(ValidationError, match="fn_"):
            FederationNotificationMessage(
                notification_id="bad_id",
                approval_id="fap_001",
                event_type=FederationNotificationEventType.APPROVAL_CREATED,
                channel=FederationNotificationChannel.EMAIL,
                body="Test",
                created_at=_now(),
            )

    def test_created_at_must_be_timezone_aware(self) -> None:
        naive_dt = datetime(2026, 1, 1, 12, 0, 0)
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederationNotificationMessage(
                notification_id="fn_001",
                approval_id="fap_001",
                event_type=FederationNotificationEventType.APPROVAL_CREATED,
                channel=FederationNotificationChannel.EMAIL,
                body="Test",
                created_at=naive_dt,
            )

    def test_status_defaults_to_pending(self) -> None:
        msg = FederationNotificationMessage(
            notification_id="fn_001",
            approval_id="fap_001",
            event_type=FederationNotificationEventType.APPROVAL_CREATED,
            channel=FederationNotificationChannel.CONSOLE,
            body="Test",
            created_at=_now(),
        )
        assert msg.status == FederationNotificationStatus.PENDING

    def test_attempt_count_defaults_to_zero(self) -> None:
        msg = FederationNotificationMessage(
            notification_id="fn_001",
            approval_id="fap_001",
            event_type=FederationNotificationEventType.APPROVAL_CREATED,
            channel=FederationNotificationChannel.CONSOLE,
            body="Test",
            created_at=_now(),
        )
        assert msg.attempt_count == 0

    def test_max_attempts_defaults_to_three(self) -> None:
        msg = FederationNotificationMessage(
            notification_id="fn_001",
            approval_id="fap_001",
            event_type=FederationNotificationEventType.APPROVAL_CREATED,
            channel=FederationNotificationChannel.CONSOLE,
            body="Test",
            created_at=_now(),
        )
        assert msg.max_attempts == 3

    def test_list_and_dict_fields_are_independent(self) -> None:
        msg1 = FederationNotificationMessage(
            notification_id="fn_001",
            approval_id="fap_001",
            event_type=FederationNotificationEventType.APPROVAL_CREATED,
            channel=FederationNotificationChannel.EMAIL,
            body="Test",
            created_at=_now(),
        )
        msg2 = FederationNotificationMessage(
            notification_id="fn_002",
            approval_id="fap_002",
            event_type=FederationNotificationEventType.APPROVAL_CREATED,
            channel=FederationNotificationChannel.EMAIL,
            body="Test",
            created_at=_now(),
        )
        msg1.recipients.append("admin-1")
        msg1.payload["key"] = "value"
        assert msg2.recipients == []
        assert msg2.payload == {}


# ===========================================================================
# FederationNotificationDelivery
# ===========================================================================

class TestFederationNotificationDelivery:
    """Tests for the FederationNotificationDelivery model."""

    def test_valid_creation_sent(self) -> None:
        now = _now()
        delivery = FederationNotificationDelivery(
            notification_id="fn_001",
            channel=FederationNotificationChannel.EMAIL,
            status=FederationNotificationStatus.SENT,
            delivered_at=now,
        )
        assert delivery.notification_id == "fn_001"
        assert delivery.channel == FederationNotificationChannel.EMAIL
        assert delivery.status == FederationNotificationStatus.SENT
        assert delivery.error is None
        assert delivery.delivered_at is not None

    def test_valid_creation_failed(self) -> None:
        delivery = FederationNotificationDelivery(
            notification_id="fn_001",
            channel=FederationNotificationChannel.WEBHOOK,
            status=FederationNotificationStatus.FAILED,
            error="Connection timeout",
        )
        assert delivery.status == FederationNotificationStatus.FAILED
        assert delivery.error == "Connection timeout"
        assert delivery.delivered_at is None

    def test_minimal_creation(self) -> None:
        delivery = FederationNotificationDelivery(
            notification_id="fn_001",
            channel=FederationNotificationChannel.CONSOLE,
            status=FederationNotificationStatus.PENDING,
        )
        assert delivery.error is None
        assert delivery.delivered_at is None


# ===========================================================================
# FederationNotificationPolicy
# ===========================================================================

class TestFederationNotificationPolicy:
    """Tests for the FederationNotificationPolicy model."""

    def test_defaults(self) -> None:
        policy = FederationNotificationPolicy()
        assert policy.enabled is False
        assert policy.default_channels == [FederationNotificationChannel.CONSOLE]
        assert policy.recipients_by_channel == {}
        assert policy.max_attempts == 3
        assert policy.backoff_seconds == 60
        assert policy.webhook_url is None
        assert policy.webhook_timeout_seconds == 5

    def test_with_values(self) -> None:
        policy = FederationNotificationPolicy(
            enabled=True,
            default_channels=[FederationNotificationChannel.EMAIL, FederationNotificationChannel.SLACK],
            recipients_by_channel={"email": ["admin-1"], "slack": ["#approvals"]},
            max_attempts=5,
            backoff_seconds=120,
            webhook_url="https://hooks.example.com/notify",
            webhook_timeout_seconds=10,
        )
        assert policy.enabled is True
        assert len(policy.default_channels) == 2
        assert policy.recipients_by_channel == {"email": ["admin-1"], "slack": ["#approvals"]}
        assert policy.max_attempts == 5
        assert policy.backoff_seconds == 120
        assert policy.webhook_url == "https://hooks.example.com/notify"
        assert policy.webhook_timeout_seconds == 10

    def test_default_channels_list_is_independent(self) -> None:
        p1 = FederationNotificationPolicy()
        p2 = FederationNotificationPolicy()
        p1.default_channels.append(FederationNotificationChannel.EMAIL)
        assert p2.default_channels == [FederationNotificationChannel.CONSOLE]

    def test_recipients_by_channel_dict_is_independent(self) -> None:
        p1 = FederationNotificationPolicy()
        p2 = FederationNotificationPolicy()
        p1.recipients_by_channel["email"] = ["admin-1"]
        assert p2.recipients_by_channel == {}


# ===========================================================================
# FederationNotificationTarget
# ===========================================================================

class TestFederationNotificationTarget:
    """Tests for the FederationNotificationTarget model."""

    def test_valid_creation(self) -> None:
        target = FederationNotificationTarget(
            channel=FederationNotificationChannel.SLACK,
            recipients=["#approvals", "#ops"],
            config={"webhook_url": "https://hooks.slack.com/xxx"},
        )
        assert target.channel == FederationNotificationChannel.SLACK
        assert target.recipients == ["#approvals", "#ops"]
        assert target.config == {"webhook_url": "https://hooks.slack.com/xxx"}

    def test_minimal_creation(self) -> None:
        target = FederationNotificationTarget(
            channel=FederationNotificationChannel.EMAIL,
        )
        assert target.recipients == []
        assert target.config == {}

    def test_list_and_dict_fields_are_independent(self) -> None:
        t1 = FederationNotificationTarget(channel=FederationNotificationChannel.EMAIL)
        t2 = FederationNotificationTarget(channel=FederationNotificationChannel.EMAIL)
        t1.recipients.append("admin-1")
        t1.config["key"] = "value"
        assert t2.recipients == []
        assert t2.config == {}


# ===========================================================================
# FederationNotificationDispatchResult
# ===========================================================================

class TestFederationNotificationDispatchResult:
    """Tests for the FederationNotificationDispatchResult model."""

    def test_defaults(self) -> None:
        result = FederationNotificationDispatchResult()
        assert result.total_dispatched == 0
        assert result.total_sent == 0
        assert result.total_failed == 0
        assert result.total_skipped == 0
        assert result.errors == []

    def test_with_values(self) -> None:
        result = FederationNotificationDispatchResult(
            total_dispatched=10,
            total_sent=8,
            total_failed=1,
            total_skipped=1,
            errors=["Webhook timeout for fn_003"],
        )
        assert result.total_dispatched == 10
        assert result.total_sent == 8
        assert result.total_failed == 1
        assert result.total_skipped == 1
        assert result.errors == ["Webhook timeout for fn_003"]

    def test_errors_list_is_independent(self) -> None:
        r1 = FederationNotificationDispatchResult()
        r2 = FederationNotificationDispatchResult()
        r1.errors.append("some error")
        assert r2.errors == []
