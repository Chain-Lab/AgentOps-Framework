"""Tests for RolloutPlanStore -- Protocol, InMemory, SQLite, factory."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepStatus,
    RolloutStepType,
)
from agent_app.runtime.policy_rollout_store import (
    InMemoryRolloutPlanStore,
    RolloutPlanStore,
    SQLiteRolloutPlanStore,
    create_rollout_plan_store,
)


def _make_plan(
    rollout_id: str = "ro_test001",
    name: str = "test_rollout",
    bundle_id: str = "pb_test001",
    status: RolloutPlanStatus = RolloutPlanStatus.DRAFT,
    created_by: str = "test_user",
) -> RolloutPlan:
    steps = [
        RolloutStep(
            step_id="step_1",
            step_type=RolloutStepType.ACTIVATE,
            environment="dev",
            ring_name="stable",
        ),
        RolloutStep(
            step_id="step_2",
            step_type=RolloutStepType.PROMOTE_RING,
            environment="prod",
            from_ring="canary",
            to_ring="stable",
            require_previous_step="step_1",
        ),
    ]
    now = datetime.now(timezone.utc)
    return RolloutPlan(
        rollout_id=rollout_id,
        name=name,
        bundle_id=bundle_id,
        status=status,
        steps=steps,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )


# -- InMemory tests --


class TestInMemoryRolloutPlanStore:
    @pytest.mark.asyncio
    async def test_in_memory_create_get(self):
        store = InMemoryRolloutPlanStore()
        plan = _make_plan(rollout_id="ro_001")
        created = await store.create(plan)
        assert created.rollout_id == "ro_001"
        fetched = await store.get("ro_001")
        assert fetched is not None
        assert fetched.rollout_id == "ro_001"
        assert fetched.name == "test_rollout"
        # Missing id returns None
        assert await store.get("ro_nonexistent") is None

    @pytest.mark.asyncio
    async def test_in_memory_update(self):
        store = InMemoryRolloutPlanStore()
        plan = _make_plan(rollout_id="ro_002", status=RolloutPlanStatus.DRAFT)
        await store.create(plan)
        # Update status
        plan.status = RolloutPlanStatus.ACTIVE
        updated = await store.update(plan)
        assert updated.status == RolloutPlanStatus.ACTIVE
        # Verify via get
        fetched = await store.get("ro_002")
        assert fetched is not None
        assert fetched.status == RolloutPlanStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_in_memory_list_by_status(self):
        store = InMemoryRolloutPlanStore()
        await store.create(_make_plan(rollout_id="ro_s1", status=RolloutPlanStatus.DRAFT))
        await store.create(_make_plan(rollout_id="ro_s2", status=RolloutPlanStatus.ACTIVE))
        await store.create(_make_plan(rollout_id="ro_s3", status=RolloutPlanStatus.DRAFT))
        draft_plans = await store.list(status=RolloutPlanStatus.DRAFT)
        assert len(draft_plans) == 2
        assert all(p.status == RolloutPlanStatus.DRAFT for p in draft_plans)
        active_plans = await store.list(status=RolloutPlanStatus.ACTIVE)
        assert len(active_plans) == 1

    @pytest.mark.asyncio
    async def test_in_memory_list_by_bundle_id(self):
        store = InMemoryRolloutPlanStore()
        await store.create(_make_plan(rollout_id="ro_b1", bundle_id="pb_alpha"))
        await store.create(_make_plan(rollout_id="ro_b2", bundle_id="pb_beta"))
        await store.create(_make_plan(rollout_id="ro_b3", bundle_id="pb_alpha"))
        alpha_plans = await store.list(bundle_id="pb_alpha")
        assert len(alpha_plans) == 2
        assert all(p.bundle_id == "pb_alpha" for p in alpha_plans)
        beta_plans = await store.list(bundle_id="pb_beta")
        assert len(beta_plans) == 1

    @pytest.mark.asyncio
    async def test_in_memory_list_all(self):
        store = InMemoryRolloutPlanStore()
        await store.create(_make_plan(rollout_id="ro_a1", status=RolloutPlanStatus.DRAFT))
        await store.create(_make_plan(rollout_id="ro_a2", status=RolloutPlanStatus.ACTIVE))
        await store.create(_make_plan(rollout_id="ro_a3", status=RolloutPlanStatus.COMPLETED))
        all_plans = await store.list()
        assert len(all_plans) == 3


# -- SQLite tests --


class TestSQLiteRolloutPlanStore:
    @pytest.mark.asyncio
    async def test_sqlite_persistence(self, tmp_path):
        db = tmp_path / "rollout_plans.db"
        s1 = SQLiteRolloutPlanStore(str(db))
        plan = _make_plan(rollout_id="ro_persist", bundle_id="pb_persist")
        await s1.create(plan)
        s1.close()
        # Read with a new instance
        s2 = SQLiteRolloutPlanStore(str(db))
        fetched = await s2.get("ro_persist")
        assert fetched is not None
        assert fetched.rollout_id == "ro_persist"
        assert fetched.bundle_id == "pb_persist"
        assert len(fetched.steps) == 2
        assert fetched.steps[0].step_type == RolloutStepType.ACTIVATE
        assert fetched.steps[1].step_type == RolloutStepType.PROMOTE_RING
        s2.close()


# -- Factory tests --


def test_factory_memory():
    store = create_rollout_plan_store("memory")
    assert isinstance(store, InMemoryRolloutPlanStore)


def test_factory_sqlite(tmp_path):
    store = create_rollout_plan_store("sqlite", str(tmp_path / "rollout.db"))
    assert isinstance(store, SQLiteRolloutPlanStore)
    store.close()


def test_factory_sqlite_requires_db_path():
    with pytest.raises(ValueError, match="db_path is required"):
        create_rollout_plan_store("sqlite")


def test_factory_unknown():
    with pytest.raises(ValueError, match="Unknown rollout store type"):
        create_rollout_plan_store("redis")
