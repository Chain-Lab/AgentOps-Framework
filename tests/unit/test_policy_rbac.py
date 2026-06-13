"""Tests for PolicyReleasePermission and PolicyReleasePermissionChecker."""

from __future__ import annotations

import pytest
from agent_app.core.context import RunContext
from agent_app.governance.policy_rbac import (
    PolicyReleasePermission,
    PolicyReleasePermissionChecker,
)


class TestPolicyReleasePermission:
    """Test PolicyReleasePermission enum values."""

    def test_permission_enum_values(self) -> None:
        """Assert all 8 enum values match expected strings."""
        assert PolicyReleasePermission.BUNDLE_CREATE.value == "policy.bundle.create"
        assert PolicyReleasePermission.GATE_RUN.value == "policy.gate.run"
        assert PolicyReleasePermission.PROMOTION_REQUEST.value == "policy.promotion.request"
        assert PolicyReleasePermission.PROMOTION_APPROVE.value == "policy.promotion.approve"
        assert PolicyReleasePermission.PROMOTION_REJECT.value == "policy.promotion.reject"
        assert PolicyReleasePermission.PROMOTION_EXECUTE.value == "policy.promotion.execute"
        assert PolicyReleasePermission.ROLLBACK_EXECUTE.value == "policy.rollback.execute"
        assert PolicyReleasePermission.BYPASS_GATE.value == "policy.gate.bypass"


class TestPolicyReleasePermissionChecker:
    """Test PolicyReleasePermissionChecker authorization logic."""

    @pytest.fixture
    def checker(self) -> PolicyReleasePermissionChecker:
        return PolicyReleasePermissionChecker()

    async def test_permission_present_allows(
        self, checker: PolicyReleasePermissionChecker
    ) -> None:
        """Context with policy.promotion.request and policy.promotion.approve allows both."""
        context = RunContext(
            run_id="r1",
            user_id="u1",
            tenant_id="t1",
            permissions=["policy.promotion.request", "policy.promotion.approve"],
        )
        assert await checker.check(PolicyReleasePermission.PROMOTION_REQUEST, context) is True
        assert await checker.check(PolicyReleasePermission.PROMOTION_APPROVE, context) is True

    async def test_missing_permission_denies(
        self, checker: PolicyReleasePermissionChecker
    ) -> None:
        """Context with only policy.bundle.create denies promotion/approve/reject/execute/rollback/bypass."""
        context = RunContext(
            run_id="r1",
            user_id="u1",
            tenant_id="t1",
            permissions=["policy.bundle.create"],
        )
        assert await checker.check(PolicyReleasePermission.PROMOTION_REQUEST, context) is False
        assert await checker.check(PolicyReleasePermission.PROMOTION_APPROVE, context) is False
        assert await checker.check(PolicyReleasePermission.PROMOTION_REJECT, context) is False
        assert await checker.check(PolicyReleasePermission.PROMOTION_EXECUTE, context) is False
        assert await checker.check(PolicyReleasePermission.ROLLBACK_EXECUTE, context) is False
        assert await checker.check(PolicyReleasePermission.BYPASS_GATE, context) is False

    async def test_empty_permissions_denies(
        self, checker: PolicyReleasePermissionChecker
    ) -> None:
        """Empty permissions list allows BUNDLE_CREATE and GATE_RUN (default), denies all others."""
        context = RunContext(
            run_id="r1",
            user_id="u1",
            tenant_id="t1",
            permissions=[],
        )
        # Default-allowed permissions
        assert await checker.check(PolicyReleasePermission.BUNDLE_CREATE, context) is True
        assert await checker.check(PolicyReleasePermission.GATE_RUN, context) is True
        # All others denied
        assert await checker.check(PolicyReleasePermission.PROMOTION_REQUEST, context) is False
        assert await checker.check(PolicyReleasePermission.PROMOTION_APPROVE, context) is False
        assert await checker.check(PolicyReleasePermission.PROMOTION_REJECT, context) is False
        assert await checker.check(PolicyReleasePermission.PROMOTION_EXECUTE, context) is False
        assert await checker.check(PolicyReleasePermission.ROLLBACK_EXECUTE, context) is False
        assert await checker.check(PolicyReleasePermission.BYPASS_GATE, context) is False


class TestPhase32Permissions:
    def test_environment_disable_permission(self):
        from agent_app.governance.policy_rbac import PolicyReleasePermission
        assert PolicyReleasePermission.ENVIRONMENT_DISABLE == "policy.environment.disable"

    def test_environment_enable_permission(self):
        from agent_app.governance.policy_rbac import PolicyReleasePermission
        assert PolicyReleasePermission.ENVIRONMENT_ENABLE == "policy.environment.enable"

    def test_environment_view_permission(self):
        from agent_app.governance.policy_rbac import PolicyReleasePermission
        assert PolicyReleasePermission.ENVIRONMENT_VIEW == "policy.environment.view"

    @pytest.mark.asyncio
    async def test_environment_view_default_allowed(self):
        from agent_app.governance.policy_rbac import PolicyReleasePermission, PolicyReleasePermissionChecker
        from agent_app.core.context import RunContext
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        assert await checker.check(PolicyReleasePermission.ENVIRONMENT_VIEW, ctx) is True

    @pytest.mark.asyncio
    async def test_environment_disable_requires_grant(self):
        from agent_app.governance.policy_rbac import PolicyReleasePermission, PolicyReleasePermissionChecker
        from agent_app.core.context import RunContext
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        assert await checker.check(PolicyReleasePermission.ENVIRONMENT_DISABLE, ctx) is False
        ctx_with_perm = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.environment.disable"])
        assert await checker.check(PolicyReleasePermission.ENVIRONMENT_DISABLE, ctx_with_perm) is True

    @pytest.mark.asyncio
    async def test_environment_enable_requires_grant(self):
        from agent_app.governance.policy_rbac import PolicyReleasePermission, PolicyReleasePermissionChecker
        from agent_app.core.context import RunContext
        checker = PolicyReleasePermissionChecker()
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=[])
        assert await checker.check(PolicyReleasePermission.ENVIRONMENT_ENABLE, ctx) is False
        ctx_with_perm = RunContext(run_id="r1", user_id="u1", tenant_id="t1", permissions=["policy.environment.enable"])
        assert await checker.check(PolicyReleasePermission.ENVIRONMENT_ENABLE, ctx_with_perm) is True
