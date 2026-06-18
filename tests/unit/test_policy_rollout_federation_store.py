from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout import RolloutStep, RolloutStepType
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedTargetStatus,
)
from agent_app.runtime.policy_rollout_federation_store import (
    FederatedRolloutPlanStore,
    FederatedRolloutTargetStore,
    InMemoryFederatedRolloutPlanStore,
    InMemoryFederatedRolloutTargetStore,
    SQLiteFederatedRolloutPlanStore,
    SQLiteFederatedRolloutTargetStore,
    create_federated_rollout_plan_store,
    create_federated_rollout_target_store,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _target(target_id: str = "frt_a", environment: str = "prod", ring_name: str | None = "canary") -> FederatedRolloutTarget:
    return FederatedRolloutTarget(
        target_id=target_id,
        name=target_id,
        tenant_id="tenant_a",
        environment=environment,
        ring_name=ring_name,
        region="us-east",
        labels={"tier": "gold"},
        created_at=_now(),
    )


def _step() -> RolloutStep:
    return RolloutStep(
        step_id="step_activate",
        step_type=RolloutStepType.ACTIVATE,
        environment="prod",
        ring_name="canary",
    )


def _plan(federation_id: str = "frp_a", bundle_id: str = "pb_123") -> FederatedRolloutPlan:
    return FederatedRolloutPlan(
        federation_id=federation_id,
        name=federation_id,
        bundle_id=bundle_id,
        target_ids=["frt_a"],
        rollout_template_steps=[_step()],
        created_by="release_manager",
        created_at=_now(),
        updated_at=_now(),
    )


@pytest.mark.asyncio
class TestInMemoryTargetStore:
    async def test_create_get_list_target(self) -> None:
        store = InMemoryFederatedRolloutTargetStore()
        target = await store.create(_target())

        assert await store.get("frt_a") == target
        assert await store.list() == [target]
        assert await store.list(tenant_id="tenant_a") == [target]
        assert await store.list(environment="prod") == [target]
        assert await store.list(ring_name="canary") == [target]
        assert await store.list(status=FederatedTargetStatus.ENABLED) == [target]
        assert await store.list(environment="staging") == []

    async def test_enable_disable_target(self) -> None:
        store = InMemoryFederatedRolloutTargetStore()
        await store.create(_target())

        disabled = await store.disable("frt_a")
        assert disabled.status == FederatedTargetStatus.DISABLED
        assert (await store.get("frt_a")).status == FederatedTargetStatus.DISABLED

        enabled = await store.enable("frt_a")
        assert enabled.status == FederatedTargetStatus.ENABLED

    async def test_enable_missing_target_raises_key_error(self) -> None:
        store = InMemoryFederatedRolloutTargetStore()

        with pytest.raises(KeyError, match="frt_missing"):
            await store.enable("frt_missing")


@pytest.mark.asyncio
class TestSQLiteTargetStore:
    async def test_sqlite_target_persists_across_instances(self, tmp_path) -> None:
        db_path = tmp_path / "targets.db"
        store = SQLiteFederatedRolloutTargetStore(str(db_path))
        await store.create(_target())
        store.close()

        reopened = SQLiteFederatedRolloutTargetStore(str(db_path))
        loaded = await reopened.get("frt_a")

        assert loaded is not None
        assert loaded.target_id == "frt_a"
        assert loaded.labels == {"tier": "gold"}
        assert loaded.status == FederatedTargetStatus.ENABLED
        reopened.close()


@pytest.mark.asyncio
class TestInMemoryPlanStore:
    async def test_create_get_update_list_plan(self) -> None:
        store = InMemoryFederatedRolloutPlanStore()
        plan = await store.create(_plan())

        assert await store.get("frp_a") == plan
        assert await store.list() == [plan]
        assert await store.list(status=FederatedRolloutPlanStatus.DRAFT) == [plan]
        assert await store.list(bundle_id="pb_123") == [plan]
        assert await store.list(bundle_id="pb_missing") == []

        updated = plan.model_copy(update={"status": FederatedRolloutPlanStatus.ACTIVE, "updated_at": _now()})
        await store.update(updated)
        assert (await store.get("frp_a")).status == FederatedRolloutPlanStatus.ACTIVE

    async def test_update_missing_plan_raises_key_error(self) -> None:
        store = InMemoryFederatedRolloutPlanStore()

        with pytest.raises(KeyError, match="frp_missing"):
            await store.update(_plan("frp_missing"))


@pytest.mark.asyncio
class TestSQLitePlanStore:
    async def test_sqlite_plan_persists_across_instances(self, tmp_path) -> None:
        db_path = tmp_path / "plans.db"
        store = SQLiteFederatedRolloutPlanStore(str(db_path))
        await store.create(_plan())
        store.close()

        reopened = SQLiteFederatedRolloutPlanStore(str(db_path))
        loaded = await reopened.get("frp_a")

        assert loaded is not None
        assert loaded.federation_id == "frp_a"
        assert loaded.rollout_template_steps[0].step_id == "step_activate"
        assert loaded.target_ids == ["frt_a"]
        reopened.close()

    async def test_sqlite_update_replaces_json_fields(self, tmp_path) -> None:
        store = SQLiteFederatedRolloutPlanStore(str(tmp_path / "plans.db"))
        plan = await store.create(_plan())
        updated = plan.model_copy(update={
            "target_ids": ["frt_a", "frt_b"],
            "status": FederatedRolloutPlanStatus.ACTIVE,
            "updated_at": _now(),
        })

        await store.update(updated)
        loaded = await store.get("frp_a")

        assert loaded is not None
        assert loaded.target_ids == ["frt_a", "frt_b"]
        assert loaded.status == FederatedRolloutPlanStatus.ACTIVE
        store.close()


class TestFactoriesAndProtocols:
    def test_target_factory_memory_and_sqlite(self, tmp_path) -> None:
        assert isinstance(create_federated_rollout_target_store("memory"), FederatedRolloutTargetStore)
        sqlite_store = create_federated_rollout_target_store("sqlite", str(tmp_path / "targets.db"))
        assert isinstance(sqlite_store, SQLiteFederatedRolloutTargetStore)
        sqlite_store.close()

    def test_plan_factory_memory_and_sqlite(self, tmp_path) -> None:
        assert isinstance(create_federated_rollout_plan_store("memory"), FederatedRolloutPlanStore)
        sqlite_store = create_federated_rollout_plan_store("sqlite", str(tmp_path / "plans.db"))
        assert isinstance(sqlite_store, SQLiteFederatedRolloutPlanStore)
        sqlite_store.close()

    def test_factory_rejects_unknown_type(self) -> None:
        with pytest.raises(ValueError, match="Unknown"):
            create_federated_rollout_target_store("redis")
        with pytest.raises(ValueError, match="Unknown"):
            create_federated_rollout_plan_store("redis")
