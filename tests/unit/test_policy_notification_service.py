"""Tests for PolicyNotificationService (Phase 44)."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from agent_app.governance.policy_notification import (
    PolicyNotificationRule,
    PolicyNotificationRuleStatus,
    PolicyNotificationSeverity,
    PolicyNotificationStatus,
    PolicyNotificationMessage,
)
from agent_app.runtime.policy_notification_store import InMemoryPolicyNotificationStore
from agent_app.runtime.policy_notification_rule_store import InMemoryPolicyNotificationRuleStore
from agent_app.runtime.policy_notification_channels import (
    InMemoryNotificationChannel,
    FailingNotificationChannel,
    LogNotificationChannel,
)
from agent_app.runtime.policy_notification_service import PolicyNotificationService


def _make_rule(
    rule_id: str = "pnr_001",
    name: str = "Test Rule",
    event_types: list[str] | None = None,
    severity: PolicyNotificationSeverity = PolicyNotificationSeverity.WARNING,
    status: PolicyNotificationRuleStatus = PolicyNotificationRuleStatus.ENABLED,
    source_types: list[str] | None = None,
    channels: list[str] | None = None,
    title_template: str | None = None,
    body_template: str | None = None,
) -> PolicyNotificationRule:
    return PolicyNotificationRule(
        rule_id=rule_id,
        name=name,
        event_types=event_types or ["policy.violation"],
        severity=severity,
        status=status,
        source_types=source_types or [],
        channels=channels or ["memory"],
        title_template=title_template,
        body_template=body_template,
    )


async def _make_service(
    channels: dict | None = None,
    rules: list[PolicyNotificationRule] | None = None,
) -> PolicyNotificationService:
    store = InMemoryPolicyNotificationStore()
    rule_store = InMemoryPolicyNotificationRuleStore()
    ch = channels or {"memory": InMemoryNotificationChannel()}
    for r in (rules or []):
        await rule_store.create(r)
    return PolicyNotificationService(
        notification_store=store,
        rule_store=rule_store,
        channels=ch,
    )


class TestNotifyEvent:
    @pytest.mark.asyncio
    async def test_matching_rule_creates_notification(self):
        rule = _make_rule(event_types=["policy.violation"], channels=["memory"])
        svc = await _make_service(rules=[rule])
        results = await svc.notify_event(
            event_type="policy.violation",
            data={"detail": "test"},
        )
        assert len(results) == 1
        msg = results[0]
        assert msg.event_type == "policy.violation"
        assert msg.status == PolicyNotificationStatus.SENT
        assert msg.sent_at is not None
        # Verify stored
        stored = await svc.list_notifications()
        assert len(stored) == 1

    @pytest.mark.asyncio
    async def test_no_matching_rule(self):
        rule = _make_rule(event_types=["policy.violation"])
        svc = await _make_service(rules=[rule])
        results = await svc.notify_event(
            event_type="unrelated.event",
            data={"detail": "test"},
        )
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_source_type_filter(self):
        rule = _make_rule(
            event_types=["policy.violation"],
            source_types=["rollout_step"],
        )
        svc = await _make_service(rules=[rule])
        # Matching source_type
        results = await svc.notify_event(
            event_type="policy.violation",
            data={},
            source_type="rollout_step",
        )
        assert len(results) == 1

        # Non-matching source_type
        results2 = await svc.notify_event(
            event_type="policy.violation",
            data={},
            source_type="agent_run",
        )
        assert len(results2) == 0

    @pytest.mark.asyncio
    async def test_disabled_rule_ignored(self):
        rule = _make_rule(
            event_types=["policy.violation"],
            status=PolicyNotificationRuleStatus.DISABLED,
        )
        svc = await _make_service(rules=[rule])
        results = await svc.notify_event(
            event_type="policy.violation",
            data={},
        )
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_unknown_channel_fails(self):
        rule = _make_rule(
            event_types=["policy.violation"],
            channels=["nonexistent"],
        )
        svc = await _make_service(rules=[rule], channels={"memory": InMemoryNotificationChannel()})
        results = await svc.notify_event(
            event_type="policy.violation",
            data={},
        )
        assert len(results) == 1
        assert results[0].status == PolicyNotificationStatus.FAILED
        assert results[0].error is not None
        assert "channel_errors" in results[0].error

    @pytest.mark.asyncio
    async def test_failing_channel_marks_failed(self):
        rule = _make_rule(
            event_types=["policy.violation"],
            channels=["failing"],
        )
        svc = await _make_service(
            rules=[rule],
            channels={"failing": FailingNotificationChannel()},
        )
        results = await svc.notify_event(
            event_type="policy.violation",
            data={},
        )
        assert len(results) == 1
        assert results[0].status == PolicyNotificationStatus.FAILED

    @pytest.mark.asyncio
    async def test_template_rendering(self):
        rule = _make_rule(
            event_types=["policy.violation"],
            title_template="Alert: {action}",
            body_template="Action {action} on {target}",
        )
        svc = await _make_service(rules=[rule])
        results = await svc.notify_event(
            event_type="policy.violation",
            data={"action": "delete", "target": "resource-1"},
        )
        assert len(results) == 1
        assert results[0].title == "Alert: delete"
        assert results[0].body == "Action delete on resource-1"

    @pytest.mark.asyncio
    async def test_template_rendering_missing_key_keeps_template(self):
        rule = _make_rule(
            event_types=["policy.violation"],
            title_template="Alert: {missing_key}",
        )
        svc = await _make_service(rules=[rule])
        results = await svc.notify_event(
            event_type="policy.violation",
            data={"action": "delete"},
        )
        assert len(results) == 1
        # Template kept as-is when key is missing
        assert results[0].title == "Alert: {missing_key}"

    @pytest.mark.asyncio
    async def test_no_template_uses_event_type_and_data_str(self):
        rule = _make_rule(
            event_types=["policy.violation"],
            title_template=None,
            body_template=None,
        )
        svc = await _make_service(rules=[rule])
        results = await svc.notify_event(
            event_type="policy.violation",
            data={"key": "value"},
        )
        assert len(results) == 1
        assert results[0].title == "policy.violation"
        assert "key" in results[0].body


class TestSendPending:
    @pytest.mark.asyncio
    async def test_send_pending(self):
        store = InMemoryPolicyNotificationStore()
        rule_store = InMemoryPolicyNotificationRuleStore()
        log_ch = LogNotificationChannel()
        svc = PolicyNotificationService(
            notification_store=store,
            rule_store=rule_store,
            channels={"log": log_ch},
        )

        # Create a pending notification directly
        msg = PolicyNotificationMessage(
            notification_id="pn_pend001",
            event_type="test.pending",
            severity=PolicyNotificationSeverity.INFO,
            title="Pending Test",
            body="Body",
            created_at=datetime.now(timezone.utc),
        )
        await store.create(msg)
        assert msg.status == PolicyNotificationStatus.PENDING

        # Send pending
        sent = await svc.send_pending()
        assert len(sent) == 1
        assert sent[0].status == PolicyNotificationStatus.SENT
        assert sent[0].sent_at is not None

    @pytest.mark.asyncio
    async def test_send_pending_no_log_channel(self):
        store = InMemoryPolicyNotificationStore()
        rule_store = InMemoryPolicyNotificationRuleStore()
        svc = PolicyNotificationService(
            notification_store=store,
            rule_store=rule_store,
            channels={},
        )

        msg = PolicyNotificationMessage(
            notification_id="pn_pend002",
            event_type="test.pending",
            severity=PolicyNotificationSeverity.INFO,
            title="Pending No Log",
            body="Body",
            created_at=datetime.now(timezone.utc),
        )
        await store.create(msg)

        sent = await svc.send_pending()
        assert len(sent) == 1
        assert sent[0].status == PolicyNotificationStatus.SENT


class TestListNotifications:
    @pytest.mark.asyncio
    async def test_list(self):
        rule = _make_rule(event_types=["policy.violation"], channels=["memory"])
        svc = await _make_service(rules=[rule])

        # Create a couple of notifications
        await svc.notify_event("policy.violation", data={"a": 1})
        await svc.notify_event("policy.violation", data={"b": 2})

        all_notifs = await svc.list_notifications()
        assert len(all_notifs) == 2

        # Filter by status
        sent_notifs = await svc.list_notifications(status=PolicyNotificationStatus.SENT)
        assert len(sent_notifs) == 2

    @pytest.mark.asyncio
    async def test_list_with_event_type_filter(self):
        rule1 = _make_rule(
            rule_id="pnr_evt01",
            event_types=["policy.violation"],
            channels=["memory"],
        )
        rule2 = _make_rule(
            rule_id="pnr_evt02",
            event_types=["policy.warning"],
            channels=["memory"],
        )
        store = InMemoryPolicyNotificationStore()
        rule_store = InMemoryPolicyNotificationRuleStore()
        await rule_store.create(rule1)
        await rule_store.create(rule2)
        svc = PolicyNotificationService(
            notification_store=store,
            rule_store=rule_store,
            channels={"memory": InMemoryNotificationChannel()},
        )

        await svc.notify_event("policy.violation", data={"x": 1})
        await svc.notify_event("policy.warning", data={"y": 2})

        violation_notifs = await svc.list_notifications(event_type="policy.violation")
        assert len(violation_notifs) == 1
        assert violation_notifs[0].event_type == "policy.violation"

        warning_notifs = await svc.list_notifications(event_type="policy.warning")
        assert len(warning_notifs) == 1
        assert warning_notifs[0].event_type == "policy.warning"
