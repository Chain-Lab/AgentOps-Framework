"""Tests for FederationNotificationService — enqueue and dispatch federation approval notifications.

Phase 49 Task 4.
Phase 50 Task 3: DLQ Integration + Retry Policy tests.
Phase 51 Task 6: Template rendering, preference checks, webhook signing integration.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationChannel,
    FederationNotificationDeadLetter,
    FederationNotificationDelivery,
    FederationNotificationDispatchResult,
    FederationNotificationDLQReason,
    FederationNotificationDLQStatus,
    FederationNotificationEventType,
    FederationNotificationMessage,
    FederationNotificationPolicy,
    FederationNotificationRetryPolicy,
    FederationNotificationStatus,
)
from agent_app.runtime.policy_rollout_federation_notification_adapters import (
    FakeFederationNotificationAdapter,
)
from agent_app.runtime.policy_rollout_federation_notification_dlq_store import (
    InMemoryFederationNotificationDLQStore,
)
from agent_app.runtime.policy_rollout_federation_notification_service import (
    FederationNotificationService,
)
from agent_app.runtime.policy_rollout_federation_notification_store import (
    InMemoryFederationNotificationStore,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy(
    *,
    channels: list[FederationNotificationChannel] | None = None,
    recipients_by_channel: dict[str, list[str]] | None = None,
    max_attempts: int = 3,
    backoff_seconds: int = 60,
) -> FederationNotificationPolicy:
    return FederationNotificationPolicy(
        enabled=True,
        default_channels=channels or [FederationNotificationChannel.CONSOLE],
        recipients_by_channel=recipients_by_channel or {},
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
    )


def _make_service(
    *,
    policy: FederationNotificationPolicy | None = None,
    store: InMemoryFederationNotificationStore | None = None,
    adapters: dict[FederationNotificationChannel, Any] | None = None,
    audit_logger: Any | None = None,
    change_event_store: Any | None = None,
    history_recorder: Any | None = None,
    dlq_store: Any | None = None,
    retry_policy: FederationNotificationRetryPolicy | None = None,
    by_channel_retry_policy: dict[str, FederationNotificationRetryPolicy] | None = None,
    template_service: Any | None = None,
    preference_service: Any | None = None,
    webhook_signature_service: Any | None = None,
) -> FederationNotificationService:
    policy = policy or _make_policy()
    store = store or InMemoryFederationNotificationStore()
    if adapters is None:
        adapters = {FederationNotificationChannel.CONSOLE: FakeFederationNotificationAdapter()}
    return FederationNotificationService(
        notification_store=store,
        adapters=adapters,
        notification_policy=policy,
        audit_logger=audit_logger,
        change_event_store=change_event_store,
        history_recorder=history_recorder,
        dlq_store=dlq_store,
        retry_policy=retry_policy,
        by_channel_retry_policy=by_channel_retry_policy,
        template_service=template_service,
        preference_service=preference_service,
        webhook_signature_service=webhook_signature_service,
    )


# ---------------------------------------------------------------------------
# enqueue_for_approval_created
# ---------------------------------------------------------------------------


class TestEnqueueApprovalCreated:
    """Tests for enqueue_for_approval_created."""

    @pytest.mark.asyncio
    async def test_creates_messages_for_each_default_channel(self) -> None:
        """One message per default channel in policy."""
        policy = _make_policy(
            channels=[
                FederationNotificationChannel.EMAIL,
                FederationNotificationChannel.SLACK,
            ],
        )
        svc = _make_service(policy=policy)
        messages = await svc.enqueue_for_approval_created(
            approval_id="ap_001",
            action="promote",
            requested_by="alice",
        )
        assert len(messages) == 2
        assert messages[0].channel == FederationNotificationChannel.EMAIL
        assert messages[1].channel == FederationNotificationChannel.SLACK
        assert all(m.event_type == FederationNotificationEventType.APPROVAL_CREATED for m in messages)

    @pytest.mark.asyncio
    async def test_message_has_correct_subject_and_body(self) -> None:
        """Subject and body contain the action name and requester."""
        svc = _make_service()
        messages = await svc.enqueue_for_approval_created(
            approval_id="ap_002",
            action="deploy",
            requested_by="bob",
        )
        msg = messages[0]
        assert "deploy" in msg.subject
        assert "bob" in msg.body
        assert "ap_002" in msg.body

    @pytest.mark.asyncio
    async def test_message_payload_contains_all_fields(self) -> None:
        """Payload includes approval_id, action, requested_by, etc."""
        svc = _make_service()
        messages = await svc.enqueue_for_approval_created(
            approval_id="ap_003",
            federation_id="fed_1",
            action="rollout",
            requested_by="carol",
            tenant_id="t1",
            environment="prod",
            region="us-east",
            ring="ring0",
        )
        payload = messages[0].payload
        assert payload["approval_id"] == "ap_003"
        assert payload["federation_id"] == "fed_1"
        assert payload["action"] == "rollout"
        assert payload["requested_by"] == "carol"
        assert payload["tenant_id"] == "t1"
        assert payload["environment"] == "prod"
        assert payload["region"] == "us-east"
        assert payload["ring"] == "ring0"

    @pytest.mark.asyncio
    async def test_message_status_is_pending(self) -> None:
        """Newly enqueued messages are PENDING."""
        svc = _make_service()
        messages = await svc.enqueue_for_approval_created(
            approval_id="ap_004",
            action="test",
            requested_by="dave",
        )
        assert all(m.status == FederationNotificationStatus.PENDING for m in messages)


# ---------------------------------------------------------------------------
# enqueue_for_approval_approved
# ---------------------------------------------------------------------------


class TestEnqueueApprovalApproved:
    """Tests for enqueue_for_approval_approved."""

    @pytest.mark.asyncio
    async def test_creates_messages(self) -> None:
        svc = _make_service()
        messages = await svc.enqueue_for_approval_approved(
            approval_id="ap_010",
            action="promote",
            approved_by="eve",
        )
        assert len(messages) == 1
        assert messages[0].event_type == FederationNotificationEventType.APPROVAL_APPROVED
        assert "eve" in messages[0].body

    @pytest.mark.asyncio
    async def test_subject_contains_action(self) -> None:
        svc = _make_service()
        messages = await svc.enqueue_for_approval_approved(
            approval_id="ap_011",
            action="deploy",
            approved_by="frank",
        )
        assert "deploy" in messages[0].subject


# ---------------------------------------------------------------------------
# enqueue_for_approval_rejected
# ---------------------------------------------------------------------------


class TestEnqueueApprovalRejected:
    """Tests for enqueue_for_approval_rejected."""

    @pytest.mark.asyncio
    async def test_creates_messages(self) -> None:
        svc = _make_service()
        messages = await svc.enqueue_for_approval_rejected(
            approval_id="ap_020",
            action="promote",
            rejected_by="grace",
        )
        assert len(messages) == 1
        assert messages[0].event_type == FederationNotificationEventType.APPROVAL_REJECTED
        assert "grace" in messages[0].body

    @pytest.mark.asyncio
    async def test_subject_indicates_rejection(self) -> None:
        svc = _make_service()
        messages = await svc.enqueue_for_approval_rejected(
            approval_id="ap_021",
            action="deploy",
            rejected_by="heidi",
        )
        assert "Rejected" in messages[0].subject


# ---------------------------------------------------------------------------
# enqueue_for_approval_escalated
# ---------------------------------------------------------------------------


class TestEnqueueApprovalEscalated:
    """Tests for enqueue_for_approval_escalated."""

    @pytest.mark.asyncio
    async def test_creates_messages(self) -> None:
        svc = _make_service()
        messages = await svc.enqueue_for_approval_escalated(
            approval_id="ap_030",
            action="promote",
            escalated_by="ivan",
            escalation_level=2,
        )
        assert len(messages) == 1
        assert messages[0].event_type == FederationNotificationEventType.APPROVAL_ESCALATED

    @pytest.mark.asyncio
    async def test_escalation_level_in_subject_and_body(self) -> None:
        svc = _make_service()
        messages = await svc.enqueue_for_approval_escalated(
            approval_id="ap_031",
            action="deploy",
            escalated_by="judy",
            escalation_level=3,
        )
        assert "Level 3" in messages[0].subject
        assert "level 3" in messages[0].body
        assert "judy" in messages[0].body

    @pytest.mark.asyncio
    async def test_escalated_by_none_omits_by_clause(self) -> None:
        svc = _make_service()
        messages = await svc.enqueue_for_approval_escalated(
            approval_id="ap_032",
            action="deploy",
            escalated_by=None,
            escalation_level=1,
        )
        # Should not have " by None" in body
        assert "by None" not in messages[0].body


# ---------------------------------------------------------------------------
# Recipients resolution
# ---------------------------------------------------------------------------


class TestRecipientsResolution:
    """Tests for recipient resolution logic."""

    @pytest.mark.asyncio
    async def test_uses_parameter_recipients_when_provided(self) -> None:
        """Explicit recipients parameter takes precedence."""
        svc = _make_service()
        messages = await svc.enqueue_for_approval_created(
            approval_id="ap_040",
            action="promote",
            requested_by="alice",
            recipients=["user1@example.com", "user2@example.com"],
        )
        assert messages[0].recipients == ["user1@example.com", "user2@example.com"]

    @pytest.mark.asyncio
    async def test_falls_back_to_policy_recipients_by_channel(self) -> None:
        """When recipients parameter is None, use policy.recipients_by_channel."""
        policy = _make_policy(
            recipients_by_channel={
                "console": ["ops-team@example.com"],
            },
        )
        svc = _make_service(policy=policy)
        messages = await svc.enqueue_for_approval_created(
            approval_id="ap_041",
            action="promote",
            requested_by="alice",
        )
        assert messages[0].recipients == ["ops-team@example.com"]

    @pytest.mark.asyncio
    async def test_empty_recipients_when_no_policy_match(self) -> None:
        """When recipients param is None and no policy mapping, recipients is empty."""
        svc = _make_service()
        messages = await svc.enqueue_for_approval_created(
            approval_id="ap_042",
            action="promote",
            requested_by="alice",
        )
        assert messages[0].recipients == []


# ---------------------------------------------------------------------------
# dispatch_pending
# ---------------------------------------------------------------------------


class TestDispatchPending:
    """Tests for dispatch_pending."""

    @pytest.mark.asyncio
    async def test_marks_sent_successfully(self) -> None:
        """Successful delivery marks message as SENT."""
        store = InMemoryFederationNotificationStore()
        adapter = FakeFederationNotificationAdapter()
        svc = _make_service(store=store, adapters={FederationNotificationChannel.CONSOLE: adapter})
        await svc.enqueue_for_approval_created(
            approval_id="ap_050",
            action="promote",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_sent == 1
        assert result.total_failed == 0
        # Verify the message is now SENT in store
        pending = await store.list_pending()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_no_adapter_marks_failed(self) -> None:
        """Missing adapter for channel marks message as FAILED."""
        store = InMemoryFederationNotificationStore()
        policy = _make_policy(channels=[FederationNotificationChannel.EMAIL])
        # No adapter for EMAIL
        svc = _make_service(policy=policy, store=store, adapters={})
        await svc.enqueue_for_approval_created(
            approval_id="ap_051",
            action="promote",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_failed == 1
        assert result.total_sent == 0
        assert any("No adapter" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_schedules_retry_on_failure_when_attempts_remain(self) -> None:
        """On adapter failure with attempts remaining, message stays PENDING with next_attempt_at."""
        store = InMemoryFederationNotificationStore()
        policy = _make_policy(max_attempts=3, backoff_seconds=30)

        failing_adapter = MagicMock()
        failing_adapter.send = AsyncMock(return_value=FederationNotificationDelivery(
            notification_id="fn_placeholder",
            channel=FederationNotificationChannel.CONSOLE,
            status=FederationNotificationStatus.FAILED,
            error="Connection refused",
        ))

        svc = _make_service(
            policy=policy,
            store=store,
            adapters={FederationNotificationChannel.CONSOLE: failing_adapter},
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_052",
            action="promote",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_failed == 1
        # After dispatch, the message should still be PENDING (retry scheduled)
        msg = await store.get((await store.list_by_approval("ap_052"))[0].notification_id)
        assert msg.status == FederationNotificationStatus.PENDING
        assert msg.next_attempt_at is not None

    @pytest.mark.asyncio
    async def test_marks_final_failure_when_max_attempts_exceeded(self) -> None:
        """When max_attempts is reached, message is marked as final FAILED."""
        store = InMemoryFederationNotificationStore()
        policy = _make_policy(max_attempts=1, backoff_seconds=30)

        failing_adapter = MagicMock()
        failing_adapter.send = AsyncMock(return_value=FederationNotificationDelivery(
            notification_id="fn_placeholder",
            channel=FederationNotificationChannel.CONSOLE,
            status=FederationNotificationStatus.FAILED,
            error="Timeout",
        ))

        svc = _make_service(
            policy=policy,
            store=store,
            adapters={FederationNotificationChannel.CONSOLE: failing_adapter},
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_053",
            action="promote",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_failed == 1
        msg = await store.get((await store.list_by_approval("ap_053"))[0].notification_id)
        assert msg.status == FederationNotificationStatus.FAILED
        assert msg.next_attempt_at is None

    @pytest.mark.asyncio
    async def test_returns_correct_counts(self) -> None:
        """Dispatch result has correct aggregate counts."""
        store = InMemoryFederationNotificationStore()
        policy = _make_policy(
            channels=[
                FederationNotificationChannel.CONSOLE,
                FederationNotificationChannel.EMAIL,
            ],
        )
        adapters: dict[FederationNotificationChannel, Any] = {
            FederationNotificationChannel.CONSOLE: FakeFederationNotificationAdapter(),
            # No EMAIL adapter — will fail
        }
        svc = _make_service(policy=policy, store=store, adapters=adapters)
        await svc.enqueue_for_approval_created(
            approval_id="ap_054",
            action="promote",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_dispatched == 2
        assert result.total_sent == 1
        assert result.total_failed == 1

    @pytest.mark.asyncio
    async def test_empty_pending_returns_zero_counts(self) -> None:
        """No pending messages yields all-zero counts."""
        svc = _make_service()
        result = await svc.dispatch_pending()
        assert result.total_dispatched == 0
        assert result.total_sent == 0
        assert result.total_failed == 0
        assert result.total_skipped == 0
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_adapter_exception_is_treated_as_failure(self) -> None:
        """If adapter.send() raises, treat it as a failed delivery."""
        store = InMemoryFederationNotificationStore()

        crashing_adapter = MagicMock()
        crashing_adapter.send = AsyncMock(side_effect=RuntimeError("boom"))

        svc = _make_service(
            store=store,
            adapters={FederationNotificationChannel.CONSOLE: crashing_adapter},
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_055",
            action="promote",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_failed == 1
        assert any("boom" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Audit / change event / history recording
# ---------------------------------------------------------------------------


class TestAuditAndSideEffects:
    """Tests for best-effort audit, change event, and history recording."""

    @pytest.mark.asyncio
    async def test_audit_logger_called_on_enqueue(self) -> None:
        """Audit logger.log is called for each enqueued message."""
        audit_logger = MagicMock()
        svc = _make_service(audit_logger=audit_logger)
        await svc.enqueue_for_approval_created(
            approval_id="ap_060",
            action="promote",
            requested_by="alice",
        )
        assert audit_logger.log.call_count == 1

    @pytest.mark.asyncio
    async def test_change_event_store_called_on_enqueue(self) -> None:
        """Change event store.record is called for each enqueued message."""
        change_event_store = MagicMock()
        svc = _make_service(change_event_store=change_event_store)
        await svc.enqueue_for_approval_created(
            approval_id="ap_061",
            action="promote",
            requested_by="alice",
        )
        assert change_event_store.record.call_count == 1

    @pytest.mark.asyncio
    async def test_history_recorder_called_when_federation_id_provided(self) -> None:
        """History recorder is called only when federation_id is not None."""
        history_recorder = MagicMock()
        svc = _make_service(history_recorder=history_recorder)
        await svc.enqueue_for_approval_created(
            approval_id="ap_062",
            federation_id="fed_1",
            action="promote",
            requested_by="alice",
        )
        assert history_recorder.record.call_count == 1

    @pytest.mark.asyncio
    async def test_history_recorder_not_called_when_federation_id_is_none(self) -> None:
        """History recorder is skipped when federation_id is None."""
        history_recorder = MagicMock()
        svc = _make_service(history_recorder=history_recorder)
        await svc.enqueue_for_approval_created(
            approval_id="ap_063",
            action="promote",
            requested_by="alice",
        )
        history_recorder.record.assert_not_called()

    @pytest.mark.asyncio
    async def test_audit_logger_failure_does_not_break_enqueue(self) -> None:
        """If audit logger raises, enqueue still returns messages."""
        audit_logger = MagicMock()
        audit_logger.log.side_effect = RuntimeError("audit broken")
        svc = _make_service(audit_logger=audit_logger)
        messages = await svc.enqueue_for_approval_created(
            approval_id="ap_064",
            action="promote",
            requested_by="alice",
        )
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_change_event_failure_does_not_break_enqueue(self) -> None:
        """If change event store raises, enqueue still returns messages."""
        change_event_store = MagicMock()
        change_event_store.record.side_effect = RuntimeError("change event broken")
        svc = _make_service(change_event_store=change_event_store)
        messages = await svc.enqueue_for_approval_created(
            approval_id="ap_065",
            action="promote",
            requested_by="alice",
        )
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_history_recorder_failure_does_not_break_enqueue(self) -> None:
        """If history recorder raises, enqueue still returns messages."""
        history_recorder = MagicMock()
        history_recorder.record.side_effect = RuntimeError("history broken")
        svc = _make_service(history_recorder=history_recorder)
        messages = await svc.enqueue_for_approval_created(
            approval_id="ap_066",
            federation_id="fed_1",
            action="promote",
            requested_by="alice",
        )
        assert len(messages) == 1


# ---------------------------------------------------------------------------
# Message properties
# ---------------------------------------------------------------------------


class TestMessageProperties:
    """Tests for message property defaults."""

    @pytest.mark.asyncio
    async def test_notification_id_has_fn_prefix(self) -> None:
        svc = _make_service()
        messages = await svc.enqueue_for_approval_created(
            approval_id="ap_070",
            action="promote",
            requested_by="alice",
        )
        assert messages[0].notification_id.startswith("fn_")

    @pytest.mark.asyncio
    async def test_max_attempts_from_policy(self) -> None:
        policy = _make_policy(max_attempts=5)
        svc = _make_service(policy=policy)
        messages = await svc.enqueue_for_approval_created(
            approval_id="ap_071",
            action="promote",
            requested_by="alice",
        )
        assert messages[0].max_attempts == 5

    @pytest.mark.asyncio
    async def test_created_at_is_timezone_aware(self) -> None:
        svc = _make_service()
        messages = await svc.enqueue_for_approval_created(
            approval_id="ap_072",
            action="promote",
            requested_by="alice",
        )
        assert messages[0].created_at.tzinfo is not None


# ---------------------------------------------------------------------------
# Helpers for DLQ tests
# ---------------------------------------------------------------------------


class FailingFakeAdapter:
    """Adapter that always fails."""

    async def send(self, message: FederationNotificationMessage) -> FederationNotificationDelivery:
        return FederationNotificationDelivery(
            notification_id=message.notification_id,
            channel=message.channel,
            status=FederationNotificationStatus.FAILED,
            error="Simulated failure",
        )


# ---------------------------------------------------------------------------
# DLQ Integration (Phase 50 Task 3)
# ---------------------------------------------------------------------------


class TestFederationNotificationServiceDLQ:
    """Tests for DLQ integration in notification service (Phase 50)."""

    def test_retry_policy_default_applies(self) -> None:
        """Default retry policy used when no channel override."""
        policy = FederationNotificationRetryPolicy(max_attempts=5, backoff_seconds=120)
        svc = _make_service(retry_policy=policy)
        result = svc.get_retry_policy_for_channel(FederationNotificationChannel.EMAIL)
        assert result is not None
        assert result.max_attempts == 5
        assert result.backoff_seconds == 120

    def test_retry_policy_channel_override_applies(self) -> None:
        """Channel override takes precedence over default."""
        default_policy = FederationNotificationRetryPolicy(max_attempts=3, backoff_seconds=60)
        email_policy = FederationNotificationRetryPolicy(max_attempts=10, backoff_seconds=30)
        svc = _make_service(
            retry_policy=default_policy,
            by_channel_retry_policy={"email": email_policy},
        )
        result = svc.get_retry_policy_for_channel(FederationNotificationChannel.EMAIL)
        assert result is not None
        assert result.max_attempts == 10
        assert result.backoff_seconds == 30

    def test_retry_policy_fallback_to_default(self) -> None:
        """Unknown channel falls back to default policy."""
        default_policy = FederationNotificationRetryPolicy(max_attempts=5, backoff_seconds=120)
        email_policy = FederationNotificationRetryPolicy(max_attempts=10, backoff_seconds=30)
        svc = _make_service(
            retry_policy=default_policy,
            by_channel_retry_policy={"email": email_policy},
        )
        # SLACK is not overridden, so should fall back to default
        result = svc.get_retry_policy_for_channel(FederationNotificationChannel.SLACK)
        assert result is not None
        assert result.max_attempts == 5
        assert result.backoff_seconds == 120

    @pytest.mark.asyncio
    async def test_below_max_attempts_does_not_create_dlq(self) -> None:
        """Failure below max_attempts doesn't create DLQ entry."""
        store = InMemoryFederationNotificationStore()
        dlq_store = InMemoryFederationNotificationDLQStore()
        retry_policy = FederationNotificationRetryPolicy(max_attempts=3, backoff_seconds=10, send_to_dlq=True)
        failing_adapter = FailingFakeAdapter()

        svc = _make_service(
            policy=_make_policy(max_attempts=3),
            store=store,
            adapters={FederationNotificationChannel.CONSOLE: failing_adapter},
            dlq_store=dlq_store,
            retry_policy=retry_policy,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_dlq_1",
            action="deploy",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_failed == 1
        # No DLQ entry yet — still below max_attempts
        dlq_items = await dlq_store.list()
        assert len(dlq_items) == 0

    @pytest.mark.asyncio
    async def test_exceeding_max_attempts_creates_dlq(self) -> None:
        """Failure at max_attempts creates DLQ entry."""
        store = InMemoryFederationNotificationStore()
        dlq_store = InMemoryFederationNotificationDLQStore()
        retry_policy = FederationNotificationRetryPolicy(max_attempts=1, backoff_seconds=10, send_to_dlq=True)
        failing_adapter = FailingFakeAdapter()

        svc = _make_service(
            policy=_make_policy(max_attempts=1),
            store=store,
            adapters={FederationNotificationChannel.CONSOLE: failing_adapter},
            dlq_store=dlq_store,
            retry_policy=retry_policy,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_dlq_2",
            action="deploy",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_failed == 1
        dlq_items = await dlq_store.list()
        assert len(dlq_items) == 1

    @pytest.mark.asyncio
    async def test_dlq_entry_has_correct_fields(self) -> None:
        """DLQ entry has correct notification_id, channel, reason, etc."""
        store = InMemoryFederationNotificationStore()
        dlq_store = InMemoryFederationNotificationDLQStore()
        retry_policy = FederationNotificationRetryPolicy(max_attempts=1, backoff_seconds=10, send_to_dlq=True)
        failing_adapter = FailingFakeAdapter()

        svc = _make_service(
            policy=_make_policy(max_attempts=1),
            store=store,
            adapters={FederationNotificationChannel.CONSOLE: failing_adapter},
            dlq_store=dlq_store,
            retry_policy=retry_policy,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_dlq_3",
            federation_id="fed_1",
            action="deploy",
            requested_by="alice",
        )
        await svc.dispatch_pending()
        dlq_items = await dlq_store.list()
        assert len(dlq_items) == 1
        entry = dlq_items[0]
        assert entry.dlq_id.startswith("fdlq_")
        assert entry.approval_id == "ap_dlq_3"
        assert entry.federation_id == "fed_1"
        assert entry.channel == "console"
        assert entry.reason == FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED
        assert entry.status == FederationNotificationDLQStatus.PENDING
        assert entry.failure_count == 1
        assert entry.last_error == "Simulated failure"

    @pytest.mark.asyncio
    async def test_send_to_dlq_false_does_not_create_dlq(self) -> None:
        """When send_to_dlq=False, no DLQ entry created even at max_attempts."""
        store = InMemoryFederationNotificationStore()
        dlq_store = InMemoryFederationNotificationDLQStore()
        retry_policy = FederationNotificationRetryPolicy(max_attempts=1, backoff_seconds=10, send_to_dlq=False)
        failing_adapter = FailingFakeAdapter()

        svc = _make_service(
            policy=_make_policy(max_attempts=1),
            store=store,
            adapters={FederationNotificationChannel.CONSOLE: failing_adapter},
            dlq_store=dlq_store,
            retry_policy=retry_policy,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_dlq_4",
            action="deploy",
            requested_by="alice",
        )
        await svc.dispatch_pending()
        dlq_items = await dlq_store.list()
        assert len(dlq_items) == 0

    @pytest.mark.asyncio
    async def test_no_dlq_store_does_not_crash(self) -> None:
        """Missing dlq_store doesn't crash when max retries exceeded."""
        store = InMemoryFederationNotificationStore()
        retry_policy = FederationNotificationRetryPolicy(max_attempts=1, backoff_seconds=10, send_to_dlq=True)
        failing_adapter = FailingFakeAdapter()

        svc = _make_service(
            policy=_make_policy(max_attempts=1),
            store=store,
            adapters={FederationNotificationChannel.CONSOLE: failing_adapter},
            dlq_store=None,
            retry_policy=retry_policy,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_dlq_5",
            action="deploy",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_failed == 1

    @pytest.mark.asyncio
    async def test_channel_specific_backoff_applied(self) -> None:
        """Channel override backoff is used for next_attempt_at."""
        store = InMemoryFederationNotificationStore()
        retry_policy = FederationNotificationRetryPolicy(max_attempts=3, backoff_seconds=10, send_to_dlq=True)
        channel_override = FederationNotificationRetryPolicy(max_attempts=3, backoff_seconds=300, send_to_dlq=True)
        failing_adapter = FailingFakeAdapter()

        svc = _make_service(
            policy=_make_policy(max_attempts=3, backoff_seconds=10),
            store=store,
            adapters={FederationNotificationChannel.CONSOLE: failing_adapter},
            retry_policy=retry_policy,
            by_channel_retry_policy={"console": channel_override},
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_dlq_6",
            action="deploy",
            requested_by="alice",
        )
        await svc.dispatch_pending()
        # The message should have next_attempt_at set with the channel override backoff
        msg = (await store.list_by_approval("ap_dlq_6"))[0]
        assert msg.next_attempt_at is not None
        # Verify backoff is from channel override (300s), not default (10s)
        delta = msg.next_attempt_at - datetime.now(timezone.utc)
        # Allow generous bounds — the backoff should be around 300 seconds
        assert delta.total_seconds() > 200

    @pytest.mark.asyncio
    async def test_dlq_creation_records_change_event(self) -> None:
        """Change event recorded when DLQ entry created."""
        store = InMemoryFederationNotificationStore()
        dlq_store = InMemoryFederationNotificationDLQStore()
        retry_policy = FederationNotificationRetryPolicy(max_attempts=1, backoff_seconds=10, send_to_dlq=True)
        change_event_store = MagicMock()
        failing_adapter = FailingFakeAdapter()

        svc = _make_service(
            policy=_make_policy(max_attempts=1),
            store=store,
            adapters={FederationNotificationChannel.CONSOLE: failing_adapter},
            dlq_store=dlq_store,
            retry_policy=retry_policy,
            change_event_store=change_event_store,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_dlq_7",
            action="deploy",
            requested_by="alice",
        )
        await svc.dispatch_pending()
        # Check that a change event with type "federation.notification.dlq_created" was recorded
        dlq_events = [
            call for call in change_event_store.record.call_args_list
            if call.kwargs.get("event_type") == "federation.notification.dlq_created"
        ]
        assert len(dlq_events) == 1
        payload = dlq_events[0].kwargs["payload"]
        assert payload["channel"] == "console"
        assert payload["reason"] == "max_retries_exceeded"

    @pytest.mark.asyncio
    async def test_successful_dispatch_does_not_create_dlq(self) -> None:
        """Successful delivery never creates DLQ."""
        store = InMemoryFederationNotificationStore()
        dlq_store = InMemoryFederationNotificationDLQStore()
        retry_policy = FederationNotificationRetryPolicy(max_attempts=1, backoff_seconds=10, send_to_dlq=True)

        svc = _make_service(
            policy=_make_policy(max_attempts=1),
            store=store,
            dlq_store=dlq_store,
            retry_policy=retry_policy,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_dlq_8",
            action="deploy",
            requested_by="alice",
        )
        await svc.dispatch_pending()
        dlq_items = await dlq_store.list()
        assert len(dlq_items) == 0

    @pytest.mark.asyncio
    async def test_multiple_failures_accumulate(self) -> None:
        """Repeated failures increment attempt_count before DLQ."""
        store = InMemoryFederationNotificationStore()
        dlq_store = InMemoryFederationNotificationDLQStore()
        retry_policy = FederationNotificationRetryPolicy(max_attempts=3, backoff_seconds=0, send_to_dlq=True)
        failing_adapter = FailingFakeAdapter()

        svc = _make_service(
            policy=_make_policy(max_attempts=3, backoff_seconds=0),
            store=store,
            adapters={FederationNotificationChannel.CONSOLE: failing_adapter},
            dlq_store=dlq_store,
            retry_policy=retry_policy,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_dlq_9",
            action="deploy",
            requested_by="alice",
        )
        # First two dispatches should fail but not create DLQ
        await svc.dispatch_pending()
        dlq_items = await dlq_store.list()
        assert len(dlq_items) == 0

        await svc.dispatch_pending()
        dlq_items = await dlq_store.list()
        assert len(dlq_items) == 0

        # Third failure should create DLQ
        await svc.dispatch_pending()
        dlq_items = await dlq_store.list()
        assert len(dlq_items) == 1
        assert dlq_items[0].failure_count == 3

    def test_retry_policy_default_when_none_configured(self) -> None:
        """When no retry_policy is configured, get_retry_policy_for_channel returns None."""
        svc = _make_service()
        result = svc.get_retry_policy_for_channel(FederationNotificationChannel.CONSOLE)
        # No retry policy configured — returns None so dispatch falls back to message.max_attempts
        assert result is None

    @pytest.mark.asyncio
    async def test_dlq_reason_max_retries_exceeded(self) -> None:
        """DLQ reason is MAX_RETRIES_EXCEEDED for adapter failures."""
        store = InMemoryFederationNotificationStore()
        dlq_store = InMemoryFederationNotificationDLQStore()
        retry_policy = FederationNotificationRetryPolicy(max_attempts=1, backoff_seconds=10, send_to_dlq=True)
        failing_adapter = FailingFakeAdapter()

        svc = _make_service(
            policy=_make_policy(max_attempts=1),
            store=store,
            adapters={FederationNotificationChannel.CONSOLE: failing_adapter},
            dlq_store=dlq_store,
            retry_policy=retry_policy,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_dlq_10",
            action="deploy",
            requested_by="alice",
        )
        await svc.dispatch_pending()
        dlq_items = await dlq_store.list()
        assert len(dlq_items) == 1
        assert dlq_items[0].reason == FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED

    @pytest.mark.asyncio
    async def test_dlq_reason_adapter_error_when_no_adapter(self) -> None:
        """Missing adapter creates DLQ with ADAPTER_ERROR reason."""
        store = InMemoryFederationNotificationStore()
        dlq_store = InMemoryFederationNotificationDLQStore()
        retry_policy = FederationNotificationRetryPolicy(max_attempts=1, backoff_seconds=10, send_to_dlq=True)

        svc = _make_service(
            policy=_make_policy(channels=[FederationNotificationChannel.EMAIL], max_attempts=1),
            store=store,
            adapters={},  # No adapter for EMAIL
            dlq_store=dlq_store,
            retry_policy=retry_policy,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_dlq_11",
            action="deploy",
            requested_by="alice",
        )
        await svc.dispatch_pending()
        dlq_items = await dlq_store.list()
        assert len(dlq_items) == 1
        assert dlq_items[0].reason == FederationNotificationDLQReason.ADAPTER_ERROR


# ---------------------------------------------------------------------------
# Phase 51: Template rendering, preference checks, webhook signing
# ---------------------------------------------------------------------------


class TestFederationNotificationServicePhase51:
    """Tests for Phase 51 integration: templates, preferences, webhook signing."""

    # --- Preference checks ---

    @pytest.mark.asyncio
    async def test_preference_opt_out_suppresses_notification(self) -> None:
        """should_deliver=False marks notification as SUPPRESSED."""
        store = InMemoryFederationNotificationStore()
        pref_service = MagicMock()
        pref_service.should_deliver = AsyncMock(return_value=False)

        svc = _make_service(
            store=store,
            preference_service=pref_service,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_1",
            action="deploy",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_skipped == 1
        # Message should be SUPPRESSED in store
        msg = await store.get((await store.list_by_approval("ap_p51_1"))[0].notification_id)
        assert msg.status == FederationNotificationStatus.SUPPRESSED

    @pytest.mark.asyncio
    async def test_preference_opt_in_allows_delivery(self) -> None:
        """should_deliver=True allows normal delivery flow."""
        store = InMemoryFederationNotificationStore()
        pref_service = MagicMock()
        pref_service.should_deliver = AsyncMock(return_value=True)

        svc = _make_service(
            store=store,
            preference_service=pref_service,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_2",
            action="deploy",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_sent == 1

    @pytest.mark.asyncio
    async def test_no_preference_service_default_delivers(self) -> None:
        """Missing preference_service delivers normally (backward compatible)."""
        store = InMemoryFederationNotificationStore()
        svc = _make_service(store=store, preference_service=None)
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_3",
            action="deploy",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_sent == 1

    # --- Template rendering ---

    @pytest.mark.asyncio
    async def test_template_rendering_applies_content(self) -> None:
        """Rendered subject/body replaces original message content."""
        from agent_app.governance.policy_rollout_federation_notification_template import (
            FederationNotificationRenderedContent,
            FederationNotificationTemplateFormat,
        )

        store = InMemoryFederationNotificationStore()
        template_service = MagicMock()
        now = datetime.now(timezone.utc)
        rendered = FederationNotificationRenderedContent(
            template_id="fnt_test",
            template_version=1,
            subject="Rendered Subject",
            body="Rendered body content",
            format=FederationNotificationTemplateFormat.TEXT,
            context_keys=["approval_id"],
            rendered_at=now,
        )
        template_service.render = AsyncMock(return_value=rendered)

        svc = _make_service(
            store=store,
            template_service=template_service,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_4",
            action="deploy",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_sent == 1
        # Verify the adapter received the rendered content
        adapter = svc._adapters[FederationNotificationChannel.CONSOLE]
        sent_msg = adapter.sent[0]
        assert sent_msg.subject == "Rendered Subject"
        assert sent_msg.body == "Rendered body content"

    @pytest.mark.asyncio
    async def test_template_rendering_failure_marks_template_failed(self) -> None:
        """TemplateMissingVariableError marks notification as TEMPLATE_FAILED."""
        from agent_app.governance.policy_rollout_federation_notification_template import (
            TemplateMissingVariableError,
        )

        store = InMemoryFederationNotificationStore()
        template_service = MagicMock()
        template_service.render = AsyncMock(
            side_effect=TemplateMissingVariableError("Missing: approval.id"),
        )

        svc = _make_service(
            store=store,
            template_service=template_service,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_5",
            action="deploy",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_failed == 1
        msg = await store.get((await store.list_by_approval("ap_p51_5"))[0].notification_id)
        assert msg.status == FederationNotificationStatus.TEMPLATE_FAILED

    @pytest.mark.asyncio
    async def test_template_disabled_marks_template_failed(self) -> None:
        """TemplateDisabledError marks notification as TEMPLATE_FAILED."""
        from agent_app.governance.policy_rollout_federation_notification_template import (
            TemplateDisabledError,
        )

        store = InMemoryFederationNotificationStore()
        template_service = MagicMock()
        template_service.render = AsyncMock(
            side_effect=TemplateDisabledError("Template is disabled"),
        )

        svc = _make_service(
            store=store,
            template_service=template_service,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_6",
            action="deploy",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_failed == 1
        msg = await store.get((await store.list_by_approval("ap_p51_6"))[0].notification_id)
        assert msg.status == FederationNotificationStatus.TEMPLATE_FAILED

    @pytest.mark.asyncio
    async def test_no_template_service_default_body(self) -> None:
        """Missing template_service uses existing body (backward compatible)."""
        store = InMemoryFederationNotificationStore()
        svc = _make_service(store=store, template_service=None)
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_7",
            action="deploy",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_sent == 1

    # --- Webhook signing ---

    @pytest.mark.asyncio
    async def test_webhook_signing_adds_headers(self) -> None:
        """Webhook channel messages get signature headers in payload."""
        from agent_app.runtime.policy_rollout_federation_webhook_signature import (
            FederationWebhookSignatureService,
        )

        store = InMemoryFederationNotificationStore()
        sig_service = FederationWebhookSignatureService()
        fake_webhook_adapter = FakeFederationNotificationAdapter()

        svc = _make_service(
            store=store,
            policy=_make_policy(channels=[FederationNotificationChannel.WEBHOOK]),
            adapters={FederationNotificationChannel.WEBHOOK: fake_webhook_adapter},
            webhook_signature_service=sig_service,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_8",
            action="deploy",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_sent == 1
        # Verify signature headers in payload
        sent_msg = fake_webhook_adapter.sent[0]
        assert "_signature_headers" in sent_msg.payload
        headers = sent_msg.payload["_signature_headers"]
        assert "X-AgentApp-Signature" in headers
        assert headers["X-AgentApp-Signature"].startswith("v1=")

    @pytest.mark.asyncio
    async def test_webhook_signing_failure_marks_signature_failed(self) -> None:
        """Signing error marks notification as SIGNATURE_FAILED."""
        store = InMemoryFederationNotificationStore()
        sig_service = MagicMock()
        sig_service.sign = MagicMock(side_effect=ValueError("Key not found"))
        sig_service.compute_digest = MagicMock(return_value="abc123")

        fake_webhook_adapter = FakeFederationNotificationAdapter()

        svc = _make_service(
            store=store,
            policy=_make_policy(channels=[FederationNotificationChannel.WEBHOOK]),
            adapters={FederationNotificationChannel.WEBHOOK: fake_webhook_adapter},
            webhook_signature_service=sig_service,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_9",
            action="deploy",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        assert result.total_failed == 1
        msg = await store.get((await store.list_by_approval("ap_p51_9"))[0].notification_id)
        assert msg.status == FederationNotificationStatus.SIGNATURE_FAILED

    # --- SUPPRESSED and TEMPLATE_FAILED not retried/DLQed ---

    @pytest.mark.asyncio
    async def test_suppressed_does_not_enter_dlq(self) -> None:
        """SUPPRESSED notifications are not retried or DLQed."""
        store = InMemoryFederationNotificationStore()
        dlq_store = InMemoryFederationNotificationDLQStore()
        pref_service = MagicMock()
        pref_service.should_deliver = AsyncMock(return_value=False)

        svc = _make_service(
            store=store,
            dlq_store=dlq_store,
            preference_service=pref_service,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_10",
            action="deploy",
            requested_by="alice",
        )
        await svc.dispatch_pending()
        dlq_items = await dlq_store.list()
        assert len(dlq_items) == 0

    @pytest.mark.asyncio
    async def test_template_failed_not_retried(self) -> None:
        """TEMPLATE_FAILED notifications are not retried."""
        from agent_app.governance.policy_rollout_federation_notification_template import (
            TemplateMissingVariableError,
        )

        store = InMemoryFederationNotificationStore()
        dlq_store = InMemoryFederationNotificationDLQStore()
        template_service = MagicMock()
        template_service.render = AsyncMock(
            side_effect=TemplateMissingVariableError("Missing var"),
        )

        svc = _make_service(
            store=store,
            dlq_store=dlq_store,
            template_service=template_service,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_11",
            action="deploy",
            requested_by="alice",
        )
        result = await svc.dispatch_pending()
        # Should be total_failed=1, not retried
        assert result.total_failed == 1
        # No DLQ entry — TEMPLATE_FAILED is not retried
        dlq_items = await dlq_store.list()
        assert len(dlq_items) == 0

    # --- replay_original ---

    @pytest.mark.asyncio
    async def test_replay_original_uses_original_body(self) -> None:
        """Replay sends the original body, not re-rendered."""
        from agent_app.runtime.policy_rollout_federation_webhook_signature import (
            FederationWebhookSignatureService,
        )

        dlq_store = InMemoryFederationNotificationDLQStore()
        now = datetime.now(timezone.utc)
        original_payload = {"approval_id": "ap_replay", "action": "deploy"}
        dlq_entry = FederationNotificationDeadLetter(
            dlq_id="fdlq_replay1",
            notification_id="fn_replay1",
            approval_id="ap_replay",
            channel="webhook",
            reason=FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED,
            status=FederationNotificationDLQStatus.PENDING,
            failure_count=3,
            last_error="Timeout",
            payload=original_payload,
            metadata={"event_type": "approval.created", "subject": "Test"},
            created_at=now,
            updated_at=now,
        )
        await dlq_store.create(dlq_entry)

        sig_service = FederationWebhookSignatureService()
        fake_webhook_adapter = FakeFederationNotificationAdapter()
        svc = _make_service(
            policy=_make_policy(channels=[FederationNotificationChannel.WEBHOOK]),
            adapters={FederationNotificationChannel.WEBHOOK: fake_webhook_adapter},
            webhook_signature_service=sig_service,
        )

        result = await svc.replay_original("fdlq_replay1", dlq_store)
        assert result.success is True
        # The sent body should contain the original payload
        sent_msg = fake_webhook_adapter.sent[0]
        assert "ap_replay" in sent_msg.body

    @pytest.mark.asyncio
    async def test_replay_original_generates_new_signature(self) -> None:
        """Replay generates a new timestamp/nonce/signature."""
        from agent_app.runtime.policy_rollout_federation_webhook_signature import (
            FederationWebhookSignatureService,
        )

        dlq_store = InMemoryFederationNotificationDLQStore()
        now = datetime.now(timezone.utc)
        dlq_entry = FederationNotificationDeadLetter(
            dlq_id="fdlq_replay2",
            notification_id="fn_replay2",
            approval_id="ap_replay2",
            channel="webhook",
            reason=FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED,
            status=FederationNotificationDLQStatus.PENDING,
            failure_count=3,
            last_error="Timeout",
            payload={"key": "value"},
            metadata={"event_type": "approval.created"},
            created_at=now,
            updated_at=now,
        )
        await dlq_store.create(dlq_entry)

        sig_service = FederationWebhookSignatureService()
        fake_webhook_adapter = FakeFederationNotificationAdapter()
        svc = _make_service(
            policy=_make_policy(channels=[FederationNotificationChannel.WEBHOOK]),
            adapters={FederationNotificationChannel.WEBHOOK: fake_webhook_adapter},
            webhook_signature_service=sig_service,
        )

        result = await svc.replay_original("fdlq_replay2", dlq_store)
        assert result.success is True
        # Signature headers should be present in payload
        sent_msg = fake_webhook_adapter.sent[0]
        assert "_signature_headers" in sent_msg.payload
        headers = sent_msg.payload["_signature_headers"]
        assert "X-AgentApp-Signature" in headers
        assert "X-AgentApp-Signature-Timestamp" in headers
        assert "X-AgentApp-Signature-Nonce" in headers

    @pytest.mark.asyncio
    async def test_replay_original_dry_run(self) -> None:
        """Dry run returns success without actually sending."""
        dlq_store = InMemoryFederationNotificationDLQStore()
        now = datetime.now(timezone.utc)
        dlq_entry = FederationNotificationDeadLetter(
            dlq_id="fdlq_replay3",
            notification_id="fn_replay3",
            approval_id="ap_replay3",
            channel="webhook",
            reason=FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED,
            status=FederationNotificationDLQStatus.PENDING,
            failure_count=3,
            last_error="Timeout",
            payload={"key": "value"},
            metadata={"event_type": "approval.created"},
            created_at=now,
            updated_at=now,
        )
        await dlq_store.create(dlq_entry)

        fake_webhook_adapter = FakeFederationNotificationAdapter()
        svc = _make_service(
            policy=_make_policy(channels=[FederationNotificationChannel.WEBHOOK]),
            adapters={FederationNotificationChannel.WEBHOOK: fake_webhook_adapter},
        )

        result = await svc.replay_original("fdlq_replay3", dlq_store, dry_run=True)
        assert result.success is True
        # No message should have been sent
        assert len(fake_webhook_adapter.sent) == 0

    @pytest.mark.asyncio
    async def test_replay_original_non_webhook_rejected(self) -> None:
        """Replay of non-webhook DLQ entry returns error."""
        dlq_store = InMemoryFederationNotificationDLQStore()
        now = datetime.now(timezone.utc)
        dlq_entry = FederationNotificationDeadLetter(
            dlq_id="fdlq_replay4",
            notification_id="fn_replay4",
            approval_id="ap_replay4",
            channel="console",  # Not webhook
            reason=FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED,
            status=FederationNotificationDLQStatus.PENDING,
            failure_count=3,
            last_error="Timeout",
            payload={"key": "value"},
            metadata={"event_type": "approval.created"},
            created_at=now,
            updated_at=now,
        )
        await dlq_store.create(dlq_entry)

        svc = _make_service()
        result = await svc.replay_original("fdlq_replay4", dlq_store)
        assert result.success is False
        assert "non-webhook" in result.error

    @pytest.mark.asyncio
    async def test_replay_original_max_replays_exceeded(self) -> None:
        """Replay fails when max replays are exceeded."""
        dlq_store = InMemoryFederationNotificationDLQStore()
        now = datetime.now(timezone.utc)
        dlq_entry = FederationNotificationDeadLetter(
            dlq_id="fdlq_replay5",
            notification_id="fn_replay5",
            approval_id="ap_replay5",
            channel="webhook",
            reason=FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED,
            status=FederationNotificationDLQStatus.PENDING,
            failure_count=3,
            last_error="Timeout",
            payload={"key": "value"},
            metadata={"event_type": "approval.created", "replay_count": 3},
            created_at=now,
            updated_at=now,
        )
        await dlq_store.create(dlq_entry)

        fake_webhook_adapter = FakeFederationNotificationAdapter()
        svc = _make_service(
            policy=_make_policy(channels=[FederationNotificationChannel.WEBHOOK]),
            adapters={FederationNotificationChannel.WEBHOOK: fake_webhook_adapter},
        )

        result = await svc.replay_original("fdlq_replay5", dlq_store, max_replays=3)
        assert result.success is False
        assert "Max replays exceeded" in result.error

    # --- Audit events ---

    @pytest.mark.asyncio
    async def test_preference_audit_event_recorded(self) -> None:
        """Suppressed notification records an audit event."""
        store = InMemoryFederationNotificationStore()
        audit_logger = MagicMock()
        pref_service = MagicMock()
        pref_service.should_deliver = AsyncMock(return_value=False)

        svc = _make_service(
            store=store,
            audit_logger=audit_logger,
            preference_service=pref_service,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_17",
            action="deploy",
            requested_by="alice",
        )
        await svc.dispatch_pending()

        # Find the suppressed audit event
        suppressed_calls = [
            c for c in audit_logger.log.call_args_list
            if c.kwargs.get("event") == "notification.suppressed"
        ]
        assert len(suppressed_calls) == 1

    @pytest.mark.asyncio
    async def test_template_failure_audit_event_recorded(self) -> None:
        """Template failed notification records an audit event."""
        from agent_app.governance.policy_rollout_federation_notification_template import (
            TemplateMissingVariableError,
        )

        store = InMemoryFederationNotificationStore()
        audit_logger = MagicMock()
        template_service = MagicMock()
        template_service.render = AsyncMock(
            side_effect=TemplateMissingVariableError("Missing var"),
        )

        svc = _make_service(
            store=store,
            audit_logger=audit_logger,
            template_service=template_service,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_18",
            action="deploy",
            requested_by="alice",
        )
        await svc.dispatch_pending()

        # Find the template_failed audit event
        tf_calls = [
            c for c in audit_logger.log.call_args_list
            if c.kwargs.get("event") == "notification.template_failed"
        ]
        assert len(tf_calls) == 1

    @pytest.mark.asyncio
    async def test_signing_failure_audit_event_recorded(self) -> None:
        """Signing failed notification records an audit event."""
        store = InMemoryFederationNotificationStore()
        audit_logger = MagicMock()
        sig_service = MagicMock()
        sig_service.sign = MagicMock(side_effect=ValueError("Key not found"))
        sig_service.compute_digest = MagicMock(return_value="abc123")

        fake_webhook_adapter = FakeFederationNotificationAdapter()

        svc = _make_service(
            store=store,
            policy=_make_policy(channels=[FederationNotificationChannel.WEBHOOK]),
            adapters={FederationNotificationChannel.WEBHOOK: fake_webhook_adapter},
            audit_logger=audit_logger,
            webhook_signature_service=sig_service,
        )
        await svc.enqueue_for_approval_created(
            approval_id="ap_p51_19",
            action="deploy",
            requested_by="alice",
        )
        await svc.dispatch_pending()

        # Find the signature_failed audit event
        sf_calls = [
            c for c in audit_logger.log.call_args_list
            if c.kwargs.get("event") == "notification.signature_failed"
        ]
        assert len(sf_calls) == 1

    # --- Replay ID format ---

    @pytest.mark.asyncio
    async def test_replay_original_replay_id_format(self) -> None:
        """Replay result has fwrp_ prefix on replay_id."""
        dlq_store = InMemoryFederationNotificationDLQStore()
        now = datetime.now(timezone.utc)
        dlq_entry = FederationNotificationDeadLetter(
            dlq_id="fdlq_replay6",
            notification_id="fn_replay6",
            approval_id="ap_replay6",
            channel="webhook",
            reason=FederationNotificationDLQReason.MAX_RETRIES_EXCEEDED,
            status=FederationNotificationDLQStatus.PENDING,
            failure_count=3,
            last_error="Timeout",
            payload={"key": "value"},
            metadata={"event_type": "approval.created"},
            created_at=now,
            updated_at=now,
        )
        await dlq_store.create(dlq_entry)

        fake_webhook_adapter = FakeFederationNotificationAdapter()
        svc = _make_service(
            policy=_make_policy(channels=[FederationNotificationChannel.WEBHOOK]),
            adapters={FederationNotificationChannel.WEBHOOK: fake_webhook_adapter},
        )

        result = await svc.replay_original("fdlq_replay6", dlq_store)
        assert result.replay_id.startswith("fwrp_")
