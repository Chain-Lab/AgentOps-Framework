"""Tests for Phase 21 approval TTL/expiration enforcement."""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from agent_app.governance.approval import ApprovalRequest, ApprovalStatus, InMemoryApprovalStore
from agent_app.governance.audit import InMemoryAuditLogger
from agent_app.runtime.approval_store import SQLiteApprovalStore


def _make_approval(**kwargs):
    defaults = {
        "approval_id": "apv_ttl_001",
        "run_id": "run-ttl",
        "tool_name": "refund.request",
        "arguments": {"order_id": "123"},
        "risk_level": "high",
        "tenant_id": "t1",
    }
    defaults.update(kwargs)
    return ApprovalRequest(**defaults)


class TestApprovalTTLInMemory:
    @pytest.fixture
    def store(self) -> InMemoryApprovalStore:
        return InMemoryApprovalStore()

    @pytest.mark.asyncio
    async def test_approval_with_future_expiry_can_approve(self, store) -> None:
        """pending approval with future expires_at can be approved."""
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        req = _make_approval(approval_id="apv_future", expires_at=future)
        await store.create(req)
        updated = await store.approve("apv_future", approved_by="mgr")
        assert updated.status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_expired_approval_cannot_approve(self, store) -> None:
        """expired approval cannot be approved."""
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        req = _make_approval(approval_id="apv_expired", expires_at=past)
        await store.create(req)
        with pytest.raises(ValueError, match="expired"):
            await store.approve("apv_expired", approved_by="mgr")

    @pytest.mark.asyncio
    async def test_expired_approval_cannot_reject(self, store) -> None:
        """expired approval cannot be rejected (treated as expired, not rejected)."""
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        req = _make_approval(approval_id="apv_expired", expires_at=past)
        await store.create(req)
        with pytest.raises(ValueError, match="expired"):
            await store.reject("apv_expired", rejected_by="mgr")

    @pytest.mark.asyncio
    async def test_expired_excluded_from_pending(self, store) -> None:
        """expired approval is not returned by list_pending."""
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        req_expired = _make_approval(approval_id="apv_expired", expires_at=past)
        req_active = _make_approval(approval_id="apv_active", expires_at=None)
        await store.create(req_expired)
        await store.create(req_active)
        pending = await store.list_pending()
        ids = [p.approval_id for p in pending]
        assert "apv_active" in ids
        assert "apv_expired" not in ids

    @pytest.mark.asyncio
    async def test_no_expiry_can_approve(self, store) -> None:
        """approval with no expires_at can still be approved."""
        req = _make_approval(approval_id="apv_no_expiry", expires_at=None)
        await store.create(req)
        updated = await store.approve("apv_no_expiry", approved_by="mgr")
        assert updated.status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_expiration_writes_audit_event(self, store) -> None:
        """attempting to approve an expired approval writes an audit event."""
        logger = InMemoryAuditLogger()
        store_with_audit = InMemoryApprovalStore(audit_logger=logger)
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        req = _make_approval(approval_id="apv_exp_audit", expires_at=past)
        await store_with_audit.create(req)
        with pytest.raises(ValueError, match="expired"):
            await store_with_audit.approve("apv_exp_audit", approved_by="mgr")
        events = logger.list_events(event_type="approval.expired")
        assert len(events) == 1
        assert events[0].approval_id == "apv_exp_audit"


class TestApprovalTTLSQLite:
    @pytest.fixture
    def store(self, tmp_path):
        db = str(tmp_path / "ttl_test.db")
        s = SQLiteApprovalStore(db_path=db)
        yield s
        s.close()

    @pytest.mark.asyncio
    async def test_sqlite_approval_with_future_expiry_can_approve(self, store) -> None:
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        req = _make_approval(approval_id="apv_sql_future", expires_at=future)
        await store.create(req)
        updated = await store.approve("apv_sql_future", approved_by="mgr")
        assert updated.status == ApprovalStatus.APPROVED

    @pytest.mark.asyncio
    async def test_sqlite_expired_approval_cannot_approve(self, store) -> None:
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        req = _make_approval(approval_id="apv_sql_expired", expires_at=past)
        await store.create(req)
        with pytest.raises(ValueError, match="expired"):
            await store.approve("apv_sql_expired", approved_by="mgr")

    @pytest.mark.asyncio
    async def test_sqlite_expired_excluded_from_pending(self, store) -> None:
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        req_expired = _make_approval(approval_id="apv_sql_exp", expires_at=past)
        req_active = _make_approval(approval_id="apv_sql_act", expires_at=None)
        await store.create(req_expired)
        await store.create(req_active)
        pending = await store.list_pending()
        ids = [p.approval_id for p in pending]
        assert "apv_sql_act" in ids
        assert "apv_sql_exp" not in ids
