"""Tests for RolloutFederationService — federation service create/start lifecycle."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_app.core.context import RunContext
from agent_app.governance.policy_change_event import PolicyChangeEventType
from agent_app.governance.policy_rbac import PolicyReleasePermission
from agent_app.governance.policy_rollout import (
    RolloutPlan,
    RolloutPlanStatus,
    RolloutStep,
    RolloutStepStatus,
    RolloutStepType,
)
from agent_app.governance.policy_rollout_federation import (
    FederatedRolloutPlanStatus,
    FederatedRolloutTargetExecutionStatus,
    FederatedRolloutWave,
    FederationExecutionStrategy,
    RolloutConflict,
    RolloutConflictSeverity,
    RolloutConflictType,
)
from agent_app.runtime.policy_rollout_conflict_detector import RolloutConflictDetector
from agent_app.runtime.policy_rollout_federation_service import RolloutFederationService
from agent_app.runtime.policy_rollout_federation_store import (
    InMemoryFederatedRolloutPlanStore,
    InMemoryFederatedRolloutTargetStore,
)
from agent_app.runtime.policy_rollout_store import InMemoryRolloutPlanStore


def _context(*permissions: str, metadata: dict | None = None) -> RunContext:
    return RunContext(
        run_id="run_test",
        user_id="release_manager",
        tenant_id="tenant_a",
        permissions=list(permissions),
        metadata=metadata or {},
    )


def _step(environment="prod", ring_name="canary") -> RolloutStep:
    return RolloutStep(
        step_id="step_activate",
        step_type=RolloutStepType.ACTIVATE,
        environment=environment,
        ring_name=ring_name,
    )


def _service(
    notification_service=None,
    audit_logger=None,
    event_store=None,
    conflict_detector=None,
):
    target_store = InMemoryFederatedRolloutTargetStore()
    federation_store = InMemoryFederatedRolloutPlanStore()
    rollout_store = InMemoryRolloutPlanStore()
    rollout_service = MagicMock()
    rollout_service.create_plan = AsyncMock()
    rollout_service.start_plan = AsyncMock()
    rollout_service.run_all_available = AsyncMock()
    service = RolloutFederationService(
        target_store=target_store,
        federation_store=federation_store,
        rollout_store=rollout_store,
        rollout_service=rollout_service,
        conflict_detector=conflict_detector,
        notification_service=notification_service,
        audit_logger=audit_logger,
        event_store=event_store,
    )
    return service, target_store, federation_store, rollout_store, rollout_service


@pytest.mark.asyncio
class TestRolloutFederationServiceCreate:
    """Tests for RolloutFederationService create target and create/start plan lifecycle."""

    async def test_create_target_requires_permission(self) -> None:
        service, _, _, _, _ = _service()
        with pytest.raises(PermissionError, match="policy.federation.target.create"):
            await service.create_target(
                name="prod-us-canary",
                environment="prod",
                actor_id="admin",
                context=_context(),
            )

    async def test_create_target_stores_target_and_audits(self) -> None:
        audit_logger = MagicMock()
        audit_logger.log = AsyncMock()
        event_store = MagicMock()
        event_store.append = AsyncMock()
        service, target_store, _, _, _ = _service(
            audit_logger=audit_logger, event_store=event_store,
        )
        target = await service.create_target(
            name="prod-us-canary",
            environment="prod",
            tenant_id="tenant_a",
            ring_name="canary",
            region="us-east",
            labels={"tier": "gold"},
            actor_id="admin",
            context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value),
        )
        assert target.target_id.startswith("frt_")
        assert target.environment == "prod"
        assert await target_store.get(target.target_id) == target
        assert audit_logger.log.await_args.args[0].event_type == "policy.federation.target.created"
        assert event_store.append.await_args.args[0].event_type == PolicyChangeEventType.FEDERATION_TARGET_CREATED

    async def test_create_federated_plan_requires_permission(self) -> None:
        service, target_store, _, _, _ = _service()
        target = await service.create_target(
            name="prod",
            environment="prod",
            context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value),
        )
        with pytest.raises(PermissionError, match="policy.federation.plan.create"):
            await service.create_federated_plan(
                name="global rollout",
                bundle_id="pb_123",
                target_ids=[target.target_id],
                rollout_template_steps=[_step()],
                created_by="release_manager",
                context=_context(),
            )

    async def test_create_federated_plan_creates_executions_and_stores_draft(self) -> None:
        service, target_store, federation_store, _, _ = _service()
        target = await service.create_target(
            name="prod",
            environment="prod",
            context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value),
        )
        plan = await service.create_federated_plan(
            name="global rollout",
            bundle_id="pb_123",
            target_ids=[target.target_id],
            rollout_template_steps=[_step()],
            created_by="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
            reason="release",
        )
        assert plan.federation_id.startswith("frp_")
        assert plan.status == FederatedRolloutPlanStatus.DRAFT
        assert plan.executions[0].execution_id.startswith("fre_")
        assert plan.executions[0].target_id == target.target_id
        assert plan.executions[0].status == FederatedRolloutTargetExecutionStatus.PENDING
        assert await federation_store.get(plan.federation_id) == plan

    async def test_create_plan_fails_on_error_conflicts(self) -> None:
        conflict = RolloutConflict(
            conflict_id="frc_test",
            conflict_type=RolloutConflictType.MISSING_TARGET,
            severity=RolloutConflictSeverity.ERROR,
            target_id="frt_missing",
            message="missing",
        )
        detector = MagicMock()
        detector.detect_conflicts = AsyncMock(return_value=[conflict])
        service, _, _, _, _ = _service(conflict_detector=detector)
        with pytest.raises(ValueError, match="Federated rollout conflicts"):
            await service.create_federated_plan(
                name="bad",
                bundle_id="pb_123",
                target_ids=["frt_missing"],
                rollout_template_steps=[_step()],
                created_by="release_manager",
                context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
            )

    async def test_create_plan_allows_error_conflicts_with_context_override(self) -> None:
        conflict = RolloutConflict(
            conflict_id="frc_test",
            conflict_type=RolloutConflictType.MISSING_TARGET,
            severity=RolloutConflictSeverity.ERROR,
            target_id="frt_missing",
            message="missing",
        )
        detector = MagicMock()
        detector.detect_conflicts = AsyncMock(return_value=[conflict])
        service, _, federation_store, _, _ = _service(conflict_detector=detector)
        plan = await service.create_federated_plan(
            name="override",
            bundle_id="pb_123",
            target_ids=["frt_missing"],
            rollout_template_steps=[_step()],
            created_by="release_manager",
            context=_context(
                PolicyReleasePermission.FEDERATION_PLAN_CREATE.value,
                metadata={"allow_federation_conflict_override": True},
            ),
        )
        assert await federation_store.get(plan.federation_id) == plan

    async def test_start_plan_rechecks_conflicts_and_marks_active(self) -> None:
        service, target_store, federation_store, _, _ = _service()
        target = await service.create_target(
            name="prod",
            environment="prod",
            context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value),
        )
        plan = await service.create_federated_plan(
            name="global rollout",
            bundle_id="pb_123",
            target_ids=[target.target_id],
            rollout_template_steps=[_step()],
            created_by="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
        )
        started = await service.start_federated_plan(
            plan.federation_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_START.value),
        )
        assert started.status == FederatedRolloutPlanStatus.ACTIVE
        assert (await federation_store.get(plan.federation_id)).status == FederatedRolloutPlanStatus.ACTIVE

    async def test_detect_conflicts_by_id_delegates_to_detector(self) -> None:
        service, target_store, federation_store, _, _ = _service()
        target = await service.create_target(
            name="prod",
            environment="prod",
            context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value),
        )
        plan = await service.create_federated_plan(
            name="global rollout",
            bundle_id="pb_123",
            target_ids=[target.target_id],
            rollout_template_steps=[_step()],
            created_by="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value),
        )
        conflicts = await service.detect_conflicts(plan.federation_id)
        assert conflicts == []


@pytest.mark.asyncio
class TestRolloutFederationServiceExecution:
    async def test_run_next_creates_child_rollout_and_marks_execution_succeeded(self) -> None:
        service, target_store, federation_store, _, rollout_service = _service()
        target = await service.create_target(name="prod", environment="prod", ring_name="canary", context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value))
        child_created = RolloutPlan(rollout_id="ro_child", name="global rollout / prod", bundle_id="pb_123", status=RolloutPlanStatus.DRAFT, steps=[_step("prod", "canary")], created_by="release_manager", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        child_done = child_created.model_copy(update={"status": RolloutPlanStatus.COMPLETED, "steps": [child_created.steps[0].model_copy(update={"status": RolloutStepStatus.SUCCEEDED})]})
        rollout_service.create_plan.return_value = child_created
        rollout_service.start_plan.return_value = child_created.model_copy(update={"status": RolloutPlanStatus.ACTIVE})
        rollout_service.run_all_available.return_value = child_done
        plan = await service.create_federated_plan(name="global rollout", bundle_id="pb_123", target_ids=[target.target_id], rollout_template_steps=[_step()], created_by="release_manager", context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value))
        await service.start_federated_plan(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_START.value))
        updated = await service.run_next_target(plan.federation_id, actor_id="release_manager", context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value))
        assert updated.executions[0].rollout_id == "ro_child"
        assert updated.executions[0].status == FederatedRolloutTargetExecutionStatus.SUCCEEDED
        assert updated.status == FederatedRolloutPlanStatus.COMPLETED
        created_steps = rollout_service.create_plan.await_args.kwargs["steps"]
        assert created_steps[0].environment == "prod"
        assert created_steps[0].ring_name == "canary"
        assert created_steps[0].step_id.endswith(target.target_id[-6:])

    async def test_run_next_marks_blocked_child_rollout_blocked(self) -> None:
        service, target_store, federation_store, _, rollout_service = _service()
        target = await service.create_target(name="prod", environment="prod", context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value))
        child = RolloutPlan(rollout_id="ro_blocked", name="blocked", bundle_id="pb_123", status=RolloutPlanStatus.ACTIVE, steps=[_step().model_copy(update={"status": RolloutStepStatus.BLOCKED, "error": {"message": "gate blocked"}})], created_by="release_manager", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        rollout_service.create_plan.return_value = child.model_copy(update={"status": RolloutPlanStatus.DRAFT})
        rollout_service.start_plan.return_value = child
        rollout_service.run_all_available.return_value = child
        plan = await service.create_federated_plan(name="global rollout", bundle_id="pb_123", target_ids=[target.target_id], rollout_template_steps=[_step()], created_by="release_manager", context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value))
        await service.start_federated_plan(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_START.value))
        updated = await service.run_next_target(plan.federation_id, actor_id="release_manager", context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value))
        assert updated.executions[0].status == FederatedRolloutTargetExecutionStatus.BLOCKED
        assert updated.status == FederatedRolloutPlanStatus.BLOCKED
        assert updated.executions[0].error == {"message": "gate blocked"}

    async def test_run_next_failed_child_marks_plan_failed_and_notifies(self) -> None:
        notification_service = MagicMock()
        notification_service.notify = AsyncMock()
        service, target_store, _, _, rollout_service = _service(notification_service=notification_service)
        target = await service.create_target(name="prod", environment="prod", context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value))
        failed_child = RolloutPlan(rollout_id="ro_failed", name="failed", bundle_id="pb_123", status=RolloutPlanStatus.FAILED, steps=[_step().model_copy(update={"status": RolloutStepStatus.FAILED, "error": {"message": "boom"}})], created_by="release_manager", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        rollout_service.create_plan.return_value = failed_child.model_copy(update={"status": RolloutPlanStatus.DRAFT})
        rollout_service.start_plan.return_value = failed_child.model_copy(update={"status": RolloutPlanStatus.ACTIVE})
        rollout_service.run_all_available.return_value = failed_child
        plan = await service.create_federated_plan(name="global rollout", bundle_id="pb_123", target_ids=[target.target_id], rollout_template_steps=[_step()], created_by="release_manager", context=_context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value))
        await service.start_federated_plan(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_START.value))
        updated = await service.run_next_target(plan.federation_id, actor_id="release_manager", context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value))
        assert updated.status == FederatedRolloutPlanStatus.FAILED
        assert updated.executions[0].status == FederatedRolloutTargetExecutionStatus.FAILED
        assert notification_service.notify.await_count == 1

    async def test_run_all_available_completes_sequential_plan(self) -> None:
        service, target_store, _, _, rollout_service = _service()
        t1 = await service.create_target("prod-a", "prod", ring_name="canary", context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value))
        t2 = await service.create_target("prod-b", "prod", ring_name="stable", context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value))
        created = RolloutPlan(rollout_id="ro_child", name="child", bundle_id="pb_123", status=RolloutPlanStatus.DRAFT, steps=[_step()], created_by="release_manager", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        done = created.model_copy(update={"status": RolloutPlanStatus.COMPLETED})
        rollout_service.create_plan.side_effect = [created.model_copy(update={"rollout_id": "ro_1"}), created.model_copy(update={"rollout_id": "ro_2"})]
        rollout_service.start_plan.side_effect = [done.model_copy(update={"rollout_id": "ro_1"}), done.model_copy(update={"rollout_id": "ro_2"})]
        rollout_service.run_all_available.side_effect = [done.model_copy(update={"rollout_id": "ro_1"}), done.model_copy(update={"rollout_id": "ro_2"})]
        plan = await service.create_federated_plan("global rollout", "pb_123", [t1.target_id, t2.target_id], [_step()], "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value))
        await service.start_federated_plan(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_START.value))
        updated = await service.run_all_available(plan.federation_id, actor_id="release_manager", context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value))
        assert [e.status for e in updated.executions] == [FederatedRolloutTargetExecutionStatus.SUCCEEDED, FederatedRolloutTargetExecutionStatus.SUCCEEDED]
        assert updated.status == FederatedRolloutPlanStatus.COMPLETED

    async def test_wave_strategy_advances_first_wave_before_second_wave(self) -> None:
        service, target_store, _, _, rollout_service = _service()
        t1 = await service.create_target("wave1", "prod", ring_name="canary", context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value))
        t2 = await service.create_target("wave2", "prod", ring_name="stable", context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value))
        child = RolloutPlan(rollout_id="ro_child", name="child", bundle_id="pb_123", status=RolloutPlanStatus.COMPLETED, steps=[_step()], created_by="release_manager", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc))
        rollout_service.create_plan.side_effect = [child.model_copy(update={"rollout_id": "ro_1"}), child.model_copy(update={"rollout_id": "ro_2"})]
        rollout_service.start_plan.side_effect = [child.model_copy(update={"rollout_id": "ro_1"}), child.model_copy(update={"rollout_id": "ro_2"})]
        rollout_service.run_all_available.side_effect = [child.model_copy(update={"rollout_id": "ro_1"}), child.model_copy(update={"rollout_id": "ro_2"})]
        plan = await service.create_federated_plan("wave rollout", "pb_123", [t1.target_id, t2.target_id], [_step()], "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value), strategy=FederationExecutionStrategy.WAVE, waves=[FederatedRolloutWave(wave_id="frw_one", target_ids=[t1.target_id]), FederatedRolloutWave(wave_id="frw_two", target_ids=[t2.target_id])])
        await service.start_federated_plan(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_START.value))
        first = await service.run_next_target(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value))
        second = await service.run_next_target(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value))
        assert first.executions[0].target_id == t1.target_id
        assert first.executions[0].status == FederatedRolloutTargetExecutionStatus.SUCCEEDED
        assert first.executions[1].status == FederatedRolloutTargetExecutionStatus.PENDING
        assert second.executions[1].target_id == t2.target_id
        assert second.status == FederatedRolloutPlanStatus.COMPLETED

    async def test_cancel_marks_plan_and_pending_executions_cancelled(self) -> None:
        service, target_store, federation_store, _, _ = _service()
        target = await service.create_target("prod", "prod", context=_context(PolicyReleasePermission.FEDERATION_TARGET_CREATE.value))
        plan = await service.create_federated_plan("global rollout", "pb_123", [target.target_id], [_step()], "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_CREATE.value))
        await service.start_federated_plan(plan.federation_id, "release_manager", _context(PolicyReleasePermission.FEDERATION_PLAN_START.value))
        cancelled = await service.cancel_federated_plan(plan.federation_id, actor_id="release_manager", context=_context(PolicyReleasePermission.FEDERATION_PLAN_CANCEL.value), reason="stop release")
        assert cancelled.status == FederatedRolloutPlanStatus.CANCELLED
        assert cancelled.executions[0].status == FederatedRolloutTargetExecutionStatus.CANCELLED
        assert (await federation_store.get(plan.federation_id)).status == FederatedRolloutPlanStatus.CANCELLED
