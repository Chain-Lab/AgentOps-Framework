"""Tests for FederationApprovalService — Phase 48 Task 3."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalPolicy,
    FederationApprovalRequest,
    FederationApprovalStatus,
)
from agent_app.governance.policy_rollout_federation_history import FederationHistoryEventType
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.runtime.policy_rollout_federation_approval_store import InMemoryFederationApprovalStore
from agent_app.runtime.policy_rollout_federation_approval_service import FederationApprovalService
from agent_app.runtime.policy_rollout_federation_history_recorder import FederationHistoryRecorder
from agent_app.runtime.policy_rollout_federation_history_store import InMemoryFederationHistoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_policy(
    enabled: bool = True,
    require_approval_for: list[str] | None = None,
    default_required_approvers: list[str] | None = None,
    delegation_enabled: bool = False,
    escalation_enabled: bool = False,
    escalation_after_minutes: int = 60,
    escalate_to: list[str] | None = None,
) -> FederationApprovalPolicy:
    return FederationApprovalPolicy(
        enabled=enabled,
        require_approval_for=require_approval_for or [
            "federation.plan.start",
            "federation.plan.run_next",
            "federation.plan.run_all",
            "federation.plan.cancel",
            "federation.target.disable",
            "federation.override_conflicts",
        ],
        default_required_approvers=default_required_approvers or ["approver-1", "approver-2"],
        delegation_enabled=delegation_enabled,
        escalation_enabled=escalation_enabled,
        escalation_after_minutes=escalation_after_minutes,
        escalate_to=escalate_to or ["escalation-approver-1"],
    )


def _make_service(
    policy: FederationApprovalPolicy | None = None,
    audit_logger: InMemoryAuditLogger | None = None,
    history_recorder: FederationHistoryRecorder | None = None,
) -> tuple[FederationApprovalService, InMemoryFederationApprovalStore]:
    store = InMemoryFederationApprovalStore()
    svc = FederationApprovalService(
        approval_store=store,
        approval_policy=policy or _make_policy(),
        audit_logger=audit_logger,
        federation_history_recorder=history_recorder,
    )
    return svc, store


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRequiresApproval:
    """1. Action not requiring approval proceeds (is_action_approved returns True)."""

    @pytest.mark.asyncio
    async def test_action_not_in_list_returns_false(self) -> None:
        svc, _ = _make_service()
        assert await svc.requires_approval("federation.some_other_action") is False

    @pytest.mark.asyncio
    async def test_action_in_list_returns_true(self) -> None:
        svc, _ = _make_service()
        assert await svc.requires_approval("federation.plan.start") is True

    @pytest.mark.asyncio
    async def test_policy_disabled_returns_false(self) -> None:
        svc, _ = _make_service(policy=_make_policy(enabled=False))
        assert await svc.requires_approval("federation.plan.start") is False

    @pytest.mark.asyncio
    async def test_is_action_approved_when_not_required(self) -> None:
        svc, _ = _make_service()
        # Action not in require_approval_for should be auto-approved
        assert await svc.is_action_approved("fed-1", "federation.some_other_action") is True


class TestCreateApprovalRequest:
    """2. Action requiring approval creates request."""

    @pytest.mark.asyncio
    async def test_create_request(self) -> None:
        svc, store = _make_service()
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        assert request.approval_id.startswith("fap_")
        assert request.federation_id == "fed-1"
        assert request.action == "federation.plan.start"
        assert request.requested_by == "user-1"
        assert request.status == FederationApprovalStatus.PENDING
        assert request.required_approvers == ["approver-1", "approver-2"]

    @pytest.mark.asyncio
    async def test_create_request_with_all_fields(self) -> None:
        svc, _ = _make_service()
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
            rollout_id="ro-1",
            target_id="tgt-1",
            wave_id="w-1",
            tenant_id="tenant-1",
            environment="prod",
            region="us-east-1",
            ring="ring-1",
            reason="Deploy v2",
            metadata={"priority": "high"},
        )
        assert request.rollout_id == "ro-1"
        assert request.target_id == "tgt-1"
        assert request.wave_id == "w-1"
        assert request.tenant_id == "tenant-1"
        assert request.environment == "prod"
        assert request.region == "us-east-1"
        assert request.ring == "ring-1"
        assert request.reason == "Deploy v2"
        assert request.metadata == {"priority": "high"}

    @pytest.mark.asyncio
    async def test_create_request_sets_expires_at_when_escalation_enabled(self) -> None:
        svc, _ = _make_service(policy=_make_policy(escalation_enabled=True, escalation_after_minutes=30))
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        assert request.expires_at is not None
        # Should be roughly 30 minutes from now
        delta = request.expires_at - request.created_at
        assert 1700 < delta.total_seconds() < 1900

    @pytest.mark.asyncio
    async def test_create_request_no_expires_at_when_escalation_disabled(self) -> None:
        svc, _ = _make_service(policy=_make_policy(escalation_enabled=False))
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        assert request.expires_at is None


class TestApprove:
    """3. Approved request allows execution (is_action_approved returns True)."""

    @pytest.mark.asyncio
    async def test_approve_by_required_approver(self) -> None:
        svc, _ = _make_service()
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        result = await svc.approve(request.approval_id, "approver-1")
        assert result.status == FederationApprovalStatus.APPROVED
        assert "approver-1" in result.approvers_who_approved

    @pytest.mark.asyncio
    async def test_approved_request_is_action_approved(self) -> None:
        svc, _ = _make_service()
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await svc.approve(request.approval_id, "approver-1")
        assert await svc.is_action_approved("fed-1", "federation.plan.start") is True


class TestReject:
    """4. Rejected request blocks execution (is_action_approved returns False)."""

    @pytest.mark.asyncio
    async def test_reject_by_required_approver(self) -> None:
        svc, _ = _make_service()
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        result = await svc.reject(request.approval_id, "approver-1", reason="Too risky")
        assert result.status == FederationApprovalStatus.REJECTED
        assert "approver-1" in result.approvers_who_rejected

    @pytest.mark.asyncio
    async def test_rejected_request_blocks_action(self) -> None:
        svc, _ = _make_service()
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await svc.reject(request.approval_id, "approver-1")
        assert await svc.is_action_approved("fed-1", "federation.plan.start") is False


class TestDelegatedApprover:
    """5. Delegated approver can approve."""

    @pytest.mark.asyncio
    async def test_delegated_approver_can_approve(self) -> None:
        svc, _ = _make_service(policy=_make_policy(delegation_enabled=True))
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        # Delegate from approver-1 to delegate-1
        await svc.delegate_approval(
            request.approval_id,
            delegated_by="approver-1",
            delegated_to="delegate-1",
        )
        # Now delegate-1 should be able to approve
        result = await svc.approve(request.approval_id, "delegate-1")
        assert result.status == FederationApprovalStatus.APPROVED
        assert "delegate-1" in result.approvers_who_approved


class TestUnauthorizedApprover:
    """6. Unauthorized actor cannot approve (PermissionError)."""

    @pytest.mark.asyncio
    async def test_unauthorized_actor_raises_permission_error(self) -> None:
        svc, _ = _make_service()
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        with pytest.raises(PermissionError):
            await svc.approve(request.approval_id, "random-person")

    @pytest.mark.asyncio
    async def test_unauthorized_actor_reject_raises_permission_error(self) -> None:
        svc, _ = _make_service()
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        with pytest.raises(PermissionError):
            await svc.reject(request.approval_id, "random-person")


class TestEscalation:
    """7. Escalation works."""

    @pytest.mark.asyncio
    async def test_escalate_pending_request(self) -> None:
        svc, _ = _make_service()
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        result = await svc.escalate(request.approval_id, escalated_by="admin", reason="Taking too long")
        assert result.status == FederationApprovalStatus.ESCALATED
        assert result.escalation_level == 1
        assert "escalation-approver-1" in result.required_approvers

    @pytest.mark.asyncio
    async def test_escalated_request_can_be_approved(self) -> None:
        svc, _ = _make_service()
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await svc.escalate(request.approval_id, escalated_by="admin")
        # Escalation approver should be able to approve
        result = await svc.approve(request.approval_id, "escalation-approver-1")
        assert result.status == FederationApprovalStatus.APPROVED


class TestFederationHistoryEvent:
    """8. Federation history event recorded on create."""

    @pytest.mark.asyncio
    async def test_create_records_history_event(self) -> None:
        history_store = InMemoryFederationHistoryStore()
        recorder = FederationHistoryRecorder(history_store=history_store)
        svc, _ = _make_service(history_recorder=recorder)

        await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )

        events = await history_store.list(federation_id="fed-1")
        approval_events = [e for e in events if e.event_type == FederationHistoryEventType.APPROVAL_CREATED]
        assert len(approval_events) == 1
        assert approval_events[0].federation_id == "fed-1"


class TestAuditEvent:
    """9. Audit event recorded on approve."""

    @pytest.mark.asyncio
    async def test_approve_records_audit_event(self) -> None:
        audit_logger = InMemoryAuditLogger()
        svc, _ = _make_service(audit_logger=audit_logger)

        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await svc.approve(request.approval_id, "approver-1")

        events = audit_logger.list_events(event_type="policy.federation.approval.approved")
        assert len(events) == 1
        assert events[0].data["approval_id"] == request.approval_id

    @pytest.mark.asyncio
    async def test_create_records_audit_event(self) -> None:
        audit_logger = InMemoryAuditLogger()
        svc, _ = _make_service(audit_logger=audit_logger)

        await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )

        events = audit_logger.list_events(event_type="policy.federation.approval.created")
        assert len(events) == 1


class TestCheckApprovalStatus:
    """10. Check approval status returns latest request."""

    @pytest.mark.asyncio
    async def test_returns_latest_request(self) -> None:
        svc, _ = _make_service()

        r1 = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        r2 = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-2",
        )

        latest = await svc.check_approval_status("fed-1", "federation.plan.start")
        assert latest is not None
        assert latest.approval_id == r2.approval_id

    @pytest.mark.asyncio
    async def test_returns_none_when_no_request(self) -> None:
        svc, _ = _make_service()
        result = await svc.check_approval_status("fed-1", "federation.plan.start")
        assert result is None


class TestDelegateApproval:
    """11. Delegate approval works when enabled; 12. fails when disabled."""

    @pytest.mark.asyncio
    async def test_delegate_approval_enabled(self) -> None:
        svc, _ = _make_service(policy=_make_policy(delegation_enabled=True))
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        result = await svc.delegate_approval(
            request.approval_id,
            delegated_by="approver-1",
            delegated_to="delegate-1",
            reason="Out of office",
        )
        assert "delegate-1" in result.delegated_approvers

    @pytest.mark.asyncio
    async def test_delegate_approval_disabled(self) -> None:
        svc, _ = _make_service(policy=_make_policy(delegation_enabled=False))
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        with pytest.raises(ValueError, match="Delegation is not enabled"):
            await svc.delegate_approval(
                request.approval_id,
                delegated_by="approver-1",
                delegated_to="delegate-1",
            )

    @pytest.mark.asyncio
    async def test_delegate_by_non_approver_raises_permission_error(self) -> None:
        svc, _ = _make_service(policy=_make_policy(delegation_enabled=True))
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        with pytest.raises(PermissionError):
            await svc.delegate_approval(
                request.approval_id,
                delegated_by="non-approver",
                delegated_to="delegate-1",
            )


class TestCancel:
    """13. Cancel works."""

    @pytest.mark.asyncio
    async def test_cancel_pending_request(self) -> None:
        audit_logger = InMemoryAuditLogger()
        history_store = InMemoryFederationHistoryStore()
        recorder = FederationHistoryRecorder(history_store=history_store)
        svc, _ = _make_service(audit_logger=audit_logger, history_recorder=recorder)

        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        result = await svc.cancel(request.approval_id, cancelled_by="user-1", reason="No longer needed")
        assert result.status == FederationApprovalStatus.CANCELLED
        assert result.resolved_by == "user-1"

        # Audit event
        events = audit_logger.list_events(event_type="policy.federation.approval.cancelled")
        assert len(events) == 1

        # History event
        history_events = await history_store.list(federation_id="fed-1")
        cancel_events = [e for e in history_events if e.event_type == FederationHistoryEventType.APPROVAL_CANCELLED]
        assert len(cancel_events) == 1


class TestIsActionApproved:
    """Integration test for is_action_approved flow."""

    @pytest.mark.asyncio
    async def test_pending_request_not_approved(self) -> None:
        svc, _ = _make_service()
        await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        # Pending request means not yet approved
        assert await svc.is_action_approved("fed-1", "federation.plan.start") is False

    @pytest.mark.asyncio
    async def test_no_request_means_not_approved(self) -> None:
        svc, _ = _make_service()
        # Action requires approval but no request created
        assert await svc.is_action_approved("fed-1", "federation.plan.start") is False

    @pytest.mark.asyncio
    async def test_escalated_then_approved(self) -> None:
        svc, _ = _make_service()
        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        await svc.escalate(request.approval_id)
        await svc.approve(request.approval_id, "escalation-approver-1")
        assert await svc.is_action_approved("fed-1", "federation.plan.start") is True


class TestPermissionDeniedAudit:
    """Verify permission_denied audit events."""

    @pytest.mark.asyncio
    async def test_permission_denied_audit_on_unauthorized_approve(self) -> None:
        audit_logger = InMemoryAuditLogger()
        svc, _ = _make_service(audit_logger=audit_logger)

        request = await svc.create_approval_request(
            federation_id="fed-1",
            action="federation.plan.start",
            requested_by="user-1",
        )
        with pytest.raises(PermissionError):
            await svc.approve(request.approval_id, "random-person")

        events = audit_logger.list_events(event_type="policy.federation.approval.permission_denied")
        assert len(events) == 1
        assert events[0].data["actor_id"] == "random-person"
