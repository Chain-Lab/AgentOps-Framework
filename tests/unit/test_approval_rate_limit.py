"""Tests for Phase 21 approval rate limiting."""

from __future__ import annotations

import pytest

from agent_app.governance.approval import ApprovalRequest, ApprovalStatus, InMemoryApprovalStore
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.runtime.approval_rate_limit import (
    ApprovalRateLimiter,
    InMemoryApprovalRateLimiter,
    RateLimitConfig,
    SQLiteApprovalRateLimiter,
    create_approval_rate_limiter,
)


def _make_limiter(**kwargs):
    defaults = {
        "max_requests": 3,
        "window_seconds": 60,
    }
    defaults.update(kwargs)
    return InMemoryApprovalRateLimiter(**defaults)


def _make_approval(**kwargs):
    defaults = {
        "approval_id": "apv_rl_001",
        "run_id": "run-rl",
        "tool_name": "refund.request",
        "arguments": {"order_id": "123"},
        "risk_level": "high",
        "tenant_id": "t1",
        "user_id": "u1",
    }
    defaults.update(kwargs)
    return ApprovalRequest(**defaults)


class TestInMemoryApprovalRateLimiter:
    @pytest.mark.asyncio
    async def test_under_limit_allows_creation(self) -> None:
        limiter = _make_limiter(max_requests=3)
        for i in range(3):
            allowed = await limiter.check_allowed(
                tenant_id="t1", user_id="u1", tool_name="refund.request"
            )
            assert allowed is True

    @pytest.mark.asyncio
    async def test_over_limit_blocks_creation(self) -> None:
        limiter = _make_limiter(max_requests=2)
        for i in range(2):
            allowed = await limiter.check_allowed(
                tenant_id="t1", user_id="u1", tool_name="refund.request"
            )
            assert allowed is True
        blocked = await limiter.check_allowed(
            tenant_id="t1", user_id="u1", tool_name="refund.request"
        )
        assert blocked is False

    @pytest.mark.asyncio
    async def test_tenant_isolation(self) -> None:
        limiter = _make_limiter(max_requests=2)
        # Exhaust tenant t1
        for i in range(2):
            await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        # t1 is blocked
        assert await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request") is False
        # t2 is still allowed
        assert await limiter.check_allowed(tenant_id="t2", user_id="u1", tool_name="refund.request") is True

    @pytest.mark.asyncio
    async def test_user_isolation(self) -> None:
        limiter = _make_limiter(max_requests=2)
        # Exhaust user u1
        for i in range(2):
            await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        # u1 is blocked
        assert await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request") is False
        # Different user u2 in same tenant is allowed
        assert await limiter.check_allowed(tenant_id="t1", user_id="u2", tool_name="refund.request") is True

    @pytest.mark.asyncio
    async def test_window_expiry_allows_retry(self) -> None:
        import time
        limiter = _make_limiter(max_requests=2, window_seconds=1)
        for i in range(2):
            await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        assert await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request") is False
        time.sleep(1.1)
        assert await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request") is True

    @pytest.mark.asyncio
    async def test_rate_limit_writes_audit_event(self) -> None:
        logger = InMemoryAuditLogger()
        limiter = InMemoryApprovalRateLimiter(
            max_requests=1, window_seconds=60, audit_logger=logger
        )
        await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        assert await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request") is False
        events = logger.list_events(event_type="approval.rate_limited")
        assert len(events) == 1
        assert events[0].tool_name == "refund.request"
        assert events[0].tenant_id == "t1"


class TestRateLimitConfig:
    def test_defaults(self) -> None:
        cfg = RateLimitConfig()
        assert cfg.max_requests == 10
        assert cfg.window_seconds == 60

    def test_custom_values(self) -> None:
        cfg = RateLimitConfig(max_requests=5, window_seconds=120)
        assert cfg.max_requests == 5
        assert cfg.window_seconds == 120


class TestSQLiteApprovalRateLimiter:
    @pytest.mark.asyncio
    async def test_under_limit_allows_creation(self, tmp_path) -> None:
        db_path = str(tmp_path / "rate_limit.db")
        limiter = SQLiteApprovalRateLimiter(max_requests=3, window_seconds=60, db_path=db_path)
        for i in range(3):
            allowed = await limiter.check_allowed(
                tenant_id="t1", user_id="u1", tool_name="refund.request"
            )
            assert allowed is True
        limiter.close()

    @pytest.mark.asyncio
    async def test_over_limit_blocks_creation(self, tmp_path) -> None:
        db_path = str(tmp_path / "rate_limit.db")
        limiter = SQLiteApprovalRateLimiter(max_requests=2, window_seconds=60, db_path=db_path)
        for i in range(2):
            await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        blocked = await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        assert blocked is False
        limiter.close()

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, tmp_path) -> None:
        db_path = str(tmp_path / "rate_limit.db")
        limiter = SQLiteApprovalRateLimiter(max_requests=2, window_seconds=60, db_path=db_path)
        for i in range(2):
            await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        assert await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request") is False
        assert await limiter.check_allowed(tenant_id="t2", user_id="u1", tool_name="refund.request") is True
        limiter.close()

    @pytest.mark.asyncio
    async def test_window_expiry_allows_retry(self, tmp_path) -> None:
        import time
        db_path = str(tmp_path / "rate_limit.db")
        limiter = SQLiteApprovalRateLimiter(max_requests=2, window_seconds=1, db_path=db_path)
        for i in range(2):
            await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        assert await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request") is False
        time.sleep(1.1)
        assert await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request") is True
        limiter.close()

    @pytest.mark.asyncio
    async def test_state_persists_across_instances(self, tmp_path) -> None:
        """The whole point of a SQLite backend: state survives a process restart."""
        db_path = str(tmp_path / "rate_limit.db")
        limiter1 = SQLiteApprovalRateLimiter(max_requests=2, window_seconds=60, db_path=db_path)
        for i in range(2):
            await limiter1.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        limiter1.close()

        # New instance, same db_path — simulates a restart.
        limiter2 = SQLiteApprovalRateLimiter(max_requests=2, window_seconds=60, db_path=db_path)
        blocked = await limiter2.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        assert blocked is False
        limiter2.close()

    @pytest.mark.asyncio
    async def test_rate_limit_writes_audit_event(self, tmp_path) -> None:
        db_path = str(tmp_path / "rate_limit.db")
        logger = InMemoryAuditLogger()
        limiter = SQLiteApprovalRateLimiter(
            max_requests=1, window_seconds=60, db_path=db_path, audit_logger=logger
        )
        await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
        assert await limiter.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request") is False
        events = logger.list_events(event_type="approval.rate_limited")
        assert len(events) == 1
        limiter.close()

    @pytest.mark.asyncio
    async def test_concurrent_instances_do_not_exceed_limit(self, tmp_path) -> None:
        """Regression: BEGIN IMMEDIATE must prevent two connections from
        both reading a stale count and both inserting past the limit."""
        import asyncio
        db_path = str(tmp_path / "rate_limit.db")
        limiter_a = SQLiteApprovalRateLimiter(max_requests=5, window_seconds=60, db_path=db_path)
        limiter_b = SQLiteApprovalRateLimiter(max_requests=5, window_seconds=60, db_path=db_path)
        results = await asyncio.gather(*[
            limiter_a.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
            if i % 2 == 0 else
            limiter_b.check_allowed(tenant_id="t1", user_id="u1", tool_name="refund.request")
            for i in range(10)
        ])
        assert sum(1 for r in results if r) == 5
        limiter_a.close()
        limiter_b.close()


class TestCreateApprovalRateLimiter:
    def test_creates_memory_backend(self) -> None:
        limiter = create_approval_rate_limiter(backend="memory", max_requests=5, window_seconds=60)
        assert isinstance(limiter, InMemoryApprovalRateLimiter)

    def test_creates_sqlite_backend(self, tmp_path) -> None:
        db_path = str(tmp_path / "rl.db")
        limiter = create_approval_rate_limiter(
            backend="sqlite", max_requests=5, window_seconds=60, db_path=db_path
        )
        assert isinstance(limiter, SQLiteApprovalRateLimiter)
        limiter.close()

    def test_unknown_backend_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown rate limiter backend"):
            create_approval_rate_limiter(backend="redis", max_requests=5, window_seconds=60)
