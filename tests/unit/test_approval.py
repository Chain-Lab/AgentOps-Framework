"""Tests for approval store and approval request model."""

import pytest

from agent_app.governance.approval import (
    ApprovalRequest,
    ApprovalStatus,
    InMemoryApprovalStore,
)
from agent_app.governance.risk import RiskLevel


def _make_approval(**kwargs):
    defaults = {
        "approval_id": "apv_test_001",
        "run_id": "run_001",
        "tool_name": "refund.request",
        "arguments": {"order_id": "123"},
        "risk_level": "high",
        "tenant_id": "t1",
    }
    defaults.update(kwargs)
    return ApprovalRequest(**defaults)


class TestApprovalRequest:
    def test_create_minimal(self) -> None:
        req = _make_approval()
        assert req.approval_id == "apv_test_001"
        assert req.tool_name == "refund.request"
        assert req.status == ApprovalStatus.PENDING
        assert req.arguments == {"order_id": "123"}

    def test_default_status_pending(self) -> None:
        req = _make_approval()
        assert req.status == "pending"

    def test_has_created_at(self) -> None:
        req = _make_approval()
        assert req.created_at is not None


class TestInMemoryApprovalStore:
    @pytest.fixture
    def store(self) -> InMemoryApprovalStore:
        return InMemoryApprovalStore()

    @pytest.mark.asyncio
    async def test_create_and_get(self, store) -> None:
        req = _make_approval(approval_id="apv_1")
        created = await store.create(req)
        assert created.approval_id == "apv_1"
        fetched = await store.get("apv_1")
        assert fetched.tool_name == "refund.request"

    @pytest.mark.asyncio
    async def test_duplicate_create_raises(self, store) -> None:
        req = _make_approval(approval_id="apv_1")
        await store.create(req)
        with pytest.raises(ValueError, match="already exists"):
            await store.create(req)

    @pytest.mark.asyncio
    async def test_get_missing_raises(self, store) -> None:
        with pytest.raises(KeyError, match="not found"):
            await store.get("nonexistent")

    @pytest.mark.asyncio
    async def test_approve(self, store) -> None:
        req = _make_approval(approval_id="apv_1")
        await store.create(req)
        updated = await store.approve("apv_1", approved_by="manager_001")
        assert updated.status == ApprovalStatus.APPROVED
        assert updated.resolved_by == "manager_001"
        assert updated.resolved_at is not None

    @pytest.mark.asyncio
    async def test_reject(self, store) -> None:
        req = _make_approval(approval_id="apv_1")
        await store.create(req)
        updated = await store.reject("apv_1", rejected_by="mgr", reason="Too risky")
        assert updated.status == ApprovalStatus.REJECTED
        assert updated.reason == "Too risky"

    @pytest.mark.asyncio
    async def test_cannot_approve_twice(self, store) -> None:
        req = _make_approval(approval_id="apv_1")
        await store.create(req)
        await store.approve("apv_1", approved_by="mgr")
        with pytest.raises(ValueError, match="already"):
            await store.approve("apv_1", approved_by="mgr2")

    @pytest.mark.asyncio
    async def test_cannot_reject_twice(self, store) -> None:
        req = _make_approval(approval_id="apv_1")
        await store.create(req)
        await store.reject("apv_1", rejected_by="mgr")
        with pytest.raises(ValueError, match="already"):
            await store.reject("apv_1", rejected_by="mgr2")

    @pytest.mark.asyncio
    async def test_list_pending_empty(self, store) -> None:
        assert await store.list_pending() == []

    @pytest.mark.asyncio
    async def test_list_pending(self, store) -> None:
        req1 = _make_approval(approval_id="apv_1", tenant_id="t1")
        req2 = _make_approval(approval_id="apv_2", tenant_id="t1")
        req3 = _make_approval(approval_id="apv_3", tenant_id="t2")
        for r in [req1, req2, req3]:
            await store.create(r)

        pending = await store.list_pending()
        assert len(pending) == 3

        pending_t1 = await store.list_pending(tenant_id="t1")
        assert len(pending_t1) == 2

        pending_t2 = await store.list_pending(tenant_id="t2")
        assert len(pending_t2) == 1

    @pytest.mark.asyncio
    async def test_approved_excluded_from_pending(self, store) -> None:
        req = _make_approval(approval_id="apv_1")
        await store.create(req)
        await store.approve("apv_1", approved_by="mgr")
        pending = await store.list_pending()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_list_pending_sorted_by_created_at(self, store) -> None:
        import time as _time
        req1 = _make_approval(approval_id="apv_1")
        await store.create(req1)
        _time.sleep(0.01)
        req2 = _make_approval(approval_id="apv_2")
        await store.create(req2)

        pending = await store.list_pending()
        assert pending[0].approval_id == "apv_1"
        assert pending[1].approval_id == "apv_2"
