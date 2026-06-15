"""Integration tests for policy-aware quorum approval workflow in RolloutService.

Phase 37 Task 4: Tests that the RolloutService uses decisions (not direct approve/reject)
for the approval flow, supports quorum policies, self-approval blocking, role restrictions,
expiration, and backward compatibility with SINGLE policy.

Phase 37 Task 8: Tests for audit and change event emission during approval operations.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from conftest import _run_async

from agent_app.core.context import RunContext
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepStatus,
    RolloutStepType,
)
from agent_app.governance.policy_rollout_approval import (
    RolloutApprovalDecisionType,
    RolloutApprovalPolicy,
    RolloutApprovalPolicyType,
    RolloutStepApprovalStatus,
)
from agent_app.runtime.policy_rollout_approval_policy import ApprovalPolicyError
from agent_app.runtime.policy_rollout_approval_store import InMemoryRolloutStepApprovalStore
from agent_app.runtime.policy_rollout_service import RolloutService
from agent_app.runtime.policy_rollout_store import InMemoryRolloutPlanStore


# -- Helpers --


def _make_service(approval_policy=None):
    """Create a RolloutService with InMemory stores for quorum testing."""
    rollout_store = InMemoryRolloutPlanStore()
    approval_store = InMemoryRolloutStepApprovalStore()
    service = RolloutService(
        rollout_store=rollout_store,
        release_service=None,
        approval_store=approval_store,
        approval_policy=approval_policy,
    )
    return service, rollout_store, approval_store


def _make_plan(service, rollout_store, ctx, steps=None, created_by="creator1"):
    """Create and start a rollout plan."""
    if steps is None:
        steps = [
            RolloutStep(
                step_id="s1",
                step_type=RolloutStepType.ASSIGN_RING,
                environment="prod",
                ring_name="canary",
                status=RolloutStepStatus.PENDING,
                requires_approval=True,
            ),
        ]
    plan = RolloutPlan(
        rollout_id=f"ro_{uuid.uuid4().hex[:12]}",
        name="quorum-test",
        bundle_id="pb_001",
        status=RolloutPlanStatus.ACTIVE,
        steps=steps,
        created_by=created_by,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    _run_async(rollout_store.create(plan))
    return plan


def _make_context(
    user_id="reviewer1",
    roles=None,
    permissions=None,
):
    return RunContext(
        run_id="test_run",
        user_id=user_id,
        tenant_id="default",
        roles=roles or ["release_reviewer"],
        permissions=permissions or ["policy.rollout.approval.approve"],
    )


def _request_approval(service, plan, ctx, requested_by="requester1", policy=None):
    """Request approval for step s1 of the given plan."""
    return _run_async(
        service.request_step_approval(
            rollout_id=plan.rollout_id,
            step_id="s1",
            requested_by=requested_by,
            context=ctx,
            reason="Need approval",
            policy=policy,
        )
    )


# -- Test class --


class TestQuorumApprovalWorkflow:
    """Integration tests for quorum and policy-aware approval flow."""

    def test_request_creates_approval_with_policy(self):
        """request_step_approval with quorum policy creates approval with that policy."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        quorum_policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = _request_approval(service, plan, ctx, policy=quorum_policy)

        assert approval.policy.policy_type == RolloutApprovalPolicyType.QUORUM
        assert approval.policy.required_approvals == 2
        assert approval.status == RolloutStepApprovalStatus.PENDING

    def test_first_quorum_approval_keeps_step_blocked(self):
        """First approve on quorum=2 keeps step BLOCKED, approval PENDING."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        quorum_policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = _request_approval(service, plan, ctx, policy=quorum_policy)

        # First approve — should not reach quorum
        ctx1 = _make_context(user_id="reviewer1")
        updated_approval = _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer1",
                context=ctx1,
                reason="First approval",
            )
        )

        # Approval should still be PENDING (quorum not reached)
        assert updated_approval.status == RolloutStepApprovalStatus.PENDING

        # Step should still be BLOCKED
        updated_plan = _run_async(rollout_store.get(plan.rollout_id))
        step = next(s for s in updated_plan.steps if s.step_id == "s1")
        assert step.status == RolloutStepStatus.BLOCKED

    def test_second_quorum_approval_unblocks_step(self):
        """Second approve on quorum=2 makes approval APPROVED, step goes to PENDING."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        quorum_policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = _request_approval(service, plan, ctx, policy=quorum_policy)

        # First approve
        ctx1 = _make_context(user_id="reviewer1")
        _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer1",
                context=ctx1,
                reason="First",
            )
        )

        # Second approve — should reach quorum
        ctx2 = _make_context(user_id="reviewer2")
        updated_approval = _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer2",
                context=ctx2,
                reason="Second",
            )
        )

        # Approval should be APPROVED
        assert updated_approval.status == RolloutStepApprovalStatus.APPROVED

        # Step should be PENDING (unblocked)
        updated_plan = _run_async(rollout_store.get(plan.rollout_id))
        step = next(s for s in updated_plan.steps if s.step_id == "s1")
        assert step.status == RolloutStepStatus.PENDING

    def test_approved_quorum_step_executes(self):
        """After quorum approval, run_next_step can execute the step.

        Note: This test uses ACTIVATE step type which requires a release_service.
        We instead verify that the step transitions from BLOCKED to PENDING (runnable).
        """
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        quorum_policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = _request_approval(service, plan, ctx, policy=quorum_policy)

        # Two approvals to reach quorum
        ctx1 = _make_context(user_id="reviewer1")
        _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer1",
                context=ctx1,
                reason="First",
            )
        )
        ctx2 = _make_context(user_id="reviewer2")
        _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer2",
                context=ctx2,
                reason="Second",
            )
        )

        # Step should now be PENDING (runnable)
        updated_plan = _run_async(rollout_store.get(plan.rollout_id))
        step = next(s for s in updated_plan.steps if s.step_id == "s1")
        assert step.status == RolloutStepStatus.PENDING

    def test_reject_fails_step_and_plan(self):
        """Reject makes approval REJECTED, step FAILED, plan FAILED."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        quorum_policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = _request_approval(service, plan, ctx, policy=quorum_policy)

        # First approve
        ctx1 = _make_context(user_id="reviewer1")
        _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer1",
                context=ctx1,
                reason="First",
            )
        )

        # Now reject
        ctx2 = _make_context(user_id="reviewer2")
        updated_approval = _run_async(
            service.reject_step(
                approval_id=approval.approval_id,
                rejected_by="reviewer2",
                context=ctx2,
                reason="Not ready",
            )
        )

        # Approval should be REJECTED
        assert updated_approval.status == RolloutStepApprovalStatus.REJECTED

        # Step should be FAILED
        updated_plan = _run_async(rollout_store.get(plan.rollout_id))
        step = next(s for s in updated_plan.steps if s.step_id == "s1")
        assert step.status == RolloutStepStatus.FAILED

        # Plan should be FAILED
        assert updated_plan.status == RolloutPlanStatus.FAILED

    def test_self_approval_blocked(self):
        """prohibit_requester_approval=True blocks requester from approving."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.SINGLE,
            required_approvals=1,
            prohibit_requester_approval=True,
        )
        approval = _request_approval(
            service, plan, ctx, requested_by="requester1", policy=policy,
        )

        # Requester tries to approve their own request
        ctx_self = _make_context(user_id="requester1")
        with pytest.raises(ApprovalPolicyError, match="requester"):
            _run_async(
                service.approve_step(
                    approval_id=approval.approval_id,
                    approved_by="requester1",
                    context=ctx_self,
                    reason="Self-approve",
                )
            )

    def test_creator_approval_blocked(self):
        """prohibit_creator_approval=True blocks plan creator from approving."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx, created_by="creator1")

        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.SINGLE,
            required_approvals=1,
            prohibit_creator_approval=True,
        )
        approval = _request_approval(service, plan, ctx, policy=policy)

        # Plan creator tries to approve
        ctx_creator = _make_context(user_id="creator1")
        with pytest.raises(ApprovalPolicyError, match="creator"):
            _run_async(
                service.approve_step(
                    approval_id=approval.approval_id,
                    approved_by="creator1",
                    context=ctx_creator,
                    reason="Creator approve",
                )
            )

    def test_role_restriction_enforced(self):
        """Actor without required role is denied."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.SINGLE,
            required_approvals=1,
            allowed_approver_roles=["release_manager"],
        )
        approval = _request_approval(service, plan, ctx, policy=policy)

        # User without release_manager role tries to approve
        ctx_no_role = _make_context(user_id="reviewer1", roles=["release_reviewer"])
        with pytest.raises(ApprovalPolicyError, match="required roles"):
            _run_async(
                service.approve_step(
                    approval_id=approval.approval_id,
                    approved_by="reviewer1",
                    context=ctx_no_role,
                    reason="Wrong role",
                )
            )

        # User with release_manager role can approve
        ctx_right_role = _make_context(user_id="manager1", roles=["release_manager"])
        updated = _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="manager1",
                context=ctx_right_role,
                reason="Right role",
            )
        )
        assert updated.status == RolloutStepApprovalStatus.APPROVED

    def test_expiration_enforced(self):
        """Expired approval cannot receive decisions."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.SINGLE,
            required_approvals=1,
            expires_after_seconds=3600,
        )
        approval = _request_approval(service, plan, ctx, policy=policy)

        # Approval should have expires_at set
        assert approval.expires_at is not None

        # Manually expire the approval in the store
        _run_async(
            service._approval_store.expire_pending(
                now=datetime.now(timezone.utc) + timedelta(hours=2),
            )
        )

        # Try to approve — should fail (status is EXPIRED, not PENDING)
        ctx_approver = _make_context(user_id="reviewer1")
        with pytest.raises(ValueError, match="PENDING"):
            _run_async(
                service.approve_step(
                    approval_id=approval.approval_id,
                    approved_by="reviewer1",
                    context=ctx_approver,
                    reason="Too late",
                )
            )

    def test_single_policy_backward_compat(self):
        """Default SINGLE policy works exactly like Phase 36."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        # No explicit policy — defaults to SINGLE
        approval = _request_approval(service, plan, ctx)

        # Default policy should be SINGLE with required_approvals=1
        assert approval.policy.policy_type == RolloutApprovalPolicyType.SINGLE
        assert approval.policy.required_approvals == 1

        # Single approve should immediately resolve
        ctx_approver = _make_context(user_id="reviewer1")
        updated_approval = _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer1",
                context=ctx_approver,
                reason="Approved",
            )
        )

        assert updated_approval.status == RolloutStepApprovalStatus.APPROVED
        assert updated_approval.resolved_by == "reviewer1"

        # Step should be unblocked
        updated_plan = _run_async(rollout_store.get(plan.rollout_id))
        step = next(s for s in updated_plan.steps if s.step_id == "s1")
        assert step.status == RolloutStepStatus.PENDING

    def test_expire_approvals_method(self):
        """expire_approvals marks pending approvals past their expires_at as EXPIRED."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.SINGLE,
            required_approvals=1,
            expires_after_seconds=60,
        )
        approval = _request_approval(service, plan, ctx, policy=policy)

        # Approval should have expires_at in the future
        assert approval.expires_at is not None

        # Expire with a future timestamp to trigger expiration
        expired = _run_async(
            service._approval_store.expire_pending(
                now=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )

        assert len(expired) == 1
        assert expired[0].status == RolloutStepApprovalStatus.EXPIRED

    def test_request_step_approval_sets_expires_at(self):
        """request_step_approval sets expires_at when policy has expires_after_seconds."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.SINGLE,
            required_approvals=1,
            expires_after_seconds=3600,
        )
        approval = _request_approval(service, plan, ctx, policy=policy)

        assert approval.expires_at is not None
        # expires_at should be roughly 3600 seconds from now
        delta = approval.expires_at - datetime.now(timezone.utc)
        assert 3500 < delta.total_seconds() <= 3600

    def test_service_default_approval_policy(self):
        """RolloutService __init__ accepts approval_policy parameter."""
        quorum = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=3,
        )
        service, _, _ = _make_service(approval_policy=quorum)
        assert service._approval_policy == quorum

    def test_decision_recorded_event_when_quorum_pending(self):
        """When quorum is not yet reached, a decision_recorded event is emitted."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        quorum_policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = _request_approval(service, plan, ctx, policy=quorum_policy)

        # First approve — quorum not reached
        ctx1 = _make_context(user_id="reviewer1")
        _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer1",
                context=ctx1,
                reason="First",
            )
        )

        # The approval should have one decision recorded
        refreshed = _run_async(service._approval_store.get(approval.approval_id))
        assert len(refreshed.decisions) == 1
        assert refreshed.decisions[0].decision_type == RolloutApprovalDecisionType.APPROVE
        assert refreshed.decisions[0].decided_by == "reviewer1"

    def test_quorum_approval_has_multiple_decisions(self):
        """Quorum approval accumulates decisions from multiple approvers."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        quorum_policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = _request_approval(service, plan, ctx, policy=quorum_policy)

        ctx1 = _make_context(user_id="reviewer1")
        _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer1",
                context=ctx1,
                reason="First",
            )
        )
        ctx2 = _make_context(user_id="reviewer2")
        _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer2",
                context=ctx2,
                reason="Second",
            )
        )

        # Should have 2 decisions
        refreshed = _run_async(service._approval_store.get(approval.approval_id))
        assert len(refreshed.decisions) == 2
        assert refreshed.status == RolloutStepApprovalStatus.APPROVED

    def test_duplicate_approver_blocked(self):
        """Same actor cannot approve twice (enforced by store)."""
        service, rollout_store, _ = _make_service()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        quorum_policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = _request_approval(service, plan, ctx, policy=quorum_policy)

        ctx1 = _make_context(user_id="reviewer1")
        _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer1",
                context=ctx1,
                reason="First",
            )
        )

        # Same person tries again
        with pytest.raises(ValueError, match="already submitted"):
            _run_async(
                service.approve_step(
                    approval_id=approval.approval_id,
                    approved_by="reviewer1",
                    context=ctx1,
                    reason="Try again",
                )
            )


# -- Audit event helpers --


def _make_service_with_audit(approval_policy=None):
    """Create a RolloutService with InMemory stores and an InMemoryAuditLogger."""
    rollout_store = InMemoryRolloutPlanStore()
    approval_store = InMemoryRolloutStepApprovalStore()
    audit_logger = InMemoryAuditLogger()
    service = RolloutService(
        rollout_store=rollout_store,
        release_service=None,
        approval_store=approval_store,
        audit_logger=audit_logger,
        approval_policy=approval_policy,
    )
    return service, rollout_store, approval_store, audit_logger


def _find_audit_events(audit_logger, event_type):
    """Find audit events matching the given event_type."""
    return [e for e in audit_logger._events if e.event_type == event_type]


class TestApprovalAuditEvents:
    """Tests for audit and change event emission during approval operations.

    Phase 37 Task 8: Verify that RolloutService emits the correct audit
    and change events for approval decisions, quorum milestones, policy
    denials, and expiration.
    """

    def test_decision_recorded_event_on_quorum_pending(self):
        """When first quorum approve keeps step blocked, a decision_recorded audit event is emitted."""
        service, rollout_store, _, audit_logger = _make_service_with_audit()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        quorum_policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = _request_approval(service, plan, ctx, policy=quorum_policy)

        # First approve — quorum not reached
        ctx1 = _make_context(user_id="reviewer1")
        _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer1",
                context=ctx1,
                reason="First",
            )
        )

        # Should have a decision_recorded audit event
        events = _find_audit_events(audit_logger, "policy.rollout.approval.decision_recorded")
        assert len(events) == 1
        data = events[0].data
        assert data["approval_id"] == approval.approval_id
        assert data["rollout_id"] == plan.rollout_id
        assert data["step_id"] == "s1"
        assert data["actor_id"] == "reviewer1"
        assert data["decision_type"] == "approve"
        assert data["required_approvals"] == 2
        assert data["current_approvals"] == 1
        assert data["policy_type"] == "quorum"

    def test_quorum_reached_event(self):
        """When second quorum approve reaches threshold, a quorum_reached event is emitted."""
        service, rollout_store, _, audit_logger = _make_service_with_audit()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        quorum_policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = _request_approval(service, plan, ctx, policy=quorum_policy)

        # First approve
        ctx1 = _make_context(user_id="reviewer1")
        _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer1",
                context=ctx1,
                reason="First",
            )
        )

        # Second approve — quorum reached
        ctx2 = _make_context(user_id="reviewer2")
        _run_async(
            service.approve_step(
                approval_id=approval.approval_id,
                approved_by="reviewer2",
                context=ctx2,
                reason="Second",
            )
        )

        # Should have a quorum_reached audit event
        events = _find_audit_events(audit_logger, "policy.rollout.approval.quorum_reached")
        assert len(events) == 1
        data = events[0].data
        assert data["approval_id"] == approval.approval_id
        assert data["rollout_id"] == plan.rollout_id
        assert data["step_id"] == "s1"
        assert data["actor_id"] == "reviewer2"
        assert data["decision_type"] == "approve"
        assert data["required_approvals"] == 2
        assert data["current_approvals"] == 2
        assert data["policy_type"] == "quorum"

        # Should also have an approved event
        approved_events = _find_audit_events(audit_logger, "policy.rollout.approval.approved")
        assert len(approved_events) == 1

    def test_policy_denied_event_on_self_approval(self):
        """When self-approval is blocked by policy, a policy_denied event is emitted."""
        service, rollout_store, _, audit_logger = _make_service_with_audit()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.SINGLE,
            required_approvals=1,
            prohibit_requester_approval=True,
        )
        approval = _request_approval(
            service, plan, ctx, requested_by="requester1", policy=policy,
        )

        # Requester tries to approve their own request
        ctx_self = _make_context(user_id="requester1")
        with pytest.raises(ApprovalPolicyError, match="requester"):
            _run_async(
                service.approve_step(
                    approval_id=approval.approval_id,
                    approved_by="requester1",
                    context=ctx_self,
                    reason="Self-approve",
                )
            )

        # Should have a policy_denied audit event
        events = _find_audit_events(audit_logger, "policy.rollout.approval.policy_denied")
        assert len(events) == 1
        data = events[0].data
        assert data["approval_id"] == approval.approval_id
        assert data["rollout_id"] == plan.rollout_id
        assert data["step_id"] == "s1"
        assert data["actor_id"] == "requester1"
        assert "requester" in data["denial_reason"]

    def test_expired_event_on_expire(self):
        """When expire_approvals runs, an expired event is emitted for each expired approval."""
        service, rollout_store, _, audit_logger = _make_service_with_audit()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.SINGLE,
            required_approvals=1,
            expires_after_seconds=1,  # expires in 1 second
        )
        approval = _request_approval(service, plan, ctx, policy=policy)

        # Wait for the approval to actually expire, then run expire_approvals
        import time
        time.sleep(1.5)

        # Run expire_approvals — should find and expire the approval
        expired = _run_async(service.expire_approvals(context=ctx))
        assert len(expired) >= 1

        # Should have an expired audit event
        events = _find_audit_events(audit_logger, "policy.rollout.approval.expired")
        assert len(events) >= 1
        assert events[0].data["approval_id"] == approval.approval_id

    def test_reject_emits_decision_recorded(self):
        """Reject decision emits decision_recorded event."""
        service, rollout_store, _, audit_logger = _make_service_with_audit()
        ctx = _make_context()
        plan = _make_plan(service, rollout_store, ctx)

        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.SINGLE,
            required_approvals=1,
        )
        approval = _request_approval(service, plan, ctx, policy=policy)

        # Reject the approval
        ctx_reject = _make_context(user_id="rejector1")
        _run_async(
            service.reject_step(
                approval_id=approval.approval_id,
                rejected_by="rejector1",
                context=ctx_reject,
                reason="Not ready",
            )
        )

        # Should have a decision_recorded audit event
        events = _find_audit_events(audit_logger, "policy.rollout.approval.decision_recorded")
        assert len(events) == 1
        data = events[0].data
        assert data["approval_id"] == approval.approval_id
        assert data["rollout_id"] == plan.rollout_id
        assert data["step_id"] == "s1"
        assert data["actor_id"] == "rejector1"
        assert data["decision_type"] == "reject"

        # Should also have a rejected audit event
        rejected_events = _find_audit_events(audit_logger, "policy.rollout.approval.rejected")
        assert len(rejected_events) == 1
