"""Tests for SQLiteApprovalStore."""

import os
import pytest

from agent_app.governance.approval import ApprovalRequest, ApprovalStatus
from agent_app.runtime.approval_store import SQLiteApprovalStore


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


class TestSQLiteApprovalStore:
    @pytest.fixture
    def db_path(self, tmp_path):
        return str(tmp_path / "test_approvals.db")

    @pytest.fixture
    def store(self, db_path):
        return SQLiteApprovalStore(db_path=db_path)

    @pytest.mark.asyncio
    async def test_creates_db_file(self, db_path):
        SQLiteApprovalStore(db_path=db_path)
        assert os.path.exists(db_path)

    @pytest.mark.asyncio
    async def test_create_and_get(self, store):
        req = _make_approval(approval_id="apv_1")
        created = await store.create(req)
        assert created.approval_id == "apv_1"
        fetched = await store.get("apv_1")
        assert fetched.tool_name == "refund.request"
        assert fetched.arguments == {"order_id": "123"}

    @pytest.mark.asyncio
    async def test_get_missing_raises(self, store):
        with pytest.raises(KeyError, match="not found"):
            await store.get("nonexistent")

    @pytest.mark.asyncio
    async def test_approve(self, store):
        req = _make_approval(approval_id="apv_1")
        await store.create(req)
        updated = await store.approve("apv_1", approved_by="mgr")
        assert updated.status == ApprovalStatus.APPROVED
        assert updated.resolved_by == "mgr"
        assert updated.resolved_at is not None

    @pytest.mark.asyncio
    async def test_reject(self, store):
        req = _make_approval(approval_id="apv_1")
        await store.create(req)
        updated = await store.reject("apv_1", rejected_by="mgr", reason="Too risky")
        assert updated.status == ApprovalStatus.REJECTED
        assert updated.reason == "Too risky"

    @pytest.mark.asyncio
    async def test_cannot_approve_twice(self, store):
        req = _make_approval(approval_id="apv_1")
        await store.create(req)
        await store.approve("apv_1", "mgr")
        with pytest.raises(ValueError, match="already"):
            await store.approve("apv_1", "mgr2")

    @pytest.mark.asyncio
    async def test_cannot_reject_twice(self, store):
        req = _make_approval(approval_id="apv_1")
        await store.create(req)
        await store.reject("apv_1", "mgr")
        with pytest.raises(ValueError, match="already"):
            await store.reject("apv_1", "mgr2")

    @pytest.mark.asyncio
    async def test_list_pending(self, store):
        req1 = _make_approval(approval_id="apv_1", tenant_id="t1")
        req2 = _make_approval(approval_id="apv_2", tenant_id="t1")
        req3 = _make_approval(approval_id="apv_3", tenant_id="t2")
        for r in [req1, req2, req3]:
            await store.create(r)
        pending = await store.list_pending()
        assert len(pending) == 3

    @pytest.mark.asyncio
    async def test_list_pending_tenant_filter(self, store):
        req1 = _make_approval(approval_id="apv_1", tenant_id="t1")
        req2 = _make_approval(approval_id="apv_2", tenant_id="t2")
        await store.create(req1)
        await store.create(req2)
        pending_t1 = await store.list_pending(tenant_id="t1")
        assert len(pending_t1) == 1

    @pytest.mark.asyncio
    async def test_persists_across_instances(self, db_path):
        store1 = SQLiteApprovalStore(db_path=db_path)
        await store1.create(_make_approval(approval_id="apv_1"))
        store1.close()

        store2 = SQLiteApprovalStore(db_path=db_path)
        fetched = await store2.get("apv_1")
        assert fetched.tool_name == "refund.request"
        store2.close()


@pytest.mark.asyncio
async def test_sqlite_approval_store_persists_metadata_decision_note_and_expiry(tmp_path) -> None:
    from datetime import datetime, timezone

    db_path = tmp_path / "approvals.db"
    store = SQLiteApprovalStore(str(db_path))
    expires_at = datetime(2026, 6, 9, 12, 30, tzinfo=timezone.utc)
    request = ApprovalRequest(
        approval_id="apv_sql_meta",
        run_id="run-1",
        tool_name="billing.charge",
        arguments={"api_token": "[redacted]"},
        metadata={"sdk_call_id": "call-1", "argument_keys": ["api_token"]},
        decision_note="reviewed",
        expires_at=expires_at,
    )

    await store.create(request)
    loaded = await store.get("apv_sql_meta")

    assert loaded.metadata == {"sdk_call_id": "call-1", "argument_keys": ["api_token"]}
    assert loaded.decision_note == "reviewed"
    assert loaded.expires_at == expires_at
    store.close()
