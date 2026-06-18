from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepType,
)
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlan,
    FederatedRolloutPlanStatus,
    FederatedRolloutTarget,
    FederatedTargetStatus,
    RolloutConflictSeverity,
    RolloutConflictType,
)
from agent_app.runtime.policy_rollout_conflict_detector import RolloutConflictDetector
from agent_app.runtime.policy_rollout_federation_store import (
    InMemoryFederatedRolloutPlanStore,
    InMemoryFederatedRolloutTargetStore,
)
from agent_app.runtime.policy_rollout_store import InMemoryRolloutPlanStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _target(
    target_id: str,
    environment: str = "prod",
    ring_name: str | None = "canary",
    status: FederatedTargetStatus = FederatedTargetStatus.ENABLED,
) -> FederatedRolloutTarget:
    return FederatedRolloutTarget(
        target_id=target_id,
        name=target_id,
        environment=environment,
        ring_name=ring_name,
        status=status,
        created_at=_now(),
    )


def _step(environment: str = "prod", ring_name: str | None = "canary") -> RolloutStep:
    return RolloutStep(
        step_id="step_activate",
        step_type=RolloutStepType.ACTIVATE,
        environment=environment,
        ring_name=ring_name,
    )


def _federated_plan(
    target_ids: list[str],
    federation_id: str = "frp_new",
    bundle_id: str = "pb_new",
) -> FederatedRolloutPlan:
    return FederatedRolloutPlan(
        federation_id=federation_id,
        name=federation_id,
        bundle_id=bundle_id,
        target_ids=target_ids,
        rollout_template_steps=[_step()],
        created_by="release_manager",
        created_at=_now(),
        updated_at=_now(),
    )


@pytest.mark.asyncio
class TestRolloutConflictDetector:
    async def test_duplicate_target_conflict(self) -> None:
        targets = InMemoryFederatedRolloutTargetStore()
        federations = InMemoryFederatedRolloutPlanStore()
        detector = RolloutConflictDetector(targets, federations)
        plan = FederatedRolloutPlan.model_construct(
            federation_id="frp_dup",
            name="dup",
            bundle_id="pb_123",
            target_ids=["frt_a", "frt_a"],
            rollout_template_steps=[_step()],
            created_by="user",
            created_at=_now(),
            updated_at=_now(),
        )
        conflicts = await detector.detect_conflicts(plan)
        assert [c.conflict_type for c in conflicts] == [RolloutConflictType.DUPLICATE_TARGET]
        assert conflicts[0].severity == RolloutConflictSeverity.ERROR
        assert conflicts[0].target_id == "frt_a"

    async def test_missing_target_conflict(self) -> None:
        detector = RolloutConflictDetector(
            InMemoryFederatedRolloutTargetStore(),
            InMemoryFederatedRolloutPlanStore(),
        )
        conflicts = await detector.detect_conflicts(_federated_plan(["frt_missing"]))
        assert conflicts[0].conflict_type == RolloutConflictType.MISSING_TARGET
        assert conflicts[0].severity == RolloutConflictSeverity.ERROR
        assert conflicts[0].target_id == "frt_missing"

    async def test_disabled_target_conflict(self) -> None:
        target_store = InMemoryFederatedRolloutTargetStore()
        await target_store.create(_target("frt_disabled", status=FederatedTargetStatus.DISABLED))
        detector = RolloutConflictDetector(target_store, InMemoryFederatedRolloutPlanStore())
        conflicts = await detector.detect_conflicts(_federated_plan(["frt_disabled"]))
        assert conflicts[0].conflict_type == RolloutConflictType.DISABLED_TARGET
        assert conflicts[0].severity == RolloutConflictSeverity.ERROR

    async def test_active_federation_same_target_conflict(self) -> None:
        target_store = InMemoryFederatedRolloutTargetStore()
        federation_store = InMemoryFederatedRolloutPlanStore()
        await target_store.create(_target("frt_a"))
        existing = _federated_plan(["frt_a"], federation_id="frp_existing")
        existing = existing.model_copy(update={"status": FederatedRolloutPlanStatus.ACTIVE})
        await federation_store.create(existing)
        detector = RolloutConflictDetector(target_store, federation_store)
        conflicts = await detector.detect_conflicts(
            _federated_plan(["frt_a"], federation_id="frp_new")
        )
        assert conflicts[0].conflict_type == RolloutConflictType.TARGET_ALREADY_ACTIVE
        assert conflicts[0].severity == RolloutConflictSeverity.ERROR
        assert conflicts[0].existing_federation_id == "frp_existing"

    async def test_existing_active_rollout_same_environment_ring_conflict(self) -> None:
        target_store = InMemoryFederatedRolloutTargetStore()
        await target_store.create(_target("frt_a", environment="prod", ring_name="canary"))
        rollout_store = InMemoryRolloutPlanStore()
        await rollout_store.create(
            RolloutPlan(
                rollout_id="ro_existing",
                name="existing",
                bundle_id="pb_existing",
                status=RolloutPlanStatus.ACTIVE,
                steps=[_step("prod", "canary")],
                created_by="user",
                created_at=_now(),
                updated_at=_now(),
            )
        )
        detector = RolloutConflictDetector(
            target_store, InMemoryFederatedRolloutPlanStore(), rollout_store
        )
        conflicts = await detector.detect_conflicts(_federated_plan(["frt_a"], bundle_id="pb_new"))
        assert [c.conflict_type for c in conflicts] == [
            RolloutConflictType.ENVIRONMENT_RING_CONFLICT,
            RolloutConflictType.BUNDLE_CONFLICT,
        ]
        assert conflicts[0].severity == RolloutConflictSeverity.ERROR
        assert conflicts[0].existing_rollout_id == "ro_existing"
        assert conflicts[1].severity == RolloutConflictSeverity.WARNING

    async def test_detector_does_not_mutate_state(self) -> None:
        target_store = InMemoryFederatedRolloutTargetStore()
        federation_store = InMemoryFederatedRolloutPlanStore()
        target = await target_store.create(_target("frt_a"))
        plan = _federated_plan(["frt_a"])
        detector = RolloutConflictDetector(target_store, federation_store)
        await detector.detect_conflicts(plan)
        assert await target_store.get("frt_a") == target
        assert await federation_store.list() == []
        assert plan.status == FederatedRolloutPlanStatus.DRAFT

    async def test_conflicts_are_deterministically_ordered(self) -> None:
        detector = RolloutConflictDetector(
            InMemoryFederatedRolloutTargetStore(),
            InMemoryFederatedRolloutPlanStore(),
        )
        plan = FederatedRolloutPlan.model_construct(
            federation_id="frp_order",
            name="order",
            bundle_id="pb_123",
            target_ids=["frt_b", "frt_a", "frt_b", "frt_a"],
            rollout_template_steps=[_step()],
            created_by="user",
            created_at=_now(),
            updated_at=_now(),
        )
        conflicts = await detector.detect_conflicts(plan)
        assert [(c.conflict_type.value, c.target_id) for c in conflicts] == [
            ("duplicate_target", "frt_a"),
            ("duplicate_target", "frt_b"),
            ("missing_target", "frt_a"),
            ("missing_target", "frt_b"),
        ]
        assert [c.conflict_id for c in conflicts] == [
            "frc_duplicate_target_frt_a",
            "frc_duplicate_target_frt_b",
            "frc_missing_target_frt_a",
            "frc_missing_target_frt_b",
        ]
