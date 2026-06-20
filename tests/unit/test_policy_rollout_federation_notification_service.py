"""Tests for FederationNotificationService — enqueue and dispatch federation approval notifications.

Phase 49 Task 4.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationChannel,
    FederationNotificationDelivery,
    FederationNotificationDispatchResult,
    FederationNotificationEventType,
    FederationNotificationMessage,
    FederationNotificationPolicy,
    FederationNotificationStatus,
)
from agent_app.runtime.policy_rollout_federation_notification_adapters import (
    FakeFederationNotificationAdapter,
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
