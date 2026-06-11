"""Tests for PromotionRequestStore — InMemory + SQLite backends.

Phase 30 Task 3: promotion request persistence store.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_app.governance.policy_promotion import (
    PromotionRequest,
    PromotionRequestStatus,
)
from agent_app.runtime.promotion_store import (
    InMemoryPromotionRequestStore,
    PromotionRequestStore,
    SQLitePromotionRequestStore,
    create_promotion_store,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(
    promotion_id: str = "pr_test_001",
    bundle_id: str = "bundle_1",
    tenant_id: str | None = None,
) -> PromotionRequest:
    """Build a minimal PromotionRequest with deterministic timestamps."""
    now = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    return PromotionRequest(
        promotion_id=promotion_id,
        bundle_id=bundle_id,
        requested_by="alice",
        tenant_id=tenant_id,
        status=PromotionRequestStatus.PENDING,
        reason="please promote",
        created_at=now,
    )


# ===========================================================================
# InMemoryPromotionRequestStore tests
# ===========================================================================


class TestInMemoryPromotionRequestStore:
    """9 tests for the in-memory store."""

    @pytest.fixture
    def store(self) -> InMemoryPromotionRequestStore:
        return InMemoryPromotionRequestStore()

    # -- basic CRUD ---------------------------------------------------------

    async def test_create_and_get(self, store: InMemoryPromotionRequestStore):
        req = _make_request()
        result = await store.create(req)
        assert result is req

        fetched = await store.get("pr_test_001")
        assert fetched is not None
        assert fetched.promotion_id == "pr_test_001"
        assert fetched.bundle_id == "bundle_1"
        assert fetched.requested_by == "alice"
        assert fetched.tenant_id is None
        assert fetched.status == PromotionRequestStatus.PENDING
        assert fetched.reason == "please promote"

    async def test_get_missing_returns_none(self, store: InMemoryPromotionRequestStore):
        result = await store.get("pr_nonexistent")
        assert result is None

    # -- lifecycle transitions -----------------------------------------------

    async def test_approve(self, store: InMemoryPromotionRequestStore):
        req = _make_request()
        await store.create(req)

        approver = "bob"
        approval_reason = "looks good"
        resolved_at = datetime(2025, 1, 2, 10, 0, 0, tzinfo=timezone.utc)

        updated = await store.approve(
            "pr_test_001", approved_by=approver, reason=approval_reason
        )
        assert updated.status == PromotionRequestStatus.APPROVED
        assert updated.resolved_by == approver
        assert updated.approval_reason == approval_reason
        assert updated.resolved_at is not None

    async def test_reject(self, store: InMemoryPromotionRequestStore):
        req = _make_request()
        await store.create(req)

        rejecter = "charlie"
        rejection_reason = "not ready yet"
        resolved_at = datetime(2025, 1, 2, 10, 0, 0, tzinfo=timezone.utc)

        updated = await store.reject(
            "pr_test_001", rejected_by=rejecter, reason=rejection_reason
        )
        assert updated.status == PromotionRequestStatus.REJECTED
        assert updated.resolved_by == rejecter
        assert updated.rejection_reason == rejection_reason
        assert updated.resolved_at is not None

    async def test_mark_executed(self, store: InMemoryPromotionRequestStore):
        req = _make_request()
        await store.create(req)

        # First approve
        await store.approve("pr_test_001", approved_by="bob", reason="ok")
        executor = "dave"
        executed_at = datetime(2025, 1, 3, 8, 0, 0, tzinfo=timezone.utc)

        updated = await store.mark_executed("pr_test_001", executed_by=executor)
        assert updated.status == PromotionRequestStatus.EXECUTED
        assert updated.executed_by == executor
        assert updated.executed_at is not None

    # -- no-op / idempotency ------------------------------------------------

    async def test_cannot_approve_twice(self, store: InMemoryPromotionRequestStore):
        req = _make_request()
        await store.create(req)

        await store.approve("pr_test_001", approved_by="bob", reason="first")
        # Second approve should be a no-op — status stays APPROVED
        updated = await store.approve("pr_test_001", approved_by="carol", reason="second")
        assert updated.status == PromotionRequestStatus.APPROVED
        assert updated.resolved_by == "bob"  # original approver preserved

    # -- list filtering ------------------------------------------------------

    async def test_list_empty(self, store: InMemoryPromotionRequestStore):
        results = await store.list()
        assert results == []

    async def test_list_by_status(self, store: InMemoryPromotionRequestStore):
        r1 = _make_request("pr_1", "bundle_1")
        r2 = _make_request("pr_2", "bundle_2")
        r3 = _make_request("pr_3", "bundle_3")
        r3.status = PromotionRequestStatus.APPROVED
        await store.create(r1)
        await store.create(r2)
        await store.create(r3)

        pending = await store.list(status=PromotionRequestStatus.PENDING)
        assert len(pending) == 2
        assert {r.promotion_id for r in pending} == {"pr_1", "pr_2"}

        approved = await store.list(status=PromotionRequestStatus.APPROVED)
        assert len(approved) == 1
        assert approved[0].promotion_id == "pr_3"

    async def test_list_by_tenant_id(self, store: InMemoryPromotionRequestStore):
        r1 = _make_request("pr_1", "bundle_1", tenant_id="tenant_a")
        r2 = _make_request("pr_2", "bundle_2", tenant_id="tenant_b")
        r3 = _make_request("pr_3", "bundle_3", tenant_id="tenant_a")
        await store.create(r1)
        await store.create(r2)
        await store.create(r3)

        results = await store.list(tenant_id="tenant_a")
        assert len(results) == 2
        assert {r.promotion_id for r in results} == {"pr_1", "pr_3"}

    async def test_create_overwrites(self, store: InMemoryPromotionRequestStore):
        req1 = _make_request("pr_same", "bundle_1")
        req1.reason = "original"
        await store.create(req1)

        req2 = _make_request("pr_same", "bundle_2")
        req2.reason = "overwritten"
        await store.create(req2)

        fetched = await store.get("pr_same")
        assert fetched is not None
        assert fetched.bundle_id == "bundle_2"
        assert fetched.reason == "overwritten"


# ===========================================================================
# SQLitePromotionRequestStore tests
# ===========================================================================


class TestSQLitePromotionRequestStore:
    """5 tests for the SQLite-backed store."""

    @pytest.fixture
    def db_path(self, tmp_path: Path) -> str:
        return str(tmp_path / "promotion_requests.db")

    async def test_create_and_get(self, db_path: str):
        store = SQLitePromotionRequestStore(db_path)
        try:
            req = _make_request()
            await store.create(req)
            fetched = await store.get("pr_test_001")
            assert fetched is not None
            assert fetched.promotion_id == "pr_test_001"
            assert fetched.bundle_id == "bundle_1"
            assert fetched.requested_by == "alice"
            assert fetched.status == PromotionRequestStatus.PENDING
        finally:
            store.close()

    async def test_approve_and_reject(self, db_path: str):
        """Approve a request, then try to reject — should be no-op."""
        store = SQLitePromotionRequestStore(db_path)
        try:
            req = _make_request()
            await store.create(req)

            approved = await store.approve(
                "pr_test_001", approved_by="bob", reason="ok"
            )
            assert approved.status == PromotionRequestStatus.APPROVED
            assert approved.resolved_by == "bob"

            # Try to reject after approval — should be no-op
            rejected = await store.reject(
                "pr_test_001", rejected_by="carol", reason="nope"
            )
            assert rejected.status == PromotionRequestStatus.APPROVED
            assert rejected.resolved_by == "bob"  # original approver preserved
        finally:
            store.close()

    async def test_mark_executed(self, db_path: str):
        store = SQLitePromotionRequestStore(db_path)
        try:
            req = _make_request()
            await store.create(req)
            await store.approve("pr_test_001", approved_by="bob", reason="ok")

            executed = await store.mark_executed("pr_test_001", executed_by="dave")
            assert executed.status == PromotionRequestStatus.EXECUTED
            assert executed.executed_by == "dave"
            assert executed.executed_at is not None
        finally:
            store.close()

    async def test_list_with_filters(self, db_path: str):
        store = SQLitePromotionRequestStore(db_path)
        try:
            r1 = _make_request("pr_1", "bundle_1", tenant_id="tenant_a")
            r2 = _make_request("pr_2", "bundle_2", tenant_id="tenant_b")
            r3 = _make_request("pr_3", "bundle_3", tenant_id="tenant_a")
            r3.status = PromotionRequestStatus.APPROVED
            await store.create(r1)
            await store.create(r2)
            await store.create(r3)

            # Filter by status
            pending = await store.list(status=PromotionRequestStatus.PENDING)
            assert len(pending) == 2

            # Filter by tenant_id
            tenant_a = await store.list(tenant_id="tenant_a")
            assert len(tenant_a) == 2

            # Combined filters
            combo = await store.list(
                status=PromotionRequestStatus.PENDING, tenant_id="tenant_a"
            )
            assert len(combo) == 1
            assert combo[0].promotion_id == "pr_1"
        finally:
            store.close()

    async def test_persists_across_instances(self, db_path: str):
        # Write with store1
        store1 = SQLitePromotionRequestStore(db_path)
        try:
            req = _make_request()
            await store1.create(req)
            await store1.approve("pr_test_001", approved_by="bob", reason="ok")
        finally:
            store1.close()

        # Read with store2
        store2 = SQLitePromotionRequestStore(db_path)
        try:
            fetched = await store2.get("pr_test_001")
            assert fetched is not None
            assert fetched.status == PromotionRequestStatus.APPROVED
            assert fetched.resolved_by == "bob"
        finally:
            store2.close()


# ===========================================================================
# Factory tests
# ===========================================================================


class TestCreatePromotionStoreFactory:
    """3 tests for the factory function."""

    async def test_memory_store(self):
        store = create_promotion_store("memory")
        assert isinstance(store, InMemoryPromotionRequestStore)

    async def test_sqlite_store(self, tmp_path: Path):
        db_path = str(tmp_path / "test.db")
        store = create_promotion_store("sqlite", db_path=db_path)
        assert isinstance(store, SQLitePromotionRequestStore)
        store.close()

    async def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            create_promotion_store("redis")
