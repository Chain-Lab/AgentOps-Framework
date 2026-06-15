"""Tests for RolloutApprovalPolicy, RolloutApprovalDecision, and extended RolloutStepApproval models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout_approval import (
    RolloutApprovalDecision,
    RolloutApprovalDecisionType,
    RolloutApprovalPolicy,
    RolloutApprovalPolicyType,
    RolloutStepApproval,
    RolloutStepApprovalStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_approval(**overrides) -> RolloutStepApproval:
    """Helper to build a RolloutStepApproval with sensible defaults."""
    now = datetime.now(timezone.utc)
    base = dict(
        approval_id="rsa_abc123",
        rollout_id="ro_xyz789",
        step_id="step_1",
        bundle_id="pb_bundle1",
        environment="production",
        ring_name="canary",
        requested_by="user_admin",
        requested_reason="Canary ring promotion requires approval",
        created_at=now,
    )
    base.update(overrides)
    return RolloutStepApproval(**base)


def _make_decision(**overrides) -> RolloutApprovalDecision:
    """Helper to build a RolloutApprovalDecision with sensible defaults."""
    now = datetime.now(timezone.utc)
    base = dict(
        decision_id="rsd_001",
        approval_id="rsa_abc123",
        decision_type=RolloutApprovalDecisionType.APPROVE,
        decided_by="user_reviewer",
        reason="Looks good",
        roles=["approver"],
        permissions=["rollout:approve"],
        created_at=now,
    )
    base.update(overrides)
    return RolloutApprovalDecision(**base)


# ---------------------------------------------------------------------------
# TestRolloutApprovalPolicyModel
# ---------------------------------------------------------------------------


class TestRolloutApprovalPolicyModel:
    """Tests for the RolloutApprovalPolicy model."""

    def test_default_single_policy(self):
        """Default policy is SINGLE with required_approvals=1."""
        policy = RolloutApprovalPolicy()
        assert policy.policy_type == RolloutApprovalPolicyType.SINGLE
        assert policy.required_approvals == 1

    def test_quorum_policy(self):
        """QUORUM policy with required_approvals=2."""
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        assert policy.policy_type == RolloutApprovalPolicyType.QUORUM
        assert policy.required_approvals == 2

    def test_single_policy_required_approvals_must_be_1(self):
        """SINGLE policy with required_approvals != 1 raises ValueError."""
        with pytest.raises(ValueError):
            RolloutApprovalPolicy(
                policy_type=RolloutApprovalPolicyType.SINGLE,
                required_approvals=2,
            )

    def test_required_approvals_must_be_positive(self):
        """required_approvals < 1 raises ValueError."""
        with pytest.raises(ValueError):
            RolloutApprovalPolicy(required_approvals=0)

    def test_expires_after_seconds_must_be_positive(self):
        """expires_after_seconds of 0 or negative raises ValueError."""
        with pytest.raises(ValueError):
            RolloutApprovalPolicy(expires_after_seconds=0)

        with pytest.raises(ValueError):
            RolloutApprovalPolicy(expires_after_seconds=-10)

    def test_expires_after_seconds_none_is_valid(self):
        """expires_after_seconds=None is valid."""
        policy = RolloutApprovalPolicy(expires_after_seconds=None)
        assert policy.expires_after_seconds is None

    def test_separation_of_duties_defaults(self):
        """prohibit_requester_approval defaults to True; prohibit_creator_approval defaults to False."""
        policy = RolloutApprovalPolicy()
        assert policy.prohibit_requester_approval is True
        assert policy.prohibit_creator_approval is False

    def test_empty_roles_and_permissions_means_no_restriction(self):
        """Empty lists for roles and permissions mean no restriction."""
        policy = RolloutApprovalPolicy()
        assert policy.allowed_approver_roles == []
        assert policy.allowed_approver_permissions == []


# ---------------------------------------------------------------------------
# TestRolloutApprovalDecisionModel
# ---------------------------------------------------------------------------


class TestRolloutApprovalDecisionModel:
    """Tests for the RolloutApprovalDecision model."""

    def test_approve_decision(self):
        """Valid approve decision."""
        decision = _make_decision(
            decision_type=RolloutApprovalDecisionType.APPROVE,
        )
        assert decision.decision_type == RolloutApprovalDecisionType.APPROVE
        assert decision.decision_id == "rsd_001"
        assert decision.approval_id == "rsa_abc123"
        assert decision.decided_by == "user_reviewer"

    def test_reject_decision(self):
        """Valid reject decision."""
        decision = _make_decision(
            decision_type=RolloutApprovalDecisionType.REJECT,
            reason="Risk too high",
        )
        assert decision.decision_type == RolloutApprovalDecisionType.REJECT
        assert decision.reason == "Risk too high"

    def test_decision_id_prefix(self):
        """Non-rsd_ prefix raises ValueError."""
        with pytest.raises(ValueError):
            _make_decision(decision_id="bad_prefix_001")

    def test_timezone_aware_created_at(self):
        """Naive datetime raises ValueError."""
        with pytest.raises(ValueError):
            _make_decision(created_at=datetime(2025, 1, 1, 12, 0, 0))


# ---------------------------------------------------------------------------
# TestRolloutStepApprovalExtended
# ---------------------------------------------------------------------------


class TestRolloutStepApprovalExtended:
    """Tests for extended RolloutStepApproval with policy, decisions, and expires_at."""

    def test_approval_with_policy_and_decisions(self):
        """RolloutStepApproval can carry a policy and decisions."""
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        decision = _make_decision()
        approval = _make_approval(
            policy=policy,
            decisions=[decision],
        )
        assert approval.policy.policy_type == RolloutApprovalPolicyType.QUORUM
        assert approval.policy.required_approvals == 2
        assert len(approval.decisions) == 1
        assert approval.decisions[0].decision_id == "rsd_001"

    def test_approval_with_expires_at(self):
        """RolloutStepApproval can carry expires_at."""
        expires = datetime(2025, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        approval = _make_approval(expires_at=expires)
        assert approval.expires_at == expires

    def test_expired_status_exists(self):
        """EXPIRED status is available on RolloutStepApprovalStatus."""
        assert RolloutStepApprovalStatus.EXPIRED == "expired"


# ---------------------------------------------------------------------------
# TestRolloutApprovalPolicyEvaluator
# ---------------------------------------------------------------------------


from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutStep,
    RolloutStepType,
)
from agent_app.runtime.policy_rollout_approval_policy import (
    ApprovalPolicyError,
    RolloutApprovalPolicyEvaluator,
)


def _make_rollout(**overrides) -> RolloutPlan:
    """Helper to build a RolloutPlan with sensible defaults."""
    now = datetime.now(timezone.utc)
    base = dict(
        rollout_id="ro_eval",
        name="test",
        bundle_id="pb_001",
        steps=[RolloutStep(step_id="s1", step_type=RolloutStepType.ACTIVATE, environment="prod")],
        created_by="creator1",
        created_at=now,
        updated_at=now,
    )
    base.update(overrides)
    return RolloutPlan(**base)


class TestRolloutApprovalPolicyEvaluator:
    """Tests for the RolloutApprovalPolicyEvaluator."""

    def setup_method(self):
        self.evaluator = RolloutApprovalPolicyEvaluator()

    # -- validate_decision: denial cases --

    def test_requester_self_approval_denied(self):
        """prohibit_requester_approval=True blocks requester from approving."""
        policy = RolloutApprovalPolicy(prohibit_requester_approval=True)
        approval = _make_approval(
            requested_by="user_admin",
            policy=policy,
        )
        decision = _make_decision(
            decided_by="user_admin",
            decision_type=RolloutApprovalDecisionType.APPROVE,
        )
        with pytest.raises(ApprovalPolicyError):
            self.evaluator.validate_decision(approval, decision)

    def test_creator_self_approval_denied(self):
        """prohibit_creator_approval=True blocks rollout creator from approving."""
        policy = RolloutApprovalPolicy(prohibit_creator_approval=True)
        approval = _make_approval(policy=policy)
        rollout = _make_rollout(created_by="creator1")
        decision = _make_decision(
            decided_by="creator1",
            decision_type=RolloutApprovalDecisionType.APPROVE,
        )
        with pytest.raises(ApprovalPolicyError):
            self.evaluator.validate_decision(approval, decision, rollout=rollout)

    def test_missing_role_denied(self):
        """Actor without required role is denied."""
        policy = RolloutApprovalPolicy(allowed_approver_roles=["release_reviewer"])
        approval = _make_approval(policy=policy)
        decision = _make_decision(
            decided_by="user_dev",
            roles=["developer"],
            decision_type=RolloutApprovalDecisionType.APPROVE,
        )
        with pytest.raises(ApprovalPolicyError):
            self.evaluator.validate_decision(approval, decision)

    def test_missing_permission_denied(self):
        """Actor without required permission is denied."""
        policy = RolloutApprovalPolicy(allowed_approver_permissions=["rollout:approve"])
        approval = _make_approval(policy=policy)
        decision = _make_decision(
            decided_by="user_dev",
            permissions=["rollout:view"],
            decision_type=RolloutApprovalDecisionType.APPROVE,
        )
        with pytest.raises(ApprovalPolicyError):
            self.evaluator.validate_decision(approval, decision)

    def test_reason_required_denied(self):
        """require_reason=True blocks decision without reason."""
        policy = RolloutApprovalPolicy(require_reason=True)
        approval = _make_approval(policy=policy)
        decision = _make_decision(
            decided_by="user_reviewer",
            reason=None,
            decision_type=RolloutApprovalDecisionType.APPROVE,
        )
        with pytest.raises(ApprovalPolicyError):
            self.evaluator.validate_decision(approval, decision)

    def test_duplicate_actor_denied(self):
        """Actor who already decided cannot decide again."""
        existing = _make_decision(decided_by="user_reviewer", decision_id="rsd_001")
        policy = RolloutApprovalPolicy()
        approval = _make_approval(
            policy=policy,
            decisions=[existing],
        )
        new_decision = _make_decision(decided_by="user_reviewer", decision_id="rsd_002")
        with pytest.raises(ApprovalPolicyError):
            self.evaluator.validate_decision(approval, new_decision)

    def test_already_resolved_denied(self):
        """Cannot decide on already-resolved approval."""
        policy = RolloutApprovalPolicy()
        approval = _make_approval(
            policy=policy,
            status=RolloutStepApprovalStatus.APPROVED,
        )
        decision = _make_decision(decision_type=RolloutApprovalDecisionType.APPROVE)
        with pytest.raises(ApprovalPolicyError):
            self.evaluator.validate_decision(approval, decision)

    def test_expired_denied(self):
        """Cannot decide on expired approval."""
        policy = RolloutApprovalPolicy()
        approval = _make_approval(
            policy=policy,
            expires_at=datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        )
        decision = _make_decision(decision_type=RolloutApprovalDecisionType.APPROVE)
        with pytest.raises(ApprovalPolicyError):
            self.evaluator.validate_decision(approval, decision)

    # -- validate_decision: success case --

    def test_valid_approve_passes(self):
        """Valid approve decision passes validation (no exception)."""
        policy = RolloutApprovalPolicy()
        approval = _make_approval(policy=policy)
        decision = _make_decision(decision_type=RolloutApprovalDecisionType.APPROVE)
        # Should not raise
        self.evaluator.validate_decision(approval, decision)

    # -- evaluate_status --

    def test_evaluate_status_reject_immediate(self):
        """Reject decision makes status REJECTED."""
        policy = RolloutApprovalPolicy()
        reject = _make_decision(
            decision_type=RolloutApprovalDecisionType.REJECT,
            decided_by="user_blocker",
        )
        approval = _make_approval(policy=policy, decisions=[reject])
        result = self.evaluator.evaluate_status(approval)
        assert result == RolloutStepApprovalStatus.REJECTED

    def test_evaluate_status_quorum_pending(self):
        """Quorum not yet reached means PENDING."""
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approve1 = _make_decision(
            decision_type=RolloutApprovalDecisionType.APPROVE,
            decided_by="user_a",
            decision_id="rsd_010",
        )
        approval = _make_approval(policy=policy, decisions=[approve1])
        result = self.evaluator.evaluate_status(approval)
        assert result == RolloutStepApprovalStatus.PENDING

    def test_evaluate_status_quorum_reached(self):
        """Quorum reached means APPROVED."""
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approve1 = _make_decision(
            decision_type=RolloutApprovalDecisionType.APPROVE,
            decided_by="user_a",
            decision_id="rsd_010",
        )
        approve2 = _make_decision(
            decision_type=RolloutApprovalDecisionType.APPROVE,
            decided_by="user_b",
            decision_id="rsd_011",
        )
        approval = _make_approval(policy=policy, decisions=[approve1, approve2])
        result = self.evaluator.evaluate_status(approval)
        assert result == RolloutStepApprovalStatus.APPROVED

    def test_evaluate_status_single_approve(self):
        """SINGLE policy with one approve means APPROVED."""
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.SINGLE,
            required_approvals=1,
        )
        approve1 = _make_decision(
            decision_type=RolloutApprovalDecisionType.APPROVE,
            decided_by="user_a",
        )
        approval = _make_approval(policy=policy, decisions=[approve1])
        result = self.evaluator.evaluate_status(approval)
        assert result == RolloutStepApprovalStatus.APPROVED
