"""Rollout approval policy evaluator — validates decisions against policy constraints.

Phase 37: Separation of duties, quorum approvals, role/permission constraints.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent_app.governance.policy_rollout_approval import (
    RolloutApprovalDecision,
    RolloutApprovalDecisionType,
    RolloutApprovalPolicy,
    RolloutApprovalPolicyType,
    RolloutStepApproval,
    RolloutStepApprovalStatus,
)


class ApprovalPolicyError(ValueError):
    """Raised when an approval decision violates the policy."""


class RolloutApprovalPolicyEvaluator:
    """Validates approval decisions and evaluates approval status."""

    def validate_decision(
        self,
        approval: RolloutStepApproval,
        decision: RolloutApprovalDecision,
        rollout: object | None = None,
        step: object | None = None,
    ) -> None:
        """Validate that a decision is allowed by the approval policy.

        Raises ApprovalPolicyError if the decision violates any policy constraint.

        Validation checks:
        1. Approval must be PENDING
        2. Approval must not be expired
        3. Actor must not have already decided
        4. If require_reason=True, reason must be present
        5. If prohibit_requester_approval=True, actor cannot equal approval.requested_by
        6. If prohibit_creator_approval=True, actor cannot equal rollout.created_by
        7. If allowed_approver_roles is non-empty, actor must have at least one allowed role
        8. If allowed_approver_permissions is non-empty, actor must have at least one allowed permission
        """
        policy = approval.policy

        # 1. Approval must be PENDING
        if approval.status != RolloutStepApprovalStatus.PENDING:
            raise ApprovalPolicyError(
                f"Approval is not PENDING (current status: {approval.status.value})"
            )

        # 2. Approval must not be expired
        if approval.expires_at is not None and approval.expires_at <= datetime.now(timezone.utc):
            raise ApprovalPolicyError("Approval has expired")

        # 3. Actor must not have already decided
        existing_actors = {d.decided_by for d in approval.decisions}
        if decision.decided_by in existing_actors:
            raise ApprovalPolicyError(
                f"Actor '{decision.decided_by}' has already submitted a decision"
            )

        # 4. If require_reason=True, reason must be present
        if policy.require_reason and not decision.reason:
            raise ApprovalPolicyError("Policy requires a reason for this decision")

        # 5. If prohibit_requester_approval=True, actor cannot equal approval.requested_by
        if policy.prohibit_requester_approval and decision.decided_by == approval.requested_by:
            raise ApprovalPolicyError(
                "Policy prohibits the approval requester from approving"
            )

        # 6. If prohibit_creator_approval=True, actor cannot equal rollout.created_by
        if policy.prohibit_creator_approval and rollout is not None:
            creator_by = getattr(rollout, "created_by", None)
            if creator_by is not None and decision.decided_by == creator_by:
                raise ApprovalPolicyError(
                    "Policy prohibits the rollout creator from approving"
                )

        # 7. If allowed_approver_roles is non-empty, actor must have at least one allowed role
        if policy.allowed_approver_roles:
            if not any(role in policy.allowed_approver_roles for role in decision.roles):
                raise ApprovalPolicyError(
                    f"Actor does not have any of the required roles: {policy.allowed_approver_roles}"
                )

        # 8. If allowed_approver_permissions is non-empty, actor must have at least one allowed permission
        if policy.allowed_approver_permissions:
            if not any(
                perm in policy.allowed_approver_permissions for perm in decision.permissions
            ):
                raise ApprovalPolicyError(
                    f"Actor does not have any of the required permissions: {policy.allowed_approver_permissions}"
                )

    def evaluate_status(
        self,
        approval: RolloutStepApproval,
    ) -> RolloutStepApprovalStatus:
        """Evaluate the status of an approval based on its decisions and policy.

        Any reject -> REJECTED
        Approve count >= required_approvals -> APPROVED
        Otherwise -> PENDING
        """
        decisions = approval.decisions
        policy = approval.policy

        # Any reject -> REJECTED
        if any(d.decision_type == RolloutApprovalDecisionType.REJECT for d in decisions):
            return RolloutStepApprovalStatus.REJECTED

        # Count approves
        approve_count = sum(
            1 for d in decisions if d.decision_type == RolloutApprovalDecisionType.APPROVE
        )

        # Approve count >= required_approvals -> APPROVED
        if approve_count >= policy.required_approvals:
            return RolloutStepApprovalStatus.APPROVED

        # Otherwise -> PENDING
        return RolloutStepApprovalStatus.PENDING
