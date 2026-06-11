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
    """

    BUNDLE_CREATE = "policy.bundle.create"
    GATE_RUN = "policy.gate.run"
    PROMOTION_REQUEST = "policy.promotion.request"
    PROMOTION_APPROVE = "policy.promotion.approve"
    PROMOTION_REJECT = "policy.promotion.reject"
    PROMOTION_EXECUTE = "policy.promotion.execute"
    ROLLBACK_EXECUTE = "policy.rollback.execute"
    BYPASS_GATE = "policy.gate.bypass"


_DEFAULT_ALLOWED: set[PolicyReleasePermission] = {
    PolicyReleasePermission.BUNDLE_CREATE,
    PolicyReleasePermission.GATE_RUN,
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
