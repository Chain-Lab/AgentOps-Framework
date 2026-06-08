"""Tests for permission checker."""

import pytest

from agent_app.core.context import RunContext
from agent_app.governance.permission import DefaultPermissionChecker


class TestDefaultPermissionChecker:
    @pytest.fixture
    def checker(self) -> DefaultPermissionChecker:
        return DefaultPermissionChecker()

    @pytest.mark.asyncio
    async def test_no_required_perms_allows(self, checker) -> None:
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        assert await checker.check([], ctx) is True

    @pytest.mark.asyncio
    async def test_all_perms_present_allows(self, checker) -> None:
        ctx = RunContext(
            run_id="r1", user_id="u1", tenant_id="t1",
            permissions=["order:read", "refund:create"],
        )
        assert await checker.check(["order:read", "refund:create"], ctx) is True

    @pytest.mark.asyncio
    async def test_subset_perms_allows(self, checker) -> None:
        ctx = RunContext(
            run_id="r1", user_id="u1", tenant_id="t1",
            permissions=["order:read", "refund:create", "admin:*"],
        )
        assert await checker.check(["order:read"], ctx) is True

    @pytest.mark.asyncio
    async def test_missing_single_perm_denies(self, checker) -> None:
        ctx = RunContext(
            run_id="r1", user_id="u1", tenant_id="t1",
            permissions=["order:read"],
        )
        assert await checker.check(["refund:create"], ctx) is False

    @pytest.mark.asyncio
    async def test_missing_multiple_perms_denies(self, checker) -> None:
        ctx = RunContext(
            run_id="r1", user_id="u1", tenant_id="t1",
            permissions=["order:read"],
        )
        assert await checker.check(["refund:create", "admin:*"], ctx) is False

    @pytest.mark.asyncio
    async def test_empty_context_no_perms_denies(self, checker) -> None:
        ctx = RunContext(run_id="r1", user_id="u1", tenant_id="t1")
        assert await checker.check(["order:read"], ctx) is False
