"""Tests for RolloutFederationService approval integration.

Phase 48 Task 5: Verifies that approval_service checks are correctly
integrated into federation service methods.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_app.core.context import RunContext
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
)
from agent_app.governance.policy_rollout_federation_approval import (
    FederationApprovalRequest,
    FederationApprovalStatus,
)
from agent_app.governance.policy_rbac import PolicyReleasePermission
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


def _step(environment: str = "prod", ring_name: str = "canary") -> RolloutStep:
    return RolloutStep(
        step_id="step_activate",
        step_type=RolloutStepType.ACTIVATE,
        environment=environment,
        ring_name=ring_name,
    )


def _make_approval_request(
    federation_id: str = "frp_test",
    action: str = "federation.plan.start",
    status: FederationApprovalStatus = FederationApprovalStatus.PENDING,
) -> FederationApprovalRequest:
    return FederationApprovalRequest(
        approval_id="fap_test12345678",
        federation_id=federation_id,
        action=action,
        requested_by="system",
        required_approvers=["approver_a"],
        status=status,
        created_at=datetime.now(timezone.utc),
    )


def _service_with_approval(
    approval_service: MagicMock | None = None,
) -> tuple[
    RolloutFederationService,
    InMemoryFederatedRolloutTargetStore,
    InMemoryFederatedRolloutPlanStore,
    InMemoryRolloutPlanStore,
    MagicMock,
]:
    """Create a service with an optional approval_service."""
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
        approval_service=approval_service,
    )
    return service, target_store, federation_store, rollout_store, rollout_service


def _service_without_approval() -> tuple[
    RolloutFederationService,
    InMemoryFederatedRolloutTargetStore,
    InMemoryFederatedRolloutPlanStore,
    InMemoryRolloutPlanStore,
    MagicMock,
]:
    """Create a service without approval_service (default None)."""
    return _service_with_approval(approval_service=None)


async def _create_started_plan(
    service: RolloutFederationService,
    target_store: InMemoryFederatedRolloutTargetStore,
) -> str:
    """Helper: create a target, plan, and start it. Returns federation_id."""
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
    await service.start_federated_plan(
        plan.federation_id,
        actor_id="release_manager",
        context=_context(PolicyReleasePermission.FEDERATION_PLAN_START.value),
    )
    return plan.federation_id


async def _create_draft_plan(
    service: RolloutFederationService,
    target_store: InMemoryFederatedRolloutTargetStore,
) -> str:
    """Helper: create a target and plan (DRAFT). Returns federation_id."""
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
    return plan.federation_id


# ======================================================================
# start_federated_plan approval tests
# ======================================================================


@pytest.mark.asyncio
class TestStartFederatedPlanApproval:
    """Approval integration tests for start_federated_plan."""

    async def test_no_approval_service_proceeds_normally(self) -> None:
        """When approval_service is None, start_plan proceeds normally."""
        service, target_store, federation_store, _, _ = _service_without_approval()
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

    async def test_approval_not_required_proceeds_normally(self) -> None:
        """When approval is not required for the action, start_plan proceeds."""
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=False)
        service, target_store, _, _, _ = _service_with_approval(approval_service)

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

    async def test_approval_required_no_request_creates_request_and_returns_required(self) -> None:
        """When approval is required and no request exists, creates one and returns approval_required."""
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=None)
        approval_service.create_approval_request = AsyncMock(
            return_value=_make_approval_request(action="federation.plan.start"),
        )
        service, target_store, _, _, _ = _service_with_approval(approval_service)

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
        result = await service.start_federated_plan(
            plan.federation_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_START.value),
        )
        assert isinstance(result, dict)
        assert result["status"] == "approval_required"
        assert result["action"] == "federation.plan.start"
        approval_service.create_approval_request.assert_awaited_once()

    async def test_approval_required_pending_returns_required(self) -> None:
        """When approval is required and request is PENDING, returns approval_required."""
        pending_request = _make_approval_request(
            action="federation.plan.start",
            status=FederationApprovalStatus.PENDING,
        )
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=pending_request)
        service, target_store, _, _, _ = _service_with_approval(approval_service)

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
        result = await service.start_federated_plan(
            plan.federation_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_START.value),
        )
        assert isinstance(result, dict)
        assert result["status"] == "approval_required"
        assert result["approval_id"] == pending_request.approval_id

    async def test_approval_required_approved_proceeds(self) -> None:
        """When approval is required and request is APPROVED, start_plan proceeds."""
        approved_request = _make_approval_request(
            action="federation.plan.start",
            status=FederationApprovalStatus.APPROVED,
        )
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=approved_request)
        service, target_store, _, _, _ = _service_with_approval(approval_service)

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

    async def test_approval_required_rejected_returns_required(self) -> None:
        """When approval is required and request is REJECTED, returns approval_required."""
        rejected_request = _make_approval_request(
            action="federation.plan.start",
            status=FederationApprovalStatus.REJECTED,
        )
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=rejected_request)
        service, target_store, _, _, _ = _service_with_approval(approval_service)

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
        result = await service.start_federated_plan(
            plan.federation_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_START.value),
        )
        assert isinstance(result, dict)
        assert result["status"] == "approval_required"
        assert result["approval_id"] == rejected_request.approval_id


# ======================================================================
# run_all_available approval tests
# ======================================================================


@pytest.mark.asyncio
class TestRunAllAvailableApproval:
    """Approval integration tests for run_all_available."""

    async def test_no_approval_service_proceeds_normally(self) -> None:
        """When approval_service is None, run_all_available proceeds normally."""
        service, target_store, _, _, rollout_service = _service_without_approval()
        child = RolloutPlan(
            rollout_id="ro_child",
            name="child",
            bundle_id="pb_123",
            status=RolloutPlanStatus.COMPLETED,
            steps=[_step()],
            created_by="release_manager",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        rollout_service.create_plan.return_value = child.model_copy(update={"status": RolloutPlanStatus.DRAFT})
        rollout_service.start_plan.return_value = child.model_copy(update={"status": RolloutPlanStatus.ACTIVE})
        rollout_service.run_all_available.return_value = child

        fed_id = await _create_started_plan(service, target_store)
        result = await service.run_all_available(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value),
        )
        assert result.status == FederatedRolloutPlanStatus.COMPLETED

    async def test_approval_not_required_proceeds_normally(self) -> None:
        """When approval is not required, run_all_available proceeds."""
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=False)
        service, target_store, _, _, rollout_service = _service_with_approval(approval_service)
        child = RolloutPlan(
            rollout_id="ro_child",
            name="child",
            bundle_id="pb_123",
            status=RolloutPlanStatus.COMPLETED,
            steps=[_step()],
            created_by="release_manager",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        rollout_service.create_plan.return_value = child.model_copy(update={"status": RolloutPlanStatus.DRAFT})
        rollout_service.start_plan.return_value = child.model_copy(update={"status": RolloutPlanStatus.ACTIVE})
        rollout_service.run_all_available.return_value = child

        fed_id = await _create_started_plan(service, target_store)
        result = await service.run_all_available(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value),
        )
        assert result.status == FederatedRolloutPlanStatus.COMPLETED

    async def test_approval_required_no_request_creates_and_returns_required(self) -> None:
        """When approval required and no request, creates one and returns approval_required."""
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=None)
        approval_service.create_approval_request = AsyncMock(
            return_value=_make_approval_request(action="federation.plan.run_all"),
        )
        service, target_store, _, _, _ = _service_with_approval(approval_service)

        fed_id = await _create_started_plan(service, target_store)
        result = await service.run_all_available(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value),
        )
        assert isinstance(result, dict)
        assert result["status"] == "approval_required"
        assert result["action"] == "federation.plan.run_all"

    async def test_approval_required_pending_returns_required(self) -> None:
        """When approval required and request is PENDING, returns approval_required."""
        pending_request = _make_approval_request(
            action="federation.plan.run_all",
            status=FederationApprovalStatus.PENDING,
        )
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=pending_request)
        service, target_store, _, _, _ = _service_with_approval(approval_service)

        fed_id = await _create_started_plan(service, target_store)
        result = await service.run_all_available(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value),
        )
        assert isinstance(result, dict)
        assert result["status"] == "approval_required"
        assert result["approval_id"] == pending_request.approval_id

    async def test_approval_required_approved_proceeds(self) -> None:
        """When approval required and request is APPROVED, run_all_available proceeds."""
        approved_request = _make_approval_request(
            action="federation.plan.run_all",
            status=FederationApprovalStatus.APPROVED,
        )
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=approved_request)
        service, target_store, _, _, rollout_service = _service_with_approval(approval_service)
        child = RolloutPlan(
            rollout_id="ro_child",
            name="child",
            bundle_id="pb_123",
            status=RolloutPlanStatus.COMPLETED,
            steps=[_step()],
            created_by="release_manager",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        rollout_service.create_plan.return_value = child.model_copy(update={"status": RolloutPlanStatus.DRAFT})
        rollout_service.start_plan.return_value = child.model_copy(update={"status": RolloutPlanStatus.ACTIVE})
        rollout_service.run_all_available.return_value = child

        fed_id = await _create_started_plan(service, target_store)
        result = await service.run_all_available(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value),
        )
        assert result.status == FederatedRolloutPlanStatus.COMPLETED

    async def test_approval_required_rejected_returns_required(self) -> None:
        """When approval required and request is REJECTED, returns approval_required."""
        rejected_request = _make_approval_request(
            action="federation.plan.run_all",
            status=FederationApprovalStatus.REJECTED,
        )
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=rejected_request)
        service, target_store, _, _, _ = _service_with_approval(approval_service)

        fed_id = await _create_started_plan(service, target_store)
        result = await service.run_all_available(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value),
        )
        assert isinstance(result, dict)
        assert result["status"] == "approval_required"
        assert result["approval_id"] == rejected_request.approval_id


# ======================================================================
# cancel_federated_plan approval tests
# ======================================================================


@pytest.mark.asyncio
class TestCancelFederatedPlanApproval:
    """Approval integration tests for cancel_federated_plan."""

    async def test_no_approval_service_proceeds_normally(self) -> None:
        """When approval_service is None, cancel proceeds normally."""
        service, target_store, federation_store, _, _ = _service_without_approval()
        fed_id = await _create_started_plan(service, target_store)
        cancelled = await service.cancel_federated_plan(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CANCEL.value),
        )
        assert cancelled.status == FederatedRolloutPlanStatus.CANCELLED

    async def test_approval_not_required_proceeds_normally(self) -> None:
        """When approval is not required, cancel proceeds normally."""
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=False)
        service, target_store, _, _, _ = _service_with_approval(approval_service)

        fed_id = await _create_started_plan(service, target_store)
        cancelled = await service.cancel_federated_plan(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CANCEL.value),
        )
        assert cancelled.status == FederatedRolloutPlanStatus.CANCELLED

    async def test_approval_required_no_request_creates_and_returns_required(self) -> None:
        """When approval required and no request, creates one and returns approval_required."""
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=None)
        approval_service.create_approval_request = AsyncMock(
            return_value=_make_approval_request(action="federation.plan.cancel"),
        )
        service, target_store, _, _, _ = _service_with_approval(approval_service)

        fed_id = await _create_started_plan(service, target_store)
        result = await service.cancel_federated_plan(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CANCEL.value),
        )
        assert isinstance(result, dict)
        assert result["status"] == "approval_required"
        assert result["action"] == "federation.plan.cancel"

    async def test_approval_required_pending_returns_required(self) -> None:
        """When approval required and request is PENDING, returns approval_required."""
        pending_request = _make_approval_request(
            action="federation.plan.cancel",
            status=FederationApprovalStatus.PENDING,
        )
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=pending_request)
        service, target_store, _, _, _ = _service_with_approval(approval_service)

        fed_id = await _create_started_plan(service, target_store)
        result = await service.cancel_federated_plan(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CANCEL.value),
        )
        assert isinstance(result, dict)
        assert result["status"] == "approval_required"
        assert result["approval_id"] == pending_request.approval_id

    async def test_approval_required_approved_proceeds(self) -> None:
        """When approval required and request is APPROVED, cancel proceeds."""
        approved_request = _make_approval_request(
            action="federation.plan.cancel",
            status=FederationApprovalStatus.APPROVED,
        )
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=approved_request)
        service, target_store, _, _, _ = _service_with_approval(approval_service)

        fed_id = await _create_started_plan(service, target_store)
        cancelled = await service.cancel_federated_plan(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CANCEL.value),
        )
        assert cancelled.status == FederatedRolloutPlanStatus.CANCELLED

    async def test_approval_required_rejected_returns_required(self) -> None:
        """When approval required and request is REJECTED, returns approval_required."""
        rejected_request = _make_approval_request(
            action="federation.plan.cancel",
            status=FederationApprovalStatus.REJECTED,
        )
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=rejected_request)
        service, target_store, _, _, _ = _service_with_approval(approval_service)

        fed_id = await _create_started_plan(service, target_store)
        result = await service.cancel_federated_plan(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_CANCEL.value),
        )
        assert isinstance(result, dict)
        assert result["status"] == "approval_required"
        assert result["approval_id"] == rejected_request.approval_id


# ======================================================================
# run_next_target approval tests
# ======================================================================


@pytest.mark.asyncio
class TestRunNextTargetApproval:
    """Approval integration tests for run_next_target."""

    async def test_no_approval_service_proceeds_normally(self) -> None:
        """When approval_service is None, run_next_target proceeds normally."""
        service, target_store, _, _, rollout_service = _service_without_approval()
        child = RolloutPlan(
            rollout_id="ro_child",
            name="child",
            bundle_id="pb_123",
            status=RolloutPlanStatus.COMPLETED,
            steps=[_step()],
            created_by="release_manager",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        rollout_service.create_plan.return_value = child.model_copy(update={"status": RolloutPlanStatus.DRAFT})
        rollout_service.start_plan.return_value = child.model_copy(update={"status": RolloutPlanStatus.ACTIVE})
        rollout_service.run_all_available.return_value = child

        fed_id = await _create_started_plan(service, target_store)
        result = await service.run_next_target(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value),
        )
        assert result.executions[0].status == FederatedRolloutTargetExecutionStatus.SUCCEEDED

    async def test_approval_required_no_request_creates_and_returns_required(self) -> None:
        """When approval required and no request, creates one and returns approval_required."""
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=None)
        approval_service.create_approval_request = AsyncMock(
            return_value=_make_approval_request(action="federation.plan.run_next"),
        )
        service, target_store, _, _, _ = _service_with_approval(approval_service)

        fed_id = await _create_started_plan(service, target_store)
        result = await service.run_next_target(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value),
        )
        assert isinstance(result, dict)
        assert result["status"] == "approval_required"
        assert result["action"] == "federation.plan.run_next"

    async def test_approval_required_approved_proceeds(self) -> None:
        """When approval required and request is APPROVED, run_next_target proceeds."""
        approved_request = _make_approval_request(
            action="federation.plan.run_next",
            status=FederationApprovalStatus.APPROVED,
        )
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=approved_request)
        service, target_store, _, _, rollout_service = _service_with_approval(approval_service)
        child = RolloutPlan(
            rollout_id="ro_child",
            name="child",
            bundle_id="pb_123",
            status=RolloutPlanStatus.COMPLETED,
            steps=[_step()],
            created_by="release_manager",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        rollout_service.create_plan.return_value = child.model_copy(update={"status": RolloutPlanStatus.DRAFT})
        rollout_service.start_plan.return_value = child.model_copy(update={"status": RolloutPlanStatus.ACTIVE})
        rollout_service.run_all_available.return_value = child

        fed_id = await _create_started_plan(service, target_store)
        result = await service.run_next_target(
            fed_id,
            actor_id="release_manager",
            context=_context(PolicyReleasePermission.FEDERATION_PLAN_EXECUTE.value),
        )
        assert result.executions[0].status == FederatedRolloutTargetExecutionStatus.SUCCEEDED


# ======================================================================
# _create_approval_result helper test
# ======================================================================


@pytest.mark.asyncio
class TestCreateApprovalResult:
    """Test the _create_approval_result helper method."""

    async def test_result_dict_structure(self) -> None:
        """_create_approval_result returns the expected dict structure."""
        service, _, _, _, _ = _service_without_approval()
        request = _make_approval_request(
            action="federation.plan.start",
        )
        result = service._create_approval_result(request)
        assert result["status"] == "approval_required"
        assert result["approval_id"] == request.approval_id
        assert result["action"] == request.action
        assert result["required_approvers"] == request.required_approvers
        assert "Approval required for" in result["message"]


# ======================================================================
# _check_approval helper tests
# ======================================================================


@pytest.mark.asyncio
class TestCheckApproval:
    """Test the _check_approval private method."""

    async def test_no_approval_service_returns_true(self) -> None:
        """When no approval_service, _check_approval returns True."""
        service, _, _, _, _ = _service_without_approval()
        result = await service._check_approval("frp_test", "federation.plan.start")
        assert result is True

    async def test_approval_not_required_returns_true(self) -> None:
        """When approval is not required for the action, returns True."""
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=False)
        service, _, _, _, _ = _service_with_approval(approval_service)
        result = await service._check_approval("frp_test", "federation.plan.start")
        assert result is True

    async def test_approval_required_no_request_creates_and_returns_false(self) -> None:
        """When approval required and no request, creates one and returns False."""
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(return_value=None)
        approval_service.create_approval_request = AsyncMock(
            return_value=_make_approval_request(),
        )
        service, _, _, _, _ = _service_with_approval(approval_service)
        result = await service._check_approval("frp_test", "federation.plan.start")
        assert result is False
        approval_service.create_approval_request.assert_awaited_once()

    async def test_approval_required_pending_returns_false(self) -> None:
        """When approval required and request is PENDING, returns False."""
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(
            return_value=_make_approval_request(status=FederationApprovalStatus.PENDING),
        )
        service, _, _, _, _ = _service_with_approval(approval_service)
        result = await service._check_approval("frp_test", "federation.plan.start")
        assert result is False

    async def test_approval_required_escalated_returns_false(self) -> None:
        """When approval required and request is ESCALATED, returns False."""
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(
            return_value=_make_approval_request(status=FederationApprovalStatus.ESCALATED),
        )
        service, _, _, _, _ = _service_with_approval(approval_service)
        result = await service._check_approval("frp_test", "federation.plan.start")
        assert result is False

    async def test_approval_required_approved_returns_true(self) -> None:
        """When approval required and request is APPROVED, returns True."""
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(
            return_value=_make_approval_request(status=FederationApprovalStatus.APPROVED),
        )
        service, _, _, _, _ = _service_with_approval(approval_service)
        result = await service._check_approval("frp_test", "federation.plan.start")
        assert result is True

    async def test_approval_required_rejected_returns_false(self) -> None:
        """When approval required and request is REJECTED, returns False."""
        approval_service = MagicMock()
        approval_service.requires_approval = AsyncMock(return_value=True)
        approval_service.check_approval_status = AsyncMock(
            return_value=_make_approval_request(status=FederationApprovalStatus.REJECTED),
        )
        service, _, _, _, _ = _service_with_approval(approval_service)
        result = await service._check_approval("frp_test", "federation.plan.start")
        assert result is False
