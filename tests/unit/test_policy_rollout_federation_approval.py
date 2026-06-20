"""Tests for policy_rollout_federation_approval models."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalStatus,
    FederationApprovalRequest,
    FederationApprovalPolicy,
    FederationApprovalDecision,
    FederationApprovalEscalation,
    FederationApprovalDashboardSummary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    """Return a timezone-aware UTC datetime for use in required fields."""
    return datetime.now(timezone.utc)


# ===========================================================================
# FederationApprovalStatus
# ===========================================================================

class TestFederationApprovalStatus:
    """Tests for the FederationApprovalStatus enum."""

    def test_all_6_statuses_exist(self) -> None:
        expected = ["pending", "approved", "rejected", "expired", "escalated", "cancelled"]
        assert len(FederationApprovalStatus) == 6
        for value in expected:
            assert value in [e.value for e in FederationApprovalStatus]

    def test_specific_enum_values(self) -> None:
        assert FederationApprovalStatus.PENDING.value == "pending"
        assert FederationApprovalStatus.APPROVED.value == "approved"
        assert FederationApprovalStatus.REJECTED.value == "rejected"
        assert FederationApprovalStatus.EXPIRED.value == "expired"
        assert FederationApprovalStatus.ESCALATED.value == "escalated"
        assert FederationApprovalStatus.CANCELLED.value == "cancelled"

    def test_is_str_enum(self) -> None:
        assert isinstance(FederationApprovalStatus.PENDING, str)
        assert FederationApprovalStatus.PENDING == "pending"


# ===========================================================================
# FederationApprovalRequest
# ===========================================================================

class TestFederationApprovalRequest:
    """Tests for the FederationApprovalRequest model."""

    def test_valid_creation_minimal(self) -> None:
        req = FederationApprovalRequest(
            approval_id="fap_001",
            federation_id="frp_plan1",
            action="federation.plan.start",
            requested_by="actor-admin",
            created_at=_now(),
        )
        assert req.approval_id == "fap_001"
        assert req.federation_id == "frp_plan1"
        assert req.rollout_id is None
        assert req.target_id is None
        assert req.wave_id is None
        assert req.tenant_id is None
        assert req.environment is None
        assert req.region is None
        assert req.ring is None
        assert req.action == "federation.plan.start"
        assert req.requested_by == "actor-admin"
        assert req.required_approvers == []
        assert req.delegated_approvers == []
        assert req.approvers_who_approved == []
        assert req.approvers_who_rejected == []
        assert req.status == FederationApprovalStatus.PENDING
        assert req.reason is None
        assert req.rejection_reason is None
        assert req.escalation_level == 0
        assert req.escalation_reason is None
        assert req.resolved_at is None
        assert req.resolved_by is None
        assert req.expires_at is None
        assert req.metadata == {}

    def test_valid_creation_all_fields(self) -> None:
        now = _now()
        req = FederationApprovalRequest(
            approval_id="fap_abc123",
            federation_id="frp_plan1",
            rollout_id="rlo_roll1",
            target_id="frt_target1",
            wave_id="frw_wave1",
            tenant_id="tenant-42",
            environment="production",
            region="us-east-1",
            ring="ring-2",
            action="federation.wave.start",
            requested_by="actor-admin",
            required_approvers=["approver-1", "approver-2"],
            delegated_approvers=["delegate-1"],
            approvers_who_approved=["approver-1"],
            approvers_who_rejected=[],
            status=FederationApprovalStatus.ESCALATED,
            reason="Wave start requires approval",
            rejection_reason=None,
            escalation_level=1,
            escalation_reason="No response within timeout",
            created_at=now,
            resolved_at=None,
            resolved_by=None,
            expires_at=now + timedelta(hours=24),
            metadata={"priority": "high"},
        )
        assert req.rollout_id == "rlo_roll1"
        assert req.target_id == "frt_target1"
        assert req.wave_id == "frw_wave1"
        assert req.tenant_id == "tenant-42"
        assert req.environment == "production"
        assert req.region == "us-east-1"
        assert req.ring == "ring-2"
        assert req.action == "federation.wave.start"
        assert req.required_approvers == ["approver-1", "approver-2"]
        assert req.delegated_approvers == ["delegate-1"]
        assert req.approvers_who_approved == ["approver-1"]
        assert req.status == FederationApprovalStatus.ESCALATED
        assert req.reason == "Wave start requires approval"
        assert req.escalation_level == 1
        assert req.escalation_reason == "No response within timeout"
        assert req.expires_at is not None
        assert req.metadata == {"priority": "high"}

    def test_approval_id_must_start_with_fap_prefix(self) -> None:
        with pytest.raises(ValidationError, match="fap_"):
            FederationApprovalRequest(
                approval_id="bad_id",
                federation_id="frp_plan1",
                action="federation.plan.start",
                requested_by="actor-admin",
                created_at=_now(),
            )

    def test_created_at_must_be_timezone_aware(self) -> None:
        naive_dt = datetime(2026, 1, 1, 12, 0, 0)
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederationApprovalRequest(
                approval_id="fap_001",
                federation_id="frp_plan1",
                action="federation.plan.start",
                requested_by="actor-admin",
                created_at=naive_dt,
            )

    def test_status_defaults_to_pending(self) -> None:
        req = FederationApprovalRequest(
            approval_id="fap_001",
            federation_id="frp_plan1",
            action="federation.plan.start",
            requested_by="actor-admin",
            created_at=_now(),
        )
        assert req.status == FederationApprovalStatus.PENDING

    def test_status_transitions(self) -> None:
        now = _now()
        # Create with PENDING
        req = FederationApprovalRequest(
            approval_id="fap_001",
            federation_id="frp_plan1",
            action="federation.plan.start",
            requested_by="actor-admin",
            created_at=now,
        )
        assert req.status == FederationApprovalStatus.PENDING

        # Transition to APPROVED
        req.status = FederationApprovalStatus.APPROVED
        assert req.status == FederationApprovalStatus.APPROVED

        # Transition to REJECTED
        req.status = FederationApprovalStatus.REJECTED
        assert req.status == FederationApprovalStatus.REJECTED

        # Transition to ESCALATED
        req.status = FederationApprovalStatus.ESCALATED
        assert req.status == FederationApprovalStatus.ESCALATED

        # Transition to EXPIRED
        req.status = FederationApprovalStatus.EXPIRED
        assert req.status == FederationApprovalStatus.EXPIRED

        # Transition to CANCELLED
        req.status = FederationApprovalStatus.CANCELLED
        assert req.status == FederationApprovalStatus.CANCELLED

    def test_metadata_defaults_to_empty_dict_and_is_independent(self) -> None:
        req1 = FederationApprovalRequest(
            approval_id="fap_001",
            federation_id="frp_plan1",
            action="federation.plan.start",
            requested_by="actor-admin",
            created_at=_now(),
        )
        assert req1.metadata == {}
        req2 = FederationApprovalRequest(
            approval_id="fap_002",
            federation_id="frp_plan1",
            action="federation.plan.start",
            requested_by="actor-admin",
            created_at=_now(),
        )
        req1.metadata["x"] = 1
        assert "x" not in req2.metadata

    def test_list_fields_default_to_empty_and_are_independent(self) -> None:
        req1 = FederationApprovalRequest(
            approval_id="fap_001",
            federation_id="frp_plan1",
            action="federation.plan.start",
            requested_by="actor-admin",
            created_at=_now(),
        )
        assert req1.required_approvers == []
        assert req1.delegated_approvers == []
        assert req1.approvers_who_approved == []
        assert req1.approvers_who_rejected == []
        req2 = FederationApprovalRequest(
            approval_id="fap_002",
            federation_id="frp_plan1",
            action="federation.plan.start",
            requested_by="actor-admin",
            created_at=_now(),
        )
        req1.required_approvers.append("approver-1")
        assert req2.required_approvers == []


# ===========================================================================
# FederationApprovalPolicy
# ===========================================================================

class TestFederationApprovalPolicy:
    """Tests for the FederationApprovalPolicy model."""

    def test_defaults(self) -> None:
        policy = FederationApprovalPolicy()
        assert policy.enabled is False
        assert policy.require_approval_for == []
        assert policy.default_required_approvers == []
        assert policy.delegation_enabled is False
        assert policy.escalation_enabled is False
        assert policy.escalation_after_minutes == 60
        assert policy.escalate_to == []

    def test_with_values(self) -> None:
        policy = FederationApprovalPolicy(
            enabled=True,
            require_approval_for=["federation.plan.start", "federation.wave.start"],
            default_required_approvers=["admin-1", "admin-2"],
            delegation_enabled=True,
            escalation_enabled=True,
            escalation_after_minutes=30,
            escalate_to=["escalation-admin"],
        )
        assert policy.enabled is True
        assert len(policy.require_approval_for) == 2
        assert policy.default_required_approvers == ["admin-1", "admin-2"]
        assert policy.delegation_enabled is True
        assert policy.escalation_enabled is True
        assert policy.escalation_after_minutes == 30
        assert policy.escalate_to == ["escalation-admin"]

    def test_list_fields_are_independent(self) -> None:
        p1 = FederationApprovalPolicy()
        p2 = FederationApprovalPolicy()
        p1.require_approval_for.append("action-1")
        assert p2.require_approval_for == []


# ===========================================================================
# FederationApprovalDecision
# ===========================================================================

class TestFederationApprovalDecision:
    """Tests for the FederationApprovalDecision model."""

    def test_valid_approval_decision(self) -> None:
        decision = FederationApprovalDecision(
            approval_id="fap_001",
            actor_id="approver-1",
            decision=FederationApprovalStatus.APPROVED,
            reason="Looks good",
            created_at=_now(),
        )
        assert decision.approval_id == "fap_001"
        assert decision.actor_id == "approver-1"
        assert decision.decision == FederationApprovalStatus.APPROVED
        assert decision.reason == "Looks good"
        assert decision.is_delegated is False
        assert decision.delegated_by is None

    def test_valid_rejection_decision(self) -> None:
        decision = FederationApprovalDecision(
            approval_id="fap_001",
            actor_id="approver-2",
            decision=FederationApprovalStatus.REJECTED,
            reason="Risk too high",
            created_at=_now(),
        )
        assert decision.decision == FederationApprovalStatus.REJECTED
        assert decision.reason == "Risk too high"

    def test_delegated_decision(self) -> None:
        decision = FederationApprovalDecision(
            approval_id="fap_001",
            actor_id="delegate-1",
            decision=FederationApprovalStatus.APPROVED,
            is_delegated=True,
            delegated_by="approver-1",
            created_at=_now(),
        )
        assert decision.is_delegated is True
        assert decision.delegated_by == "approver-1"

    def test_created_at_must_be_timezone_aware(self) -> None:
        naive_dt = datetime(2026, 1, 1, 12, 0, 0)
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederationApprovalDecision(
                approval_id="fap_001",
                actor_id="approver-1",
                decision=FederationApprovalStatus.APPROVED,
                created_at=naive_dt,
            )

    def test_defaults(self) -> None:
        decision = FederationApprovalDecision(
            approval_id="fap_001",
            actor_id="approver-1",
            decision=FederationApprovalStatus.APPROVED,
            created_at=_now(),
        )
        assert decision.reason is None
        assert decision.is_delegated is False
        assert decision.delegated_by is None


# ===========================================================================
# FederationApprovalEscalation
# ===========================================================================

class TestFederationApprovalEscalation:
    """Tests for the FederationApprovalEscalation model."""

    def test_valid_creation(self) -> None:
        esc = FederationApprovalEscalation(
            approval_id="fap_001",
            from_level=0,
            to_level=1,
            escalated_by="system",
            reason="No response within timeout",
            new_required_approvers=["escalation-admin-1"],
            created_at=_now(),
        )
        assert esc.approval_id == "fap_001"
        assert esc.from_level == 0
        assert esc.to_level == 1
        assert esc.escalated_by == "system"
        assert esc.reason == "No response within timeout"
        assert esc.new_required_approvers == ["escalation-admin-1"]

    def test_minimal_creation(self) -> None:
        esc = FederationApprovalEscalation(
            approval_id="fap_001",
            from_level=0,
            to_level=1,
            created_at=_now(),
        )
        assert esc.escalated_by is None
        assert esc.reason is None
        assert esc.new_required_approvers == []

    def test_created_at_must_be_timezone_aware(self) -> None:
        naive_dt = datetime(2026, 1, 1, 12, 0, 0)
        with pytest.raises(ValidationError, match="timezone-aware"):
            FederationApprovalEscalation(
                approval_id="fap_001",
                from_level=0,
                to_level=1,
                created_at=naive_dt,
            )

    def test_new_required_approvers_default_independent(self) -> None:
        esc1 = FederationApprovalEscalation(
            approval_id="fap_001",
            from_level=0,
            to_level=1,
            created_at=_now(),
        )
        esc2 = FederationApprovalEscalation(
            approval_id="fap_002",
            from_level=1,
            to_level=2,
            created_at=_now(),
        )
        esc1.new_required_approvers.append("admin-1")
        assert esc2.new_required_approvers == []


# ===========================================================================
# FederationApprovalDashboardSummary
# ===========================================================================

class TestFederationApprovalDashboardSummary:
    """Tests for the FederationApprovalDashboardSummary model."""

    def test_defaults(self) -> None:
        summary = FederationApprovalDashboardSummary()
        assert summary.total_pending == 0
        assert summary.total_approved == 0
        assert summary.total_rejected == 0
        assert summary.total_expired == 0
        assert summary.total_escalated == 0
        assert summary.total_cancelled == 0
        assert summary.average_approval_latency_seconds is None
        assert summary.by_tenant == {}
        assert summary.by_action == {}
        assert summary.blocked_federation_actions == 0

    def test_with_values(self) -> None:
        summary = FederationApprovalDashboardSummary(
            total_pending=5,
            total_approved=20,
            total_rejected=3,
            total_expired=1,
            total_escalated=2,
            total_cancelled=0,
            average_approval_latency_seconds=120.5,
            by_tenant={"tenant-1": 3, "tenant-2": 2},
            by_action={"federation.plan.start": 4, "federation.wave.start": 1},
            blocked_federation_actions=5,
        )
        assert summary.total_pending == 5
        assert summary.total_approved == 20
        assert summary.total_rejected == 3
        assert summary.total_expired == 1
        assert summary.total_escalated == 2
        assert summary.average_approval_latency_seconds == 120.5
        assert summary.by_tenant == {"tenant-1": 3, "tenant-2": 2}
        assert summary.by_action == {"federation.plan.start": 4, "federation.wave.start": 1}
        assert summary.blocked_federation_actions == 5

    def test_dict_fields_are_independent(self) -> None:
        s1 = FederationApprovalDashboardSummary()
        s2 = FederationApprovalDashboardSummary()
        s1.by_tenant["t-1"] = 5
        assert s2.by_tenant == {}
