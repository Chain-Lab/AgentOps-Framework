"""Policy release RBAC — permission enum and checker for policy governance actions."""

from __future__ import annotations

from enum import StrEnum

from agent_app.core.context import RunContext


class PolicyReleasePermission(StrEnum):
    """Permissions governing policy release operations.

    Attributes:
        BUNDLE_CREATE: Create a new policy bundle.
        GATE_RUN: Execute a policy gate evaluation.
        PROMOTION_REQUEST: Request promotion of a policy bundle.
        PROMOTION_APPROVE: Approve a policy promotion request.
        PROMOTION_REJECT: Reject a policy promotion request.
        PROMOTION_EXECUTE: Execute a policy promotion.
        ROLLBACK_EXECUTE: Execute a policy rollback.
        BYPASS_GATE: Bypass a policy gate.
        ENVIRONMENT_DISABLE: Disable a policy environment.
        ENVIRONMENT_ENABLE: Enable a policy environment.
        ENVIRONMENT_VIEW: View policy environment state.
        RING_CREATE: Create a release ring.
        RING_ASSIGN: Assign a ring to an activation.
        RING_PROMOTE: Promote a policy bundle to a ring.
        RING_DISABLE: Disable a release ring.
        RING_ENABLE: Enable a release ring.
        RING_VIEW: View release ring state.
        RELOAD_REQUEST: Request a policy reload (requires explicit permission).
        RELOAD_VIEW: View policy reload status (default-allowed).
        EVENT_VIEW: View policy change events (default-allowed).
        ROUTING_SIMULATE: Simulate policy routing decisions (requires explicit permission).
        ROLLOUT_CREATE: Create a policy rollout.
        ROLLOUT_START: Start a policy rollout.
        ROLLOUT_EXECUTE: Execute a policy rollout.
        ROLLOUT_CANCEL: Cancel a policy rollout.
        ROLLOUT_VIEW: View policy rollout state (default-allowed).
        ROLLOUT_APPROVAL_REQUEST: Request approval for a rollout step (requires explicit permission).
        ROLLOUT_APPROVAL_APPROVE: Approve a rollout step (requires explicit permission).
        ROLLOUT_APPROVAL_REJECT: Reject a rollout step (requires explicit permission).
        ROLLOUT_APPROVAL_VIEW: View rollout approval state (default-allowed).
    """

    BUNDLE_CREATE = "policy.bundle.create"
    GATE_RUN = "policy.gate.run"
    PROMOTION_REQUEST = "policy.promotion.request"
    PROMOTION_APPROVE = "policy.promotion.approve"
    PROMOTION_REJECT = "policy.promotion.reject"
    PROMOTION_EXECUTE = "policy.promotion.execute"
    ROLLBACK_EXECUTE = "policy.rollback.execute"
    BYPASS_GATE = "policy.gate.bypass"
    ENVIRONMENT_DISABLE = "policy.environment.disable"
    ENVIRONMENT_ENABLE = "policy.environment.enable"
    ENVIRONMENT_VIEW = "policy.environment.view"
    RING_CREATE = "policy.ring.create"
    RING_ASSIGN = "policy.ring.assign"
    RING_PROMOTE = "policy.ring.promote"
    RING_DISABLE = "policy.ring.disable"
    RING_ENABLE = "policy.ring.enable"
    RING_VIEW = "policy.ring.view"
    RELOAD_REQUEST = "policy.reload.request"
    RELOAD_VIEW = "policy.reload.view"
    EVENT_VIEW = "policy.event.view"
    ROUTING_SIMULATE = "policy.routing.simulate"
    ROLLOUT_CREATE = "policy.rollout.create"
    ROLLOUT_START = "policy.rollout.start"
    ROLLOUT_EXECUTE = "policy.rollout.execute"
    ROLLOUT_CANCEL = "policy.rollout.cancel"
    ROLLOUT_VIEW = "policy.rollout.view"
    ROLLOUT_APPROVAL_REQUEST = "policy.rollout.approval.request"
    ROLLOUT_APPROVAL_APPROVE = "policy.rollout.approval.approve"
    ROLLOUT_APPROVAL_REJECT = "policy.rollout.approval.reject"
    ROLLOUT_APPROVAL_VIEW = "policy.rollout.approval.view"
    RUNTIME_POLICY_CREATE = "policy.runtime.create"
    RUNTIME_POLICY_VIEW = "policy.runtime.view"
    RUNTIME_POLICY_ENABLE = "policy.runtime.enable"
    RUNTIME_POLICY_DISABLE = "policy.runtime.disable"
    RUNTIME_POLICY_EVALUATE = "policy.runtime.evaluate"
    OBSERVABILITY_VIEW = "policy.observability.view"
    OBSERVABILITY_EXPORT = "policy.observability.export"
    SIMULATION_RUN = "policy.simulation.run"
    SIMULATION_VIEW = "policy.simulation.view"
    SIMULATION_EXPORT = "policy.simulation.export"


_DEFAULT_ALLOWED: set[PolicyReleasePermission] = {
    PolicyReleasePermission.BUNDLE_CREATE,
    PolicyReleasePermission.GATE_RUN,
    PolicyReleasePermission.ENVIRONMENT_VIEW,
    PolicyReleasePermission.RING_VIEW,
    PolicyReleasePermission.RELOAD_VIEW,
    PolicyReleasePermission.EVENT_VIEW,
    PolicyReleasePermission.ROLLOUT_VIEW,
    PolicyReleasePermission.ROLLOUT_APPROVAL_VIEW,
    PolicyReleasePermission.RUNTIME_POLICY_VIEW,
    PolicyReleasePermission.RUNTIME_POLICY_EVALUATE,
    PolicyReleasePermission.OBSERVABILITY_VIEW,
    PolicyReleasePermission.SIMULATION_VIEW,
}


class PolicyReleasePermissionChecker:
    """RBAC checker for policy release permissions.

    Authorization rules:
    1. If the permission is in the default-allowed set, allow without
       requiring explicit permission in context.
    2. If the context's ``permissions`` list contains the required
       permission, allow.
    3. Otherwise deny.
    """

    async def check(
        self,
        required_permission: PolicyReleasePermission,
        context: RunContext,
    ) -> bool:
        """Check whether the context grants the required policy release permission.

        Args:
            required_permission: The policy release permission to check.
            context: Current run context (user, tenant, roles, permissions).

        Returns:
            True if authorized, False otherwise.
        """
        if required_permission in _DEFAULT_ALLOWED:
            return True
        return required_permission.value in context.permissions
