"""Tests for RolloutStepApprovalStore -- Protocol, InMemory, SQLite, factory."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_app.governance.policy_rollout_approval import (
    RolloutApprovalDecision,
    RolloutApprovalDecisionType,
    RolloutApprovalPolicy,
    RolloutApprovalPolicyType,
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


# -- Phase 37: add_decision / expire_pending tests --


def _make_decision(
    decision_id: str = "rsd_001",
    approval_id: str = "rsa_001",
    decision_type: RolloutApprovalDecisionType = RolloutApprovalDecisionType.APPROVE,
    decided_by: str = "approver1",
    reason: str | None = None,
    roles: list[str] | None = None,
    permissions: list[str] | None = None,
) -> RolloutApprovalDecision:
    now = datetime.now(timezone.utc)
    return RolloutApprovalDecision(
        decision_id=decision_id,
        approval_id=approval_id,
        decision_type=decision_type,
        decided_by=decided_by,
        reason=reason,
        roles=roles or [],
        permissions=permissions or [],
        created_at=now,
    )


def _make_approval_with_policy(
    approval_id: str = "rsa_p37_001",
    rollout_id: str = "ro_p37",
    step_id: str = "step_1",
    bundle_id: str = "pb_p37",
    environment: str = "dev",
    ring_name: str | None = "canary",
    requested_by: str = "requester",
    requested_reason: str | None = None,
    status: RolloutStepApprovalStatus = RolloutStepApprovalStatus.PENDING,
    policy: RolloutApprovalPolicy | None = None,
    expires_at: datetime | None = None,
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
        policy=policy or RolloutApprovalPolicy(),
        expires_at=expires_at,
    )


class TestInMemoryApprovalStorePhase37:
    """Phase 37 tests for InMemoryRolloutStepApprovalStore: add_decision and expire_pending."""

    @pytest.mark.asyncio
    async def test_add_approve_decision(self):
        store = InMemoryRolloutStepApprovalStore()
        approval = _make_approval_with_policy(approval_id="rsa_p37_a1")
        await store.create(approval)
        decision = _make_decision(
            decision_id="rsd_a1",
            approval_id="rsa_p37_a1",
            decision_type=RolloutApprovalDecisionType.APPROVE,
            decided_by="approver1",
        )
        updated = await store.add_decision("rsa_p37_a1", decision)
        assert updated.status == RolloutStepApprovalStatus.APPROVED
        assert len(updated.decisions) == 1
        assert updated.decisions[0].decided_by == "approver1"
        assert updated.resolved_by is not None
        assert updated.resolved_at is not None

    @pytest.mark.asyncio
    async def test_add_reject_decision(self):
        store = InMemoryRolloutStepApprovalStore()
        approval = _make_approval_with_policy(approval_id="rsa_p37_r1")
        await store.create(approval)
        decision = _make_decision(
            decision_id="rsd_r1",
            approval_id="rsa_p37_r1",
            decision_type=RolloutApprovalDecisionType.REJECT,
            decided_by="rejector1",
        )
        updated = await store.add_decision("rsa_p37_r1", decision)
        assert updated.status == RolloutStepApprovalStatus.REJECTED
        assert len(updated.decisions) == 1
        assert updated.resolved_by is not None
        assert updated.resolved_at is not None

    @pytest.mark.asyncio
    async def test_duplicate_actor_decision_rejected(self):
        store = InMemoryRolloutStepApprovalStore()
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = _make_approval_with_policy(approval_id="rsa_p37_dup", policy=policy)
        await store.create(approval)
        decision1 = _make_decision(
            decision_id="rsd_dup1",
            approval_id="rsa_p37_dup",
            decided_by="actor_a",
        )
        await store.add_decision("rsa_p37_dup", decision1)
        decision2 = _make_decision(
            decision_id="rsd_dup2",
            approval_id="rsa_p37_dup",
            decided_by="actor_a",
        )
        with pytest.raises(ValueError, match="already"):
            await store.add_decision("rsa_p37_dup", decision2)

    @pytest.mark.asyncio
    async def test_quorum_approval_remains_pending(self):
        store = InMemoryRolloutStepApprovalStore()
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = _make_approval_with_policy(
            approval_id="rsa_p37_q1",
            policy=policy,
        )
        await store.create(approval)
        decision = _make_decision(
            decision_id="rsd_q1a",
            approval_id="rsa_p37_q1",
            decided_by="approver1",
        )
        updated = await store.add_decision("rsa_p37_q1", decision)
        assert updated.status == RolloutStepApprovalStatus.PENDING
        assert len(updated.decisions) == 1

    @pytest.mark.asyncio
    async def test_quorum_approval_becomes_approved(self):
        store = InMemoryRolloutStepApprovalStore()
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
        )
        approval = _make_approval_with_policy(
            approval_id="rsa_p37_q2",
            policy=policy,
        )
        await store.create(approval)
        decision1 = _make_decision(
            decision_id="rsd_q2a",
            approval_id="rsa_p37_q2",
            decided_by="approver1",
        )
        await store.add_decision("rsa_p37_q2", decision1)
        decision2 = _make_decision(
            decision_id="rsd_q2b",
            approval_id="rsa_p37_q2",
            decided_by="approver2",
        )
        updated = await store.add_decision("rsa_p37_q2", decision2)
        assert updated.status == RolloutStepApprovalStatus.APPROVED
        assert len(updated.decisions) == 2
        assert updated.resolved_at is not None

    @pytest.mark.asyncio
    async def test_already_resolved_cannot_receive_decision(self):
        store = InMemoryRolloutStepApprovalStore()
        approval = _make_approval_with_policy(approval_id="rsa_p37_resolved")
        await store.create(approval)
        await store.approve("rsa_p37_resolved", approved_by="admin")
        decision = _make_decision(
            decision_id="rsd_resolved",
            approval_id="rsa_p37_resolved",
            decided_by="approver2",
        )
        with pytest.raises(ValueError, match="PENDING"):
            await store.add_decision("rsa_p37_resolved", decision)

    @pytest.mark.asyncio
    async def test_expire_pending_marks_expired(self):
        store = InMemoryRolloutStepApprovalStore()
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        approval = _make_approval_with_policy(
            approval_id="rsa_p37_exp1",
            expires_at=past,
        )
        await store.create(approval)
        expired = await store.expire_pending()
        assert len(expired) == 1
        assert expired[0].approval_id == "rsa_p37_exp1"
        assert expired[0].status == RolloutStepApprovalStatus.EXPIRED
        assert expired[0].resolved_at is not None

    @pytest.mark.asyncio
    async def test_expire_pending_skips_non_expired(self):
        store = InMemoryRolloutStepApprovalStore()
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        approval = _make_approval_with_policy(
            approval_id="rsa_p37_skip",
            expires_at=future,
        )
        await store.create(approval)
        expired = await store.expire_pending()
        assert len(expired) == 0
        fetched = await store.get("rsa_p37_skip")
        assert fetched is not None
        assert fetched.status == RolloutStepApprovalStatus.PENDING

    @pytest.mark.asyncio
    async def test_expired_approval_cannot_receive_decision(self):
        store = InMemoryRolloutStepApprovalStore()
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        approval = _make_approval_with_policy(
            approval_id="rsa_p37_exp2",
            expires_at=past,
        )
        await store.create(approval)
        await store.expire_pending()
        decision = _make_decision(
            decision_id="rsd_exp2",
            approval_id="rsa_p37_exp2",
            decided_by="approver1",
        )
        with pytest.raises(ValueError, match="PENDING"):
            await store.add_decision("rsa_p37_exp2", decision)


class TestSQLiteApprovalStorePhase37:
    """Phase 37 tests for SQLiteRolloutStepApprovalStore: add_decision, expire_pending, policy persistence."""

    @pytest.mark.asyncio
    async def test_sqlite_add_decision(self, tmp_path):
        db = tmp_path / "p37_decision.db"
        store = SQLiteRolloutStepApprovalStore(str(db))
        approval = _make_approval_with_policy(approval_id="rsa_sql_dec1")
        await store.create(approval)
        decision = _make_decision(
            decision_id="rsd_sql1",
            approval_id="rsa_sql_dec1",
            decided_by="approver1",
        )
        updated = await store.add_decision("rsa_sql_dec1", decision)
        assert updated.status == RolloutStepApprovalStatus.APPROVED
        assert len(updated.decisions) == 1
        # Verify persistence
        fetched = await store.get("rsa_sql_dec1")
        assert fetched is not None
        assert fetched.status == RolloutStepApprovalStatus.APPROVED
        assert len(fetched.decisions) == 1
        assert fetched.decisions[0].decided_by == "approver1"
        store.close()

    @pytest.mark.asyncio
    async def test_sqlite_expire_pending(self, tmp_path):
        db = tmp_path / "p37_expire.db"
        store = SQLiteRolloutStepApprovalStore(str(db))
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        approval = _make_approval_with_policy(
            approval_id="rsa_sql_exp1",
            expires_at=past,
        )
        await store.create(approval)
        expired = await store.expire_pending()
        assert len(expired) == 1
        assert expired[0].status == RolloutStepApprovalStatus.EXPIRED
        # Verify persistence
        fetched = await store.get("rsa_sql_exp1")
        assert fetched is not None
        assert fetched.status == RolloutStepApprovalStatus.EXPIRED
        store.close()

    @pytest.mark.asyncio
    async def test_sqlite_policy_persistence(self, tmp_path):
        db = tmp_path / "p37_policy.db"
        store = SQLiteRolloutStepApprovalStore(str(db))
        policy = RolloutApprovalPolicy(
            policy_type=RolloutApprovalPolicyType.QUORUM,
            required_approvals=2,
            allowed_approver_roles=["admin", "reviewer"],
            prohibit_requester_approval=True,
            expires_after_seconds=3600,
        )
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        approval = _make_approval_with_policy(
            approval_id="rsa_sql_pol1",
            policy=policy,
            expires_at=future,
        )
        await store.create(approval)
        # Verify persistence
        fetched = await store.get("rsa_sql_pol1")
        assert fetched is not None
        assert fetched.policy.policy_type == RolloutApprovalPolicyType.QUORUM
        assert fetched.policy.required_approvals == 2
        assert fetched.policy.allowed_approver_roles == ["admin", "reviewer"]
        assert fetched.policy.prohibit_requester_approval is True
        assert fetched.policy.expires_after_seconds == 3600
        assert fetched.expires_at is not None
        # Add a decision and verify decisions persist
        decision = _make_decision(
            decision_id="rsd_sql_pol1",
            approval_id="rsa_sql_pol1",
            decided_by="approver1",
            roles=["admin"],
        )
        updated = await store.add_decision("rsa_sql_pol1", decision)
        assert updated.status == RolloutStepApprovalStatus.PENDING  # QUORUM needs 2
        fetched2 = await store.get("rsa_sql_pol1")
        assert fetched2 is not None
        assert len(fetched2.decisions) == 1
        assert fetched2.decisions[0].decided_by == "approver1"
        assert fetched2.decisions[0].roles == ["admin"]
        store.close()
