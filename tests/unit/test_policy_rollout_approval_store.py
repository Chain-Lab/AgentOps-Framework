"""Tests for RolloutStepApprovalStore -- Protocol, InMemory, SQLite, factory."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout_approval import (
    RolloutStepApproval,
    RolloutStepApprovalStatus,
)
from agent_app.runtime.policy_rollout_approval_store import (
    InMemoryRolloutStepApprovalStore,
    RolloutStepApprovalStore,
    SQLiteRolloutStepApprovalStore,
    create_rollout_step_approval_store,
)


def _make_approval(
    approval_id: str = "rsa_001",
    rollout_id: str = "ro_test001",
    step_id: str = "step_1",
    bundle_id: str = "pb_test001",
    environment: str = "dev",
    ring_name: str | None = "canary",
    requested_by: str = "test_user",
    requested_reason: str | None = None,
    status: RolloutStepApprovalStatus = RolloutStepApprovalStatus.PENDING,
) -> RolloutStepApproval:
    now = datetime.now(timezone.utc)
    return RolloutStepApproval(
        approval_id=approval_id,
        rollout_id=rollout_id,
        step_id=step_id,
        bundle_id=bundle_id,
        environment=environment,
        ring_name=ring_name,
        requested_by=requested_by,
        requested_reason=requested_reason,
        status=status,
        created_at=now,
    )


# -- InMemory tests --


class TestInMemoryRolloutStepApprovalStore:
    @pytest.mark.asyncio
    async def test_create_and_get(self):
        store = InMemoryRolloutStepApprovalStore()
        approval = _make_approval(approval_id="rsa_001")
        created = await store.create(approval)
        assert created.approval_id == "rsa_001"
        fetched = await store.get("rsa_001")
        assert fetched is not None
        assert fetched.approval_id == "rsa_001"
        assert fetched.rollout_id == "ro_test001"
        # Missing id returns None
        assert await store.get("rsa_nonexistent") is None

    @pytest.mark.asyncio
    async def test_duplicate_pending_returns_existing(self):
        store = InMemoryRolloutStepApprovalStore()
        a1 = _make_approval(approval_id="rsa_100", rollout_id="ro_dup", step_id="step_1")
        created1 = await store.create(a1)
        assert created1.approval_id == "rsa_100"
        # Second approval for same rollout_id + step_id while PENDING
        a2 = _make_approval(approval_id="rsa_101", rollout_id="ro_dup", step_id="step_1")
        created2 = await store.create(a2)
        # Should return the existing one, not the new one
        assert created2.approval_id == "rsa_100"

    @pytest.mark.asyncio
    async def test_approve(self):
        store = InMemoryRolloutStepApprovalStore()
        approval = _make_approval(approval_id="rsa_200")
        await store.create(approval)
        approved = await store.approve("rsa_200", approved_by="admin", reason="Looks good")
        assert approved.status == RolloutStepApprovalStatus.APPROVED
        assert approved.resolved_by == "admin"
        assert approved.resolved_reason == "Looks good"
        assert approved.resolved_at is not None

    @pytest.mark.asyncio
    async def test_reject(self):
        store = InMemoryRolloutStepApprovalStore()
        approval = _make_approval(approval_id="rsa_300")
        await store.create(approval)
        rejected = await store.reject("rsa_300", rejected_by="admin", reason="Not ready")
        assert rejected.status == RolloutStepApprovalStatus.REJECTED
        assert rejected.resolved_by == "admin"
        assert rejected.resolved_reason == "Not ready"
        assert rejected.resolved_at is not None

    @pytest.mark.asyncio
    async def test_cannot_approve_twice(self):
        store = InMemoryRolloutStepApprovalStore()
        approval = _make_approval(approval_id="rsa_400")
        await store.create(approval)
        await store.approve("rsa_400", approved_by="admin")
        with pytest.raises(ValueError, match="PENDING"):
            await store.approve("rsa_400", approved_by="admin2")

    @pytest.mark.asyncio
    async def test_cannot_reject_approved(self):
        store = InMemoryRolloutStepApprovalStore()
        approval = _make_approval(approval_id="rsa_500")
        await store.create(approval)
        await store.approve("rsa_500", approved_by="admin")
        with pytest.raises(ValueError, match="PENDING"):
            await store.reject("rsa_500", rejected_by="admin2")

    @pytest.mark.asyncio
    async def test_cancel_for_step(self):
        store = InMemoryRolloutStepApprovalStore()
        approval = _make_approval(approval_id="rsa_600", rollout_id="ro_cancel", step_id="step_1")
        await store.create(approval)
        cancelled = await store.cancel_for_step("ro_cancel", "step_1", cancelled_by="system", reason="Aborted")
        assert cancelled is not None
        assert cancelled.status == RolloutStepApprovalStatus.CANCELLED
        assert cancelled.resolved_by == "system"
        assert cancelled.resolved_reason == "Aborted"
        assert cancelled.resolved_at is not None
        # No pending approval for that step now
        assert await store.get_pending_for_step("ro_cancel", "step_1") is None

    @pytest.mark.asyncio
    async def test_list_by_status(self):
        store = InMemoryRolloutStepApprovalStore()
        a1 = _make_approval(approval_id="rsa_l1", step_id="step_1", status=RolloutStepApprovalStatus.PENDING)
        a2 = _make_approval(approval_id="rsa_l2", step_id="step_2", status=RolloutStepApprovalStatus.APPROVED)
        a3 = _make_approval(approval_id="rsa_l3", step_id="step_3", status=RolloutStepApprovalStatus.PENDING)
        await store.create(a1)
        await store.create(a2)
        await store.create(a3)
        pending = await store.list(status=RolloutStepApprovalStatus.PENDING)
        assert len(pending) == 2
        assert all(a.status == RolloutStepApprovalStatus.PENDING for a in pending)
        approved = await store.list(status=RolloutStepApprovalStatus.APPROVED)
        assert len(approved) == 1

    @pytest.mark.asyncio
    async def test_list_by_rollout_id(self):
        store = InMemoryRolloutStepApprovalStore()
        a1 = _make_approval(approval_id="rsa_r1", rollout_id="ro_alpha", step_id="step_1")
        a2 = _make_approval(approval_id="rsa_r2", rollout_id="ro_beta", step_id="step_1")
        a3 = _make_approval(approval_id="rsa_r3", rollout_id="ro_alpha", step_id="step_2")
        await store.create(a1)
        await store.create(a2)
        await store.create(a3)
        alpha = await store.list(rollout_id="ro_alpha")
        assert len(alpha) == 2
        assert all(a.rollout_id == "ro_alpha" for a in alpha)
        beta = await store.list(rollout_id="ro_beta")
        assert len(beta) == 1


# -- SQLite tests --


class TestSQLiteRolloutStepApprovalStore:
    @pytest.mark.asyncio
    async def test_sqlite_persistence(self, tmp_path):
        db = tmp_path / "approval_store.db"
        s1 = SQLiteRolloutStepApprovalStore(str(db))
        approval = _make_approval(approval_id="rsa_persist", rollout_id="ro_persist", step_id="step_p")
        await s1.create(approval)
        s1.close()
        # Read with a new instance
        s2 = SQLiteRolloutStepApprovalStore(str(db))
        fetched = await s2.get("rsa_persist")
        assert fetched is not None
        assert fetched.approval_id == "rsa_persist"
        assert fetched.rollout_id == "ro_persist"
        assert fetched.step_id == "step_p"
        assert fetched.status == RolloutStepApprovalStatus.PENDING
        s2.close()


# -- Factory tests --


def test_factory_memory():
    store = create_rollout_step_approval_store("memory")
    assert isinstance(store, InMemoryRolloutStepApprovalStore)


def test_factory_sqlite(tmp_path):
    store = create_rollout_step_approval_store("sqlite", str(tmp_path / "approval.db"))
    assert isinstance(store, SQLiteRolloutStepApprovalStore)
    store.close()


def test_factory_sqlite_requires_db_path():
    with pytest.raises(ValueError, match="db_path is required"):
        create_rollout_step_approval_store("sqlite")


def test_factory_unknown():
    with pytest.raises(ValueError, match="Unknown rollout step approval store type"):
        create_rollout_step_approval_store("redis")
