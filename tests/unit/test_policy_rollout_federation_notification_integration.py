"""Tests for FederationApprovalService notification integration — Phase 49 Task 8.

Verifies that the approval service enqueues notifications on lifecycle events
(create, approve, reject, escalate) and that notification failures never break
the approval state transitions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalPolicy,
    FederationApprovalStatus,
)
from agent_app.governance.policy_rollout_federation_notification import (
    FederationNotificationChannel,
    FederationNotificationEventType,
    FederationNotificationPolicy,
)
from agent_app.runtime.policy_rollout_federation_approval_service import (
    FederationApprovalService,
)
from agent_app.runtime.policy_rollout_federation_approval_store import (
    InMemoryFederationApprovalStore,
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


def _make_approval_policy(
    enabled: bool = True,
    require_approval_for: list[str] | None = None,
    default_required_approvers: list[str] | None = None,
    escalation_enabled: bool = False,
    escalation_after_minutes: int = 60,
    escalate_to: list[str] | None = None,
    delegation_enabled: bool = False,
) -> FederationApprovalPolicy:
    return FederationApprovalPolicy(
        enabled=enabled,
        require_approval_for=require_approval_for or [
            "federation.plan.start",
            "federation.plan.run_next",
        ],
        default_required_approvers=default_required_approvers or ["approver-1", "approver-2"],
        escalation_enabled=escalation_enabled,
        escalation_after_minutes=escalation_after_minutes,
        escalate_to=escalate_to or ["escalation-approver-1"],
        delegation_enabled=delegation_enabled,
    )


def _make_notification_policy(
    enabled: bool = True,
    channels: list[FederationNotificationChannel] | None = None,
) -> FederationNotificationPolicy:
    return FederationNotificationPolicy(
        enabled=enabled,
        default_channels=channels or [FederationNotificationChannel.CONSOLE],
        max_attempts=3,
        backoff_seconds=60,
    )


def _make_notification_service(
    policy: FederationNotificationPolicy | None = None,
) -> tuple[FederationNotificationService, InMemoryFederationNotificationStore, FakeFederationNotificationAdapter]:
    notif_policy = policy or _make_notification_policy()
    notif_store = InMemoryFederationNotificationStore()
    fake_adapter = FakeFederationNotificationAdapter()
    svc = FederationNotificationService(
        notification_store=notif_store,
        adapters={FederationNotificationChannel.CONSOLE: fake_adapter},
        notification_policy=notif_policy,
    )
    return svc, notif_store, fake_adapter


def _make_approval_service(
    approval_policy: FederationApprovalPolicy | None = None,
    notification_service: Any | None = None,
) -> tuple[FederationApprovalService, InMemoryFederationApprovalStore]:
    store = InMemoryFederationApprovalStore()
    svc = FederationApprovalService(
        approval_store=store,
        approval_policy=approval_policy or _make_approval_policy(),
        notification_service=notification_service,
    )
    return svc, store


# ---------------------------------------------------------------------------
# Tests: Approval created -> notification enqueued
# ---------------------------------------------------------------------------


class TestApprovalCreatedNotification:
    """Approval creation enqueues notification when notification_service is provided."""

    @pytest.mark.asyncio
    async def test_create_enqueues_notification(self) -> None:
        notif_svc, notif_store, _ = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )

        # Notification should be in the store
        notifications = await notif_store.list_by_approval(request.approval_id)
        assert len(notifications) == 1
        assert notifications[0].event_type == FederationNotificationEventType.APPROVAL_CREATED
        assert notifications[0].approval_id == request.approval_id

    @pytest.mark.asyncio
    async def test_create_notification_contains_correct_fields(self) -> None:
        notif_svc, notif_store, _ = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )

        notifications = await notif_store.list_by_approval(request.approval_id)
        assert len(notifications) == 1
        notif = notifications[0]
        assert notif.federation_id == "fed-1"
        assert "federation.plan.start" in notif.body
        assert "user-1" in notif.body

    @pytest.mark.asyncio
    async def test_create_enqueues_one_notification_per_channel(self) -> None:
        notif_policy = _make_notification_policy(
            channels=[FederationNotificationChannel.CONSOLE, FederationNotificationChannel.NOOP],
        )
        notif_svc, notif_store, _ = _make_notification_service(policy=notif_policy)
        # Need adapter for NOOP channel too
        notif_svc._adapters[FederationNotificationChannel.NOOP] = FakeFederationNotificationAdapter()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )

        notifications = await notif_store.list_by_approval(request.approval_id)
        assert len(notifications) == 2
        channels = {n.channel for n in notifications}
        assert FederationNotificationChannel.CONSOLE in channels
        assert FederationNotificationChannel.NOOP in channels


# ---------------------------------------------------------------------------
# Tests: Approval approved -> notification enqueued
# ---------------------------------------------------------------------------


class TestApprovalApprovedNotification:
    """Approval approval enqueues notification."""

    @pytest.mark.asyncio
    async def test_approve_enqueues_notification(self) -> None:
        notif_svc, notif_store, _ = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await approval_svc.approve(request.approval_id, "approver-1")

        notifications = await notif_store.list_by_approval(request.approval_id)
        # One for created + one for approved
        assert len(notifications) == 2
        approved_notifs = [n for n in notifications if n.event_type == FederationNotificationEventType.APPROVAL_APPROVED]
        assert len(approved_notifs) == 1
        assert approved_notifs[0].approval_id == request.approval_id

    @pytest.mark.asyncio
    async def test_approve_notification_contains_approved_by(self) -> None:
        notif_svc, notif_store, _ = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await approval_svc.approve(request.approval_id, "approver-1")

        notifications = await notif_store.list_by_approval(request.approval_id)
        approved_notifs = [n for n in notifications if n.event_type == FederationNotificationEventType.APPROVAL_APPROVED]
        assert len(approved_notifs) == 1
        assert "approver-1" in approved_notifs[0].body


# ---------------------------------------------------------------------------
# Tests: Approval rejected -> notification enqueued
# ---------------------------------------------------------------------------


class TestApprovalRejectedNotification:
    """Approval rejection enqueues notification."""

    @pytest.mark.asyncio
    async def test_reject_enqueues_notification(self) -> None:
        notif_svc, notif_store, _ = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await approval_svc.reject(request.approval_id, "approver-1", reason="Too risky")

        notifications = await notif_store.list_by_approval(request.approval_id)
        # One for created + one for rejected
        assert len(notifications) == 2
        rejected_notifs = [n for n in notifications if n.event_type == FederationNotificationEventType.APPROVAL_REJECTED]
        assert len(rejected_notifs) == 1
        assert rejected_notifs[0].approval_id == request.approval_id

    @pytest.mark.asyncio
    async def test_reject_notification_contains_rejected_by(self) -> None:
        notif_svc, notif_store, _ = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await approval_svc.reject(request.approval_id, "approver-2")

        notifications = await notif_store.list_by_approval(request.approval_id)
        rejected_notifs = [n for n in notifications if n.event_type == FederationNotificationEventType.APPROVAL_REJECTED]
        assert len(rejected_notifs) == 1
        assert "approver-2" in rejected_notifs[0].body


# ---------------------------------------------------------------------------
# Tests: Approval escalated -> notification enqueued
# ---------------------------------------------------------------------------


class TestApprovalEscalatedNotification:
    """Approval escalation enqueues notification."""

    @pytest.mark.asyncio
    async def test_escalate_enqueues_notification(self) -> None:
        notif_svc, notif_store, _ = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await approval_svc.escalate(request.approval_id, escalated_by="admin", reason="Taking too long")

        notifications = await notif_store.list_by_approval(request.approval_id)
        # One for created + one for escalated
        assert len(notifications) == 2
        escalated_notifs = [n for n in notifications if n.event_type == FederationNotificationEventType.APPROVAL_ESCALATED]
        assert len(escalated_notifs) == 1
        assert escalated_notifs[0].approval_id == request.approval_id

    @pytest.mark.asyncio
    async def test_escalate_notification_contains_escalation_level(self) -> None:
        notif_svc, notif_store, _ = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await approval_svc.escalate(request.approval_id, escalated_by="admin")

        notifications = await notif_store.list_by_approval(request.approval_id)
        escalated_notifs = [n for n in notifications if n.event_type == FederationNotificationEventType.APPROVAL_ESCALATED]
        assert len(escalated_notifs) == 1
        assert "Level 1" in escalated_notifs[0].subject


# ---------------------------------------------------------------------------
# Tests: Notification service None -> no errors, approval still works
# ---------------------------------------------------------------------------


class TestNotificationServiceNone:
    """When notification_service is None, approval operations work normally."""

    @pytest.mark.asyncio
    async def test_create_without_notification_service(self) -> None:
        approval_svc, _ = _make_approval_service(notification_service=None)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        assert request.status == FederationApprovalStatus.PENDING
        assert request.approval_id.startswith("fap_")

    @pytest.mark.asyncio
    async def test_approve_without_notification_service(self) -> None:
        approval_svc, _ = _make_approval_service(notification_service=None)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        result = await approval_svc.approve(request.approval_id, "approver-1")
        assert result.status == FederationApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_reject_without_notification_service(self) -> None:
        approval_svc, _ = _make_approval_service(notification_service=None)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        result = await approval_svc.reject(request.approval_id, "approver-1")
        assert result.status == FederationApprovalStatus.REJECTED

    @pytest.mark.asyncio
    async def test_escalate_without_notification_service(self) -> None:
        approval_svc, _ = _make_approval_service(notification_service=None)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        result = await approval_svc.escalate(request.approval_id, escalated_by="admin")
        assert result.status == FederationApprovalStatus.ESCALATED
        assert result.escalation_level == 1


# ---------------------------------------------------------------------------
# Tests: Notification service throws -> approval still succeeds
# ---------------------------------------------------------------------------


class _BrokenNotificationService:
    """A notification service that always raises on every enqueue method."""

    async def enqueue_for_approval_created(self, **kwargs: Any) -> None:
        raise RuntimeError("Notification service is broken")

    async def enqueue_for_approval_approved(self, **kwargs: Any) -> None:
        raise RuntimeError("Notification service is broken")

    async def enqueue_for_approval_rejected(self, **kwargs: Any) -> None:
        raise RuntimeError("Notification service is broken")

    async def enqueue_for_approval_escalated(self, **kwargs: Any) -> None:
        raise RuntimeError("Notification service is broken")


class TestNotificationServiceFailure:
    """Notification service failure must never break approval state transitions."""

    @pytest.mark.asyncio
    async def test_create_succeeds_when_notification_raises(self) -> None:
        broken = _BrokenNotificationService()
        approval_svc, _ = _make_approval_service(notification_service=broken)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        assert request.status == FederationApprovalStatus.PENDING
        assert request.approval_id.startswith("fap_")

    @pytest.mark.asyncio
    async def test_approve_succeeds_when_notification_raises(self) -> None:
        broken = _BrokenNotificationService()
        approval_svc, _ = _make_approval_service(notification_service=broken)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        result = await approval_svc.approve(request.approval_id, "approver-1")
        assert result.status == FederationApprovalStatus.APPROVED
        assert "approver-1" in result.approvers_who_approved

    @pytest.mark.asyncio
    async def test_reject_succeeds_when_notification_raises(self) -> None:
        broken = _BrokenNotificationService()
        approval_svc, _ = _make_approval_service(notification_service=broken)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        result = await approval_svc.reject(request.approval_id, "approver-1", reason="No way")
        assert result.status == FederationApprovalStatus.REJECTED
        assert "approver-1" in result.approvers_who_rejected

    @pytest.mark.asyncio
    async def test_escalate_succeeds_when_notification_raises(self) -> None:
        broken = _BrokenNotificationService()
        approval_svc, _ = _make_approval_service(notification_service=broken)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        result = await approval_svc.escalate(request.approval_id, escalated_by="admin")
        assert result.status == FederationApprovalStatus.ESCALATED
        assert result.escalation_level == 1


# ---------------------------------------------------------------------------
# Tests: End-to-end lifecycle
# ---------------------------------------------------------------------------


class TestEndToEndNotificationLifecycle:
    """End-to-end: create approval -> approve -> verify both notifications in store."""

    @pytest.mark.asyncio
    async def test_create_then_approve_notifications(self) -> None:
        notif_svc, notif_store, _ = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await approval_svc.approve(request.approval_id, "approver-1")

        notifications = await notif_store.list_by_approval(request.approval_id)
        assert len(notifications) == 2
        event_types = [n.event_type for n in notifications]
        assert FederationNotificationEventType.APPROVAL_CREATED in event_types
        assert FederationNotificationEventType.APPROVAL_APPROVED in event_types

    @pytest.mark.asyncio
    async def test_create_then_reject_notifications(self) -> None:
        notif_svc, notif_store, _ = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await approval_svc.reject(request.approval_id, "approver-1")

        notifications = await notif_store.list_by_approval(request.approval_id)
        assert len(notifications) == 2
        event_types = [n.event_type for n in notifications]
        assert FederationNotificationEventType.APPROVAL_CREATED in event_types
        assert FederationNotificationEventType.APPROVAL_REJECTED in event_types

    @pytest.mark.asyncio
    async def test_create_then_escalate_then_approve_notifications(self) -> None:
        notif_svc, notif_store, _ = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await approval_svc.escalate(request.approval_id, escalated_by="admin")
        await approval_svc.approve(request.approval_id, "escalation-approver-1")

        notifications = await notif_store.list_by_approval(request.approval_id)
        assert len(notifications) == 3
        event_types = [n.event_type for n in notifications]
        assert FederationNotificationEventType.APPROVAL_CREATED in event_types
        assert FederationNotificationEventType.APPROVAL_ESCALATED in event_types
        assert FederationNotificationEventType.APPROVAL_APPROVED in event_types

    @pytest.mark.asyncio
    async def test_multiple_approvals_generate_separate_notifications(self) -> None:
        notif_svc, notif_store, _ = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        r1 = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        r2 = await approval_svc.create_approval_request(
            federation_id="fed-2",
            action="federation.plan.run_next",
            requested_by="user-2",
        )

        n1 = await notif_store.list_by_approval(r1.approval_id)
        n2 = await notif_store.list_by_approval(r2.approval_id)
        assert len(n1) == 1
        assert len(n2) == 1
        assert n1[0].approval_id != n2[0].approval_id

    @pytest.mark.asyncio
    async def test_notification_store_reflects_all_lifecycle_events(self) -> None:
        notif_svc, notif_store, _ = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        # Create two approvals with different outcomes
        r1 = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await approval_svc.approve(r1.approval_id, "approver-1")

        r2 = await approval_svc.create_approval_request(
            federation_id="fed-2",
            action="federation.plan.run_next",
            requested_by="user-2",
        )
        await approval_svc.escalate(r2.approval_id, escalated_by="admin")
        await approval_svc.reject(r2.approval_id, "escalation-approver-1")

        # Verify total notifications in store
        all_pending = await notif_store.list_pending(limit=100)
        # r1: created + approved = 2; r2: created + escalated + rejected = 3; total = 5
        assert len(all_pending) == 5

    @pytest.mark.asyncio
    async def test_dispatch_delivers_all_pending_notifications(self) -> None:
        notif_svc, notif_store, fake_adapter = _make_notification_service()
        approval_svc, _ = _make_approval_service(notification_service=notif_svc)

        request = await approval_svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await approval_svc.approve(request.approval_id, "approver-1")

        # Dispatch all pending notifications
        result = await notif_svc.dispatch_pending()
        assert result.total_dispatched == 2
        assert result.total_sent == 2
        assert result.total_failed == 0

        # Verify adapter received them
        assert len(fake_adapter.sent) == 2
