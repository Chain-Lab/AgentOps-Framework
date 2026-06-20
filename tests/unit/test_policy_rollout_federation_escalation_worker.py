"""Unit tests for FederationApprovalEscalationWorker."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalPolicy,
    FederationApprovalRequest,
    FederationApprovalStatus,
)
from agent_app.runtime.policy_rollout_federation_approval_service import (
    FederationApprovalService,
)
from agent_app.runtime.policy_rollout_federation_approval_store import (
    InMemoryFederationApprovalStore,
)
from agent_app.runtime.policy_rollout_federation_escalation_worker import (
    FederationApprovalEscalationWorker,
    FederationApprovalEscalationWorkerResult,
)
from agent_app.runtime.policy_rollout_federation_notification_service import (
    FederationNotificationService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_approval(
    approval_id: str = "fap_001",
    *,
    created_at: datetime | None = None,
    action: str = "federation.plan.start",
    federation_id: str = "fed-1",
    status: FederationApprovalStatus = FederationApprovalStatus.PENDING,
) -> FederationApprovalRequest:
    return FederationApprovalRequest(
        approval_id=approval_id,
        federation_id=federation_id,
        action=action,
        requested_by="req-1",
        required_approvers=["approver-1"],
        status=status,
        created_at=created_at or datetime.now(timezone.utc),
    )


def _make_worker(
    *,
    escalation_after_minutes: int = 60,
    dry_run: bool = False,
    notification_service: FederationNotificationService | None = None,
    distributed_lock: object | None = None,
) -> tuple[
    FederationApprovalEscalationWorker,
    InMemoryFederationApprovalStore,
    FederationApprovalService,
]:
    store = InMemoryFederationApprovalStore()
    policy = FederationApprovalPolicy(
        enabled=True,
        require_approval_for=["federation.plan.start"],
        default_required_approvers=["approver-1"],
        escalation_enabled=True,
        escalation_after_minutes=escalation_after_minutes,
        escalate_to=["escalation-admin"],
    )
    service = FederationApprovalService(
        approval_store=store,
        approval_policy=policy,
    )
    worker = FederationApprovalEscalationWorker(
        approval_store=store,
        approval_service=service,
        notification_service=notification_service,
        distributed_lock=distributed_lock,
        escalation_after_minutes=escalation_after_minutes,
        dry_run=dry_run,
    )
    return worker, store, service


# ---------------------------------------------------------------------------
# Tests — no pending approvals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_pending_approvals():
    """When there are no pending approvals, scanned/escalated are 0."""
    worker, store, _ = _make_worker()
    result = await worker.tick()
    assert result.scanned_count == 0
    assert result.escalated_count == 0
    assert result.skipped_count == 0
    assert result.errors == []


# ---------------------------------------------------------------------------
# Tests — pending approval before timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_approval_before_timeout_is_skipped():
    """A pending approval that hasn't timed out yet should be skipped."""
    now = datetime.now(timezone.utc)
    worker, store, _ = _make_worker(escalation_after_minutes=60)
    await store.create(_make_approval("fap_001", created_at=now - timedelta(minutes=30)))
    result = await worker.tick(now=now)
    assert result.scanned_count == 1
    assert result.escalated_count == 0
    assert result.skipped_count == 1


@pytest.mark.asyncio
async def test_pending_approval_exactly_at_timeout_is_escalated():
    """An approval whose timeout is exactly *now* should be escalated (<=)."""
    now = datetime.now(timezone.utc)
    worker, store, _ = _make_worker(escalation_after_minutes=60)
    created = now - timedelta(minutes=60)
    await store.create(_make_approval("fap_001", created_at=created))
    result = await worker.tick(now=now)
    assert result.scanned_count == 1
    assert result.escalated_count == 1
    assert result.skipped_count == 0


# ---------------------------------------------------------------------------
# Tests — pending approval after timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_approval_after_timeout_is_escalated():
    """A pending approval past the timeout should be escalated."""
    now = datetime.now(timezone.utc)
    worker, store, _ = _make_worker(escalation_after_minutes=60)
    created = now - timedelta(minutes=120)
    await store.create(_make_approval("fap_001", created_at=created))
    result = await worker.tick(now=now)
    assert result.scanned_count == 1
    assert result.escalated_count == 1
    assert result.skipped_count == 0
    # Verify the store now has the approval in ESCALATED status
    updated = await store.get("fap_001")
    assert updated is not None
    assert updated.status == FederationApprovalStatus.ESCALATED


# ---------------------------------------------------------------------------
# Tests — notification service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalation_creates_notification_when_service_provided():
    """When notification_service is available, enqueue_for_approval_escalated is called."""
    now = datetime.now(timezone.utc)
    notif_svc = AsyncMock(spec=FederationNotificationService)
    worker, store, _ = _make_worker(
        escalation_after_minutes=60,
        notification_service=notif_svc,
    )
    created = now - timedelta(minutes=120)
    await store.create(_make_approval("fap_001", created_at=created))
    result = await worker.tick(now=now)
    assert result.escalated_count == 1
    notif_svc.enqueue_for_approval_escalated.assert_called_once()
    call_kwargs = notif_svc.enqueue_for_approval_escalated.call_args.kwargs
    assert call_kwargs["approval_id"] == "fap_001"
    assert call_kwargs["escalated_by"] == "escalation_worker"


@pytest.mark.asyncio
async def test_escalation_without_notification_service_still_works():
    """Escalation works fine when no notification_service is provided."""
    now = datetime.now(timezone.utc)
    worker, store, _ = _make_worker(escalation_after_minutes=60)
    created = now - timedelta(minutes=120)
    await store.create(_make_approval("fap_001", created_at=created))
    result = await worker.tick(now=now)
    assert result.escalated_count == 1
    assert result.errors == []


@pytest.mark.asyncio
async def test_notification_failure_does_not_break_escalation():
    """If the notification service raises, escalation still succeeds."""
    now = datetime.now(timezone.utc)
    notif_svc = AsyncMock(spec=FederationNotificationService)
    notif_svc.enqueue_for_approval_escalated.side_effect = RuntimeError("notif down")
    worker, store, _ = _make_worker(
        escalation_after_minutes=60,
        notification_service=notif_svc,
    )
    created = now - timedelta(minutes=120)
    await store.create(_make_approval("fap_001", created_at=created))
    result = await worker.tick(now=now)
    assert result.escalated_count == 1
    assert result.errors == []  # notification failure is best-effort, not added to errors
    # The approval should still be escalated in the store
    updated = await store.get("fap_001")
    assert updated is not None
    assert updated.status == FederationApprovalStatus.ESCALATED


# ---------------------------------------------------------------------------
# Tests — dry_run mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_does_not_escalate():
    """In dry_run mode, approvals are scanned but not actually escalated."""
    now = datetime.now(timezone.utc)
    worker, store, _ = _make_worker(escalation_after_minutes=60, dry_run=True)
    created = now - timedelta(minutes=120)
    await store.create(_make_approval("fap_001", created_at=created))
    result = await worker.tick(now=now)
    assert result.scanned_count == 1
    assert result.escalated_count == 1  # still tracked as "would be escalated"
    assert result.skipped_count == 0
    # But the store should still show PENDING (not actually escalated)
    updated = await store.get("fap_001")
    assert updated is not None
    assert updated.status == FederationApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_dry_run_with_notification_service():
    """In dry_run mode, notification is still attempted (best-effort)."""
    now = datetime.now(timezone.utc)
    notif_svc = AsyncMock(spec=FederationNotificationService)
    worker, store, _ = _make_worker(
        escalation_after_minutes=60,
        dry_run=True,
        notification_service=notif_svc,
    )
    created = now - timedelta(minutes=120)
    await store.create(_make_approval("fap_001", created_at=created))
    result = await worker.tick(now=now)
    assert result.escalated_count == 1
    # Notification should still be called in dry_run
    notif_svc.enqueue_for_approval_escalated.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — distributed lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lock_acquired_worker_runs_normally():
    """When the lock is acquired, the worker runs normally."""
    now = datetime.now(timezone.utc)
    lock = AsyncMock()
    lock.acquire.return_value = True
    worker, store, _ = _make_worker(
        escalation_after_minutes=60,
        distributed_lock=lock,
    )
    created = now - timedelta(minutes=120)
    await store.create(_make_approval("fap_001", created_at=created))
    result = await worker.tick(now=now)
    assert result.escalated_count == 1
    lock.acquire.assert_called_once()
    lock.release.assert_called_once()


@pytest.mark.asyncio
async def test_lock_unavailable_returns_skip():
    """When the lock cannot be acquired, the worker returns skipped_count=1."""
    lock = AsyncMock()
    lock.acquire.return_value = False
    worker, _, _ = _make_worker(distributed_lock=lock)
    result = await worker.tick()
    assert result.scanned_count == 0
    assert result.skipped_count == 1
    assert "Lock unavailable" in result.errors
    lock.release.assert_not_called()


@pytest.mark.asyncio
async def test_lock_acquisition_error_returns_skip():
    """If lock.acquire raises an exception, the worker returns an error."""
    lock = AsyncMock()
    lock.acquire.side_effect = RuntimeError("lock service down")
    worker, _, _ = _make_worker(distributed_lock=lock)
    result = await worker.tick()
    assert result.skipped_count == 1
    assert any("Lock acquisition error" in e for e in result.errors)


@pytest.mark.asyncio
async def test_lock_released_after_tick():
    """Lock is released even if the tick processes approvals."""
    now = datetime.now(timezone.utc)
    lock = AsyncMock()
    lock.acquire.return_value = True
    worker, store, _ = _make_worker(
        escalation_after_minutes=60,
        distributed_lock=lock,
    )
    created = now - timedelta(minutes=120)
    await store.create(_make_approval("fap_001", created_at=created))
    await worker.tick(now=now)
    lock.release.assert_called_once()


@pytest.mark.asyncio
async def test_lock_released_on_exception():
    """Lock is released even if an unexpected exception occurs during processing."""
    now = datetime.now(timezone.utc)
    lock = AsyncMock()
    lock.acquire.return_value = True
    worker, store, svc = _make_worker(
        escalation_after_minutes=60,
        distributed_lock=lock,
    )
    # Force store.list to raise
    store.list = AsyncMock(side_effect=RuntimeError("db error"))
    # The exception propagates, but the lock should still be released
    with pytest.raises(RuntimeError, match="db error"):
        await worker.tick(now=now)
    # The lock should still be released via the finally block
    lock.release.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — multiple pending approvals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_approvals_some_due_some_not():
    """Mixed scenario: some approvals are due, some are not."""
    now = datetime.now(timezone.utc)
    worker, store, _ = _make_worker(escalation_after_minutes=60)
    # Due
    await store.create(_make_approval("fap_001", created_at=now - timedelta(minutes=120)))
    # Not due
    await store.create(_make_approval("fap_002", created_at=now - timedelta(minutes=30)))
    # Due
    await store.create(_make_approval("fap_003", created_at=now - timedelta(minutes=90)))
    result = await worker.tick(now=now)
    assert result.scanned_count == 3
    assert result.escalated_count == 2
    assert result.skipped_count == 1


@pytest.mark.asyncio
async def test_all_approvals_due():
    """All pending approvals are past the timeout."""
    now = datetime.now(timezone.utc)
    worker, store, _ = _make_worker(escalation_after_minutes=60)
    await store.create(_make_approval("fap_001", created_at=now - timedelta(minutes=61)))
    await store.create(_make_approval("fap_002", created_at=now - timedelta(minutes=62)))
    await store.create(_make_approval("fap_003", created_at=now - timedelta(minutes=63)))
    result = await worker.tick(now=now)
    assert result.scanned_count == 3
    assert result.escalated_count == 3
    assert result.skipped_count == 0


@pytest.mark.asyncio
async def test_all_approvals_not_due():
    """No pending approvals have timed out yet."""
    now = datetime.now(timezone.utc)
    worker, store, _ = _make_worker(escalation_after_minutes=60)
    await store.create(_make_approval("fap_001", created_at=now - timedelta(minutes=10)))
    await store.create(_make_approval("fap_002", created_at=now - timedelta(minutes=20)))
    result = await worker.tick(now=now)
    assert result.scanned_count == 2
    assert result.escalated_count == 0
    assert result.skipped_count == 2


# ---------------------------------------------------------------------------
# Tests — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_escalation_error_caught_and_recorded():
    """If service.escalate raises, the error is recorded but other approvals still processed."""
    now = datetime.now(timezone.utc)
    worker, store, svc = _make_worker(escalation_after_minutes=60)
    await store.create(_make_approval("fap_001", created_at=now - timedelta(minutes=120)))
    await store.create(_make_approval("fap_002", created_at=now - timedelta(minutes=120)))
    # Make escalate fail for the first approval only
    original_escalate = svc.escalate
    call_count = 0

    async def _flaky_escalate(approval_id, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("escalation service error")
        return await original_escalate(approval_id, **kwargs)

    svc.escalate = _flaky_escalate
    result = await worker.tick(now=now)
    assert result.scanned_count == 2
    assert result.escalated_count == 2  # both counted (one errored, one succeeded)
    assert len(result.errors) == 1
    assert "fap_001" in result.errors[0]
    # Second approval should still be escalated
    updated = await store.get("fap_002")
    assert updated is not None
    assert updated.status == FederationApprovalStatus.ESCALATED


# ---------------------------------------------------------------------------
# Tests — now parameter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_now_defaults_to_utc():
    """When now is not provided, it defaults to datetime.now(timezone.utc)."""
    worker, store, _ = _make_worker(escalation_after_minutes=60)
    # Create an approval that is definitely past timeout
    past = datetime.now(timezone.utc) - timedelta(minutes=120)
    await store.create(_make_approval("fap_001", created_at=past))
    result = await worker.tick()  # no now parameter
    assert result.escalated_count == 1


# ---------------------------------------------------------------------------
# Tests — non-pending approvals are not listed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_pending_approvals_are_ignored():
    """Only PENDING approvals are scanned; APPROVED/ESCALATED/etc. are ignored."""
    now = datetime.now(timezone.utc)
    worker, store, _ = _make_worker(escalation_after_minutes=60)
    created = now - timedelta(minutes=120)
    # Create a PENDING approval (will be listed)
    await store.create(_make_approval("fap_001", created_at=created, status=FederationApprovalStatus.PENDING))
    # Create an APPROVED approval (should NOT be listed)
    approved = _make_approval("fap_002", created_at=created, status=FederationApprovalStatus.APPROVED)
    approved.approvers_who_approved = ["approver-1"]
    approved.resolved_by = "approver-1"
    approved.resolved_at = now
    await store.create(approved)
    result = await worker.tick(now=now)
    assert result.scanned_count == 1  # only the PENDING one
    assert result.escalated_count == 1


# ---------------------------------------------------------------------------
# Tests — result model
# ---------------------------------------------------------------------------


def test_result_model_defaults():
    """FederationApprovalEscalationWorkerResult has correct defaults."""
    r = FederationApprovalEscalationWorkerResult()
    assert r.scanned_count == 0
    assert r.escalated_count == 0
    assert r.skipped_count == 0
    assert r.errors == []


def test_result_model_with_values():
    """FederationApprovalEscalationWorkerResult accepts values."""
    r = FederationApprovalEscalationWorkerResult(
        scanned_count=5,
        escalated_count=2,
        skipped_count=3,
        errors=["err1"],
    )
    assert r.scanned_count == 5
    assert r.escalated_count == 2
    assert r.skipped_count == 3
    assert r.errors == ["err1"]


# ---------------------------------------------------------------------------
# Tests — custom escalation_after_minutes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_escalation_timeout():
    """Worker respects a custom escalation_after_minutes value."""
    now = datetime.now(timezone.utc)
    # 30-minute timeout
    worker, store, _ = _make_worker(escalation_after_minutes=30)
    # Created 20 minutes ago — not yet due
    await store.create(_make_approval("fap_001", created_at=now - timedelta(minutes=20)))
    # Created 35 minutes ago — due
    await store.create(_make_approval("fap_002", created_at=now - timedelta(minutes=35)))
    result = await worker.tick(now=now)
    assert result.scanned_count == 2
    assert result.escalated_count == 1
    assert result.skipped_count == 1


# ---------------------------------------------------------------------------
# Tests — escalation_level in notification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notification_receives_correct_escalation_level():
    """Notification service receives the correct escalation_level after escalation."""
    now = datetime.now(timezone.utc)
    notif_svc = AsyncMock(spec=FederationNotificationService)
    worker, store, _ = _make_worker(
        escalation_after_minutes=60,
        notification_service=notif_svc,
    )
    created = now - timedelta(minutes=120)
    await store.create(_make_approval("fap_001", created_at=created))
    result = await worker.tick(now=now)
    assert result.escalated_count == 1
    call_kwargs = notif_svc.enqueue_for_approval_escalated.call_args.kwargs
    # After escalation, level should be 1 (was 0, incremented by 1)
    assert call_kwargs["escalation_level"] == 1


# ---------------------------------------------------------------------------
# Tests — lock release failure is best-effort
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lock_release_failure_does_not_break_result():
    """If lock.release raises, the worker still returns a valid result."""
    now = datetime.now(timezone.utc)
    lock = AsyncMock()
    lock.acquire.return_value = True
    lock.release.side_effect = RuntimeError("lock release failed")
    worker, store, _ = _make_worker(
        escalation_after_minutes=60,
        distributed_lock=lock,
    )
    created = now - timedelta(minutes=120)
    await store.create(_make_approval("fap_001", created_at=created))
    result = await worker.tick(now=now)
    # The escalation should still have succeeded
    assert result.escalated_count == 1
    assert result.errors == []  # lock release failure is best-effort, not added to errors
