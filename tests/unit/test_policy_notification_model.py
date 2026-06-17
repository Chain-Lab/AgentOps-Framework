"""Tests for policy notification models."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agent_app.governance.policy_notification import (
    PolicyNotificationMessage,
    PolicyNotificationRule,
    PolicyNotificationRuleStatus,
    PolicyNotificationSeverity,
    PolicyNotificationStatus,
)


# ---------------------------------------------------------------------------
# PolicyNotificationSeverity
# ---------------------------------------------------------------------------

class TestPolicyNotificationSeverity:
    def test_values(self):
        assert PolicyNotificationSeverity.INFO == "info"
        assert PolicyNotificationSeverity.WARNING == "warning"
        assert PolicyNotificationSeverity.ERROR == "error"
        assert PolicyNotificationSeverity.CRITICAL == "critical"


# ---------------------------------------------------------------------------
# PolicyNotificationStatus
# ---------------------------------------------------------------------------

class TestPolicyNotificationStatus:
    def test_values(self):
        assert PolicyNotificationStatus.PENDING == "pending"
        assert PolicyNotificationStatus.SENT == "sent"
        assert PolicyNotificationStatus.FAILED == "failed"
        assert PolicyNotificationStatus.SUPPRESSED == "suppressed"


# ---------------------------------------------------------------------------
# PolicyNotificationMessage
# ---------------------------------------------------------------------------

class TestPolicyNotificationMessage:
    def test_valid_message(self):
        msg = PolicyNotificationMessage(
            notification_id="pn_001",
            event_type="policy.created",
            severity=PolicyNotificationSeverity.INFO,
            title="Policy Created",
            body="A new policy was created.",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        assert msg.notification_id == "pn_001"
        assert msg.event_type == "policy.created"
        assert msg.severity == PolicyNotificationSeverity.INFO
        assert msg.title == "Policy Created"
        assert msg.body == "A new policy was created."
        assert msg.status == PolicyNotificationStatus.PENDING
        assert msg.metadata == {}
        assert msg.source_type is None
        assert msg.source_id is None
        assert msg.actor_id is None
        assert msg.sent_at is None
        assert msg.error is None

    def test_id_prefix_validation(self):
        with pytest.raises(ValidationError, match="pn_"):
            PolicyNotificationMessage(
                notification_id="bad_id",
                event_type="policy.created",
                severity=PolicyNotificationSeverity.INFO,
                title="T",
                body="B",
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )

    def test_tz_aware_created_at(self):
        with pytest.raises(ValidationError, match="timezone-aware"):
            PolicyNotificationMessage(
                notification_id="pn_001",
                event_type="policy.created",
                severity=PolicyNotificationSeverity.INFO,
                title="T",
                body="B",
                created_at=datetime(2026, 1, 1),
            )

    def test_with_source_and_actor(self):
        msg = PolicyNotificationMessage(
            notification_id="pn_002",
            event_type="rollout.step_completed",
            severity=PolicyNotificationSeverity.WARNING,
            title="Step Done",
            body="Step completed.",
            source_type="rollout_step",
            source_id="rs_123",
            actor_id="user_42",
            metadata={"key": "value"},
            created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        assert msg.source_type == "rollout_step"
        assert msg.source_id == "rs_123"
        assert msg.actor_id == "user_42"
        assert msg.metadata == {"key": "value"}


# ---------------------------------------------------------------------------
# PolicyNotificationRuleStatus
# ---------------------------------------------------------------------------

class TestPolicyNotificationRuleStatus:
    def test_values(self):
        assert PolicyNotificationRuleStatus.ENABLED == "enabled"
        assert PolicyNotificationRuleStatus.DISABLED == "disabled"


# ---------------------------------------------------------------------------
# PolicyNotificationRule
# ---------------------------------------------------------------------------

class TestPolicyNotificationRule:
    def test_valid_rule(self):
        rule = PolicyNotificationRule(
            rule_id="pnr_001",
            name="Alert on critical events",
            event_types=["policy.violated"],
        )
        assert rule.rule_id == "pnr_001"
        assert rule.name == "Alert on critical events"
        assert rule.event_types == ["policy.violated"]
        assert rule.severity == PolicyNotificationSeverity.INFO
        assert rule.status == PolicyNotificationRuleStatus.ENABLED
        assert rule.source_types == []
        assert rule.metadata == {}

    def test_rule_id_prefix(self):
        with pytest.raises(ValidationError, match="pnr_"):
            PolicyNotificationRule(
                rule_id="bad_id",
                name="Rule",
                event_types=["policy.created"],
            )

    def test_empty_event_types_invalid(self):
        with pytest.raises(ValidationError, match="event_types must not be empty"):
            PolicyNotificationRule(
                rule_id="pnr_001",
                name="Rule",
                event_types=[],
            )

    def test_with_templates(self):
        rule = PolicyNotificationRule(
            rule_id="pnr_002",
            name="Templated Rule",
            event_types=["policy.expired"],
            title_template="Policy {policy_id} expired",
            body_template="Policy {policy_id} has expired at {expired_at}.",
        )
        assert rule.title_template == "Policy {policy_id} expired"
        assert rule.body_template == "Policy {policy_id} has expired at {expired_at}."

    def test_default_channels_is_log(self):
        rule = PolicyNotificationRule(
            rule_id="pnr_003",
            name="Default Channels Rule",
            event_types=["policy.created"],
        )
        assert rule.channels == ["log"]
